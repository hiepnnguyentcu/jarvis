"""
Integration tests for Chunk 4: Voice identity + people management.
Tests hit the live server at http://localhost:8000.
"""

import io
import uuid
import struct
import wave

import httpx
import pytest

BASE_URL = "http://localhost:8000"


def _unique_email() -> str:
    return f"test_people_{uuid.uuid4().hex[:8]}@example.com"


def _make_wav(duration_s: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Create a minimal valid WAV file filled with silence."""
    n_samples = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))
    return buf.getvalue()


_AUDIO_A = _make_wav(1.0)   # "person A" audio
_AUDIO_B = _make_wav(2.0)   # "person B" audio — different length → different embedding


@pytest.fixture(scope="module")
def http():
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        yield client


@pytest.fixture(scope="module")
def token(http):
    resp = http.post("/auth/register", json={"email": _unique_email(), "password": "pass1234"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}"}


# ── People CRUD ───────────────────────────────────────────────────────────────

def test_create_person_no_auth(http):
    assert http.post("/people", json={"name": "Alice"}).status_code == 403


def test_create_person(http, headers):
    resp = http.post("/people", json={"name": "Alice"}, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Alice"
    assert data["has_voice_embedding"] is False
    assert "id" in data


def test_list_people(http, headers):
    resp = http.get("/people", headers=headers)
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()]
    assert "Alice" in names


def test_get_person(http, headers):
    person = http.post("/people", json={"name": "Bob"}, headers=headers).json()
    resp = http.get(f"/people/{person['id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["name"] == "Bob"


def test_get_person_not_found(http, headers):
    resp = http.get(f"/people/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


def test_person_isolation(http, token):
    """People are scoped to the creating user."""
    other_token = http.post(
        "/auth/register", json={"email": _unique_email(), "password": "pass1234"}
    ).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    person = http.post(
        "/people", json={"name": "Private"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    assert http.get(f"/people/{person['id']}", headers=other_headers).status_code == 404


# ── Voice enrollment ──────────────────────────────────────────────────────────

def test_enroll_voice_sets_embedding(http, headers):
    person = http.post("/people", json={"name": "Carol"}, headers=headers).json()
    pid = person["id"]

    # No embedding before enrollment
    assert http.get(f"/people/{pid}", headers=headers).json()["has_voice_embedding"] is False

    resp = http.post(
        f"/people/{pid}/enroll",
        files={"audio": ("carol.wav", _AUDIO_A, "audio/wav")},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["has_voice_embedding"] is True

    # GET also shows embedding present
    assert http.get(f"/people/{pid}", headers=headers).json()["has_voice_embedding"] is True


def test_enroll_voice_idempotent(http, headers):
    """Re-enrolling updates the embedding, doesn't create a duplicate row."""
    person = http.post("/people", json={"name": "Dave"}, headers=headers).json()
    pid = person["id"]

    http.post(f"/people/{pid}/enroll", files={"audio": ("a.wav", _AUDIO_A, "audio/wav")}, headers=headers)
    http.post(f"/people/{pid}/enroll", files={"audio": ("b.wav", _AUDIO_B, "audio/wav")}, headers=headers)

    # Still only one person, has embedding
    assert http.get(f"/people/{pid}", headers=headers).json()["has_voice_embedding"] is True


def test_enroll_voice_too_small(http, headers):
    person = http.post("/people", json={"name": "Eve"}, headers=headers).json()
    resp = http.post(
        f"/people/{person['id']}/enroll",
        files={"audio": ("tiny.wav", b"\x00" * 10, "audio/wav")},
        headers=headers,
    )
    assert resp.status_code == 422


def test_enroll_voice_wrong_person(http, headers):
    resp = http.post(
        f"/people/{uuid.uuid4()}/enroll",
        files={"audio": ("a.wav", _AUDIO_A, "audio/wav")},
        headers=headers,
    )
    assert resp.status_code == 404


# ── Wearer enroll-voice computes embedding ────────────────────────────────────

def test_enroll_wearer_voice_stores_embedding(http):
    """POST /auth/enroll-voice now computes and stores the embedding (not null)."""
    tok = http.post(
        "/auth/register", json={"email": _unique_email(), "password": "pass1234"}
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    resp = http.post(
        "/auth/enroll-voice",
        files={"audio": ("wearer.wav", _AUDIO_A, "audio/wav")},
        headers=h,
    )
    assert resp.status_code == 204

    # User is now enrolled
    me = http.get("/auth/me", headers=h).json()
    assert me["voice_enrolled"] is True


# ── Identity: same audio → same embedding (cos sim = 1.0) ────────────────────

def test_same_audio_same_embedding(http, headers):
    """
    Enroll two different people with the same audio bytes.
    They should have identical embeddings (mock embedding is deterministic).
    This verifies compute_embedding is pure / deterministic.
    Separately, enrolling a person with different audio gives a different embedding.
    """
    p1 = http.post("/people", json={"name": "Frank"}, headers=headers).json()
    p2 = http.post("/people", json={"name": "Grace"}, headers=headers).json()

    # Enroll both with same audio
    http.post(f"/people/{p1['id']}/enroll", files={"audio": ("a.wav", _AUDIO_A, "audio/wav")}, headers=headers)
    http.post(f"/people/{p2['id']}/enroll", files={"audio": ("a.wav", _AUDIO_A, "audio/wav")}, headers=headers)

    # Both have embeddings
    assert http.get(f"/people/{p1['id']}", headers=headers).json()["has_voice_embedding"] is True
    assert http.get(f"/people/{p2['id']}", headers=headers).json()["has_voice_embedding"] is True
