"""
Integration tests for Chunk 3: AssemblyAI streaming + session management.
Requires MOCK_ASSEMBLYAI=true (default in dev docker-compose).
Tests hit the live server at http://localhost:8000.
"""

import asyncio
import json
import uuid

import httpx
import pytest
import websockets

BASE_URL = "http://localhost:8000"
WS_BASE = "ws://localhost:8000"


@pytest.fixture(scope="module")
def http():
    with httpx.Client(base_url=BASE_URL, timeout=15) as client:
        yield client


def _unique_email() -> str:
    return f"test_stream_{uuid.uuid4().hex[:8]}@example.com"


@pytest.fixture(scope="module")
def token(http):
    resp = http.post("/auth/register", json={"email": _unique_email(), "password": "pass1234"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}"}


# ── Session REST ──────────────────────────────────────────────────────────────

def test_create_session_no_auth(http):
    assert http.post("/sessions").status_code == 403


def test_create_session(http, headers):
    resp = http.post("/sessions", headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert "id" in data
    assert data["ended_at"] is None
    assert data["person_id"] is None


def test_list_sessions(http, headers):
    resp = http.get("/sessions", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1  # at least the one from test_create_session


def test_get_session_not_found(http, headers):
    resp = http.get(f"/sessions/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


def test_end_session(http, headers):
    sess = http.post("/sessions", headers=headers).json()
    resp = http.post(f"/sessions/{sess['id']}/end", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["ended_at"] is not None

    # Idempotent
    resp2 = http.post(f"/sessions/{sess['id']}/end", headers=headers)
    assert resp2.status_code == 200


def test_list_segments_empty(http, headers):
    sess = http.post("/sessions", headers=headers).json()
    resp = http.get(f"/sessions/{sess['id']}/segments", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


# ── WebSocket streaming ───────────────────────────────────────────────────────

def test_mock_stream_writes_8_segments(http, headers, token):
    sess_id = http.post("/sessions", headers=headers).json()["id"]

    async def _stream():
        uri = f"{WS_BASE}/ws/stream/{sess_id}?token={token}"
        msgs = []
        async with websockets.connect(uri) as ws:
            async for raw in ws:
                msg = json.loads(raw)
                msgs.append(msg)
                if msg["type"] == "done":
                    break
        return msgs

    msgs = asyncio.run(_stream())

    segments = [m for m in msgs if m["type"] == "segment"]
    assert len(segments) == 8
    assert msgs[-1]["type"] == "done"

    # Each segment has required fields
    for seg in segments:
        assert seg["speaker"] in ("A", "B")
        assert seg["text"]
        assert isinstance(seg["start_ms"], int)
        assert isinstance(seg["end_ms"], int)


def test_mock_stream_persists_to_db(http, headers, token):
    sess_id = http.post("/sessions", headers=headers).json()["id"]

    async def _stream():
        uri = f"{WS_BASE}/ws/stream/{sess_id}?token={token}"
        async with websockets.connect(uri) as ws:
            async for raw in ws:
                if json.loads(raw)["type"] == "done":
                    break

    asyncio.run(_stream())

    segs = http.get(f"/sessions/{sess_id}/segments", headers=headers).json()
    assert len(segs) == 8
    assert segs[0]["speaker_label"] in ("A", "B")
    assert segs[0]["text"]
    assert segs[0]["start_ms"] < segs[0]["end_ms"]

    # Session should be marked ended
    sess = http.get(f"/sessions/{sess_id}", headers=headers).json()
    assert sess["ended_at"] is not None


def test_ws_rejects_bad_token(http, headers):
    sess_id = http.post("/sessions", headers=headers).json()["id"]

    async def _stream():
        uri = f"{WS_BASE}/ws/stream/{sess_id}?token=not-a-valid-jwt"
        try:
            async with websockets.connect(uri) as ws:
                await asyncio.wait_for(ws.recv(), timeout=2.0)
        except Exception:
            pass

    asyncio.run(_stream())

    # No segments written — server rejected the connection
    segs = http.get(f"/sessions/{sess_id}/segments", headers=headers).json()
    assert len(segs) == 0


def test_ws_rejects_already_ended_session(http, headers, token):
    sess = http.post("/sessions", headers=headers).json()
    sess_id = sess["id"]
    http.post(f"/sessions/{sess_id}/end", headers=headers)

    async def _stream():
        uri = f"{WS_BASE}/ws/stream/{sess_id}?token={token}"
        try:
            async with websockets.connect(uri) as ws:
                await asyncio.wait_for(ws.recv(), timeout=2.0)
        except Exception:
            pass

    asyncio.run(_stream())

    # Session still has no segments (stream was rejected)
    segs = http.get(f"/sessions/{sess_id}/segments", headers=headers).json()
    assert len(segs) == 0


def test_session_isolation(http, token):
    """Sessions from one user are not visible to another."""
    other_email = _unique_email()
    other_token = http.post(
        "/auth/register", json={"email": other_email, "password": "pass1234"}
    ).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    # Create session as first user
    sess_id = http.post("/sessions", headers={"Authorization": f"Bearer {token}"}).json()["id"]

    # Second user cannot access it
    assert http.get(f"/sessions/{sess_id}", headers=other_headers).status_code == 404
    assert http.get(f"/sessions/{sess_id}/segments", headers=other_headers).status_code == 404
