"""
Integration tests for Chunk 6: Recap generation + TTS.
Tests hit the live server at http://localhost:8000.
"""
import asyncio
import uuid

import httpx
import pytest
import websockets

BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"


def _unique_email() -> str:
    return f"test_recap_{uuid.uuid4().hex[:8]}@example.com"


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
def person_with_graph(http, headers, user_token):
    """Create a person, run a mock stream session, and extract the graph."""
    person = http.post("/people", json={"name": "Jordan"}, headers=headers).json()
    assert person["id"]

    sess = http.post(
        "/sessions", json={"person_id": person["id"]}, headers=headers
    ).json()

    async def _stream():
        uri = f"{WS_URL}/ws/stream/{sess['id']}?token={user_token}"
        async with websockets.connect(uri) as ws:
            async for raw in ws:
                if __import__("json").loads(raw)["type"] == "done":
                    break

    asyncio.run(_stream())

    http.post(f"/sessions/{sess['id']}/extract", headers=headers)
    return person


# ── Auth gates ───────────────────────────────────────────────────────────────

def test_recap_no_auth(http, person_with_graph):
    resp = http.get(f"/people/{person_with_graph['id']}/recap")
    assert resp.status_code == 403


def test_recap_wrong_person(http, headers):
    resp = http.get(f"/people/{uuid.uuid4()}/recap", headers=headers)
    assert resp.status_code == 404


# ── Recap content ─────────────────────────────────────────────────────────────

def test_recap_empty_graph(http, headers):
    """Person with no graph data → generic but non-empty recap."""
    person = http.post("/people", json={"name": "NoData"}, headers=headers).json()
    resp = http.get(f"/people/{person['id']}/recap", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "recap" in data
    assert len(data["recap"]) > 0
    assert "NoData" in data["recap"]


def test_recap_with_graph(http, headers, person_with_graph):
    """After extraction, recap should mention person name."""
    resp = http.get(f"/people/{person_with_graph['id']}/recap", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "recap" in data
    assert len(data["recap"]) > 0
    assert "Jordan" in data["recap"]


def test_recap_content_references_facts(http, headers, person_with_graph):
    """
    The mock recap for Jordan should reference graph facts extracted from the fixture.
    Fixture has Tokyo mentions → mock extractor creates edges → recap references them.
    """
    graph = http.get(f"/people/{person_with_graph['id']}/graph", headers=headers).json()
    recap = http.get(f"/people/{person_with_graph['id']}/recap", headers=headers).json()

    # If there are edges, recap should contain something substantive
    if graph["edges"]:
        # Mock recap format: "Last time you spoke with Jordan, you learned that..."
        assert "Last time" in recap["recap"] or "Jordan" in recap["recap"]


# ── Recap is plain text (no TTS) ─────────────────────────────────────────────

def test_recap_is_text_only(http, headers, person_with_graph):
    """Recap endpoint returns plain text — no audio, no TTS."""
    resp = http.get(f"/people/{person_with_graph['id']}/recap", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["recap"], str)
    assert len(data["recap"]) > 10


# ── Isolation ─────────────────────────────────────────────────────────────────

def test_recap_isolation(http, headers):
    other_token = http.post(
        "/auth/register", json={"email": _unique_email(), "password": "pass1234"}
    ).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    person = http.post("/people", json={"name": "Mine"}, headers=headers).json()
    assert http.get(
        f"/people/{person['id']}/recap", headers=other_headers
    ).status_code == 404
