"""
Integration tests for Chunk 5: Knowledge graph extraction.

Flow for extraction tests:
  1. Register user, create person
  2. Create session with person_id
  3. Run mock WebSocket stream → 8 segments written
  4. POST /sessions/{id}/extract → graph extraction runs
  5. GET /people/{id}/graph → verify nodes and edges

All tests hit the live server at http://localhost:8000.
"""
import asyncio
import uuid

import httpx
import pytest
import websockets

BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"


def _unique_email() -> str:
    return f"test_graph_{uuid.uuid4().hex[:8]}@example.com"


@pytest.fixture(scope="module")
def http():
    with httpx.Client(base_url=BASE_URL, timeout=15) as client:
        yield client


@pytest.fixture(scope="module")
def user_token(http):
    resp = http.post("/auth/register", json={"email": _unique_email(), "password": "pass1234"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def headers(user_token):
    return {"Authorization": f"Bearer {user_token}"}


@pytest.fixture(scope="module")
def person(http, headers):
    resp = http.post("/people", json={"name": "Taylor"}, headers=headers)
    assert resp.status_code == 201
    return resp.json()


# ── GET /people/{id}/graph auth gate ─────────────────────────────────────────

def test_graph_no_auth(http, person):
    resp = http.get(f"/people/{person['id']}/graph")
    assert resp.status_code == 403


def test_graph_wrong_person(http, headers):
    resp = http.get(f"/people/{uuid.uuid4()}/graph", headers=headers)
    assert resp.status_code == 404


# ── Empty graph before extraction ─────────────────────────────────────────────

def test_graph_empty_before_extraction(http, headers, person):
    resp = http.get(f"/people/{person['id']}/graph", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Taylor"
    assert data["edges"] == []


# ── Session creation with person_id ──────────────────────────────────────────

def test_create_session_with_person(http, headers, person):
    resp = http.post("/sessions", json={"person_id": person["id"]}, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["person_id"] == person["id"]


def test_create_session_no_body_still_works(http, headers):
    resp = http.post("/sessions", headers=headers)
    assert resp.status_code == 201
    assert resp.json()["person_id"] is None


def test_create_session_wrong_person(http, headers):
    resp = http.post("/sessions", json={"person_id": str(uuid.uuid4())}, headers=headers)
    assert resp.status_code == 404


# ── Extract endpoint guards ────────────────────────────────────────────────────

def test_extract_no_person_id(http, headers):
    """Session without person_id → 422."""
    sess = http.post("/sessions", headers=headers).json()
    resp = http.post(f"/sessions/{sess['id']}/extract", headers=headers)
    assert resp.status_code == 422


# ── Full pipeline: stream → extract → graph ──────────────────────────────────

def _run_ws_stream(session_id: str, token: str) -> list[dict]:
    """Connect to WebSocket, collect all segment messages, wait for 'done'."""
    async def _inner():
        uri = f"{WS_URL}/ws/stream/{session_id}?token={token}"
        msgs = []
        async with websockets.connect(uri) as ws:
            async for raw in ws:
                msg = __import__("json").loads(raw)
                if msg["type"] == "done":
                    break
                msgs.append(msg)
        return msgs

    return asyncio.run(_inner())


def test_extract_pipeline(http, headers, user_token, person):
    """
    Full pipeline:
      - create session linked to person
      - run mock stream (8 segments)
      - call extract endpoint
      - verify graph has nodes and edges
    """
    # Create session with person_id
    sess = http.post(
        "/sessions", json={"person_id": person["id"]}, headers=headers
    ).json()
    session_id = sess["id"]

    # Stream mock segments (ends the session internally via WebSocket handler)
    msgs = _run_ws_stream(session_id, user_token)
    assert len(msgs) == 8

    # Extract graph from segments
    resp = http.post(f"/sessions/{session_id}/extract", headers=headers)
    assert resp.status_code == 200
    extract_data = resp.json()
    assert extract_data["triples_stored"] >= 0  # mock may return 0 for some segments

    # Graph endpoint returns the entity node
    graph = http.get(f"/people/{person['id']}/graph", headers=headers).json()
    assert graph["name"] == "Taylor"
    # nodes should include at least the person node
    assert len(graph["nodes"]) >= 1
    assert graph["nodes"][0]["name"] == "Taylor"


def test_graph_has_edges_after_extraction(http, headers, person):
    """
    After extraction, the Tokyo-related utterances from the fixture should have
    produced at least one edge (mock extractor detects 'Tokyo' in speaker B's text).
    """
    graph = http.get(f"/people/{person['id']}/graph", headers=headers).json()
    # Fixture has: "Just got back from Tokyo" and "thinking of moving there next year"
    # Mock extractor picks up 'Tokyo' in those utterances for speaker_label B
    assert len(graph["edges"]) > 0
    predicates = [e["predicate"] for e in graph["edges"]]
    assert any(p in predicates for p in ["visited", "is_planning_to_move_to", "mentioned"])


# ── Dedup: extracting same session twice doesn't duplicate edges ──────────────

def test_extract_idempotent(http, headers, person):
    """Calling extract twice on the same session shouldn't double the edges."""
    sess = http.post(
        "/sessions", json={"person_id": person["id"]}, headers=headers
    ).json()
    _run_ws_stream(sess["id"], headers["Authorization"].split()[1])

    http.post(f"/sessions/{sess['id']}/extract", headers=headers)
    count_before = len(
        http.get(f"/people/{person['id']}/graph", headers=headers).json()["edges"]
    )

    http.post(f"/sessions/{sess['id']}/extract", headers=headers)
    count_after = len(
        http.get(f"/people/{person['id']}/graph", headers=headers).json()["edges"]
    )

    assert count_after == count_before


# ── Isolation: another user cannot extract or view ───────────────────────────

def test_graph_isolation(http, headers):
    other_token = http.post(
        "/auth/register", json={"email": _unique_email(), "password": "pass1234"}
    ).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    person_a = http.post("/people", json={"name": "Private"}, headers=headers).json()
    assert http.get(f"/people/{person_a['id']}/graph", headers=other_headers).status_code == 404
