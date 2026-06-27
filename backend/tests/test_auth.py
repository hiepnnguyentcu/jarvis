"""
Auth integration tests — runs against the live server at BASE_URL.
In CI this is the Docker backend service (http://localhost:8000).
Run inside the container with: pytest tests/test_auth.py -v
"""
import io
import uuid

import httpx
import pytest

BASE_URL = "http://localhost:8000"


def unique_email() -> str:
    return f"test_{uuid.uuid4().hex[:8]}@example.com"


@pytest.fixture(scope="session")
def http():
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        yield client


def test_health(http):
    resp = http.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_register_returns_tokens(http):
    resp = http.post("/auth/register", json={"email": unique_email(), "password": "pass1234"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_register_duplicate_email(http):
    email = unique_email()
    http.post("/auth/register", json={"email": email, "password": "pass1234"})
    resp = http.post("/auth/register", json={"email": email, "password": "pass1234"})
    assert resp.status_code == 409


def test_login_valid(http):
    email = unique_email()
    http.post("/auth/register", json={"email": email, "password": "pass1234"})
    resp = http.post("/auth/login", json={"email": email, "password": "pass1234"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_wrong_password(http):
    email = unique_email()
    http.post("/auth/register", json={"email": email, "password": "pass1234"})
    resp = http.post("/auth/login", json={"email": email, "password": "wrongpass"})
    assert resp.status_code == 401


def test_me_requires_auth(http):
    resp = http.get("/auth/me")
    assert resp.status_code == 403


def test_me_with_valid_token(http):
    email = unique_email()
    reg = http.post("/auth/register", json={"email": email, "password": "pass1234"})
    token = reg.json()["access_token"]
    resp = http.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == email
    assert data["voice_enrolled"] is False


def test_enroll_voice_sets_flag(http):
    email = unique_email()
    reg = http.post("/auth/register", json={"email": email, "password": "pass1234"})
    token = reg.json()["access_token"]

    audio_bytes = b"\x00" * 4096
    resp = http.post(
        "/auth/enroll-voice",
        headers={"Authorization": f"Bearer {token}"},
        files={"audio": ("voice.wav", io.BytesIO(audio_bytes), "audio/wav")},
    )
    assert resp.status_code == 204

    me = http.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["voice_enrolled"] is True


def test_invalid_token_rejected(http):
    resp = http.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
