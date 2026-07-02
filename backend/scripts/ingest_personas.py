"""
Ingest synthetic persona recordings into Jarvis.

For each persona this script:
  1. Creates a Person record (skips if already exists by name)
  2. Enrolls their voice from the MP3
  3. Creates a session pre-linked to that person
  4. Streams the MP3 over the WebSocket at realtime pace
  5. Ends the session and runs knowledge graph extraction
  6. Prints a summary

Usage (from infra/ directory):
    docker compose exec \\
      -e JARVIS_EMAIL=hiepnguyentcu@gmail.com \\
      -e JARVIS_PASSWORD=yourpassword \\
      backend python /app/scripts/ingest_personas.py

MP3 files must be in /app/voice-sample/ inside the container,
i.e. jarvis/voice-sample/ on the host.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import websockets

BASE     = os.getenv("JARVIS_BASE_URL", "http://localhost:8000")
WS_BASE  = BASE.replace("http://", "ws://").replace("https://", "wss://")
VOICE_DIR = Path("/app/voice-sample")

# ── Personas ──────────────────────────────────────────────────────────────────
# Map MP3 filename (in voice-sample/) → display name for the Person record
PERSONAS = [
    ("ava.mp3",     "Ava"),
    ("daniel.mp3",  "Daniel Osei"),
    ("marcus.mp3",  "Marcus Webb"),
    ("sarah.mp3",   "Sarah Chen"),
    ("yuki.mp3",    "Yuki Tanaka"),
]

CHUNK_BYTES = 3200   # 100ms of PCM at 16 kHz 16-bit mono
CHUNK_SLEEP = 0.10   # realtime pace


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _pcm_chunks(path: Path):
    """Yield raw PCM chunks from any audio file via ffmpeg."""
    proc = subprocess.Popen(
        ["ffmpeg", "-loglevel", "error",
         "-i", str(path),
         "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
         "pipe:1"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        while True:
            chunk = proc.stdout.read(CHUNK_BYTES)
            if not chunk:
                break
            yield chunk
    finally:
        proc.stdout.close()
        proc.wait()


async def ingest_persona(
    client: httpx.AsyncClient,
    token: str,
    mp3_path: Path,
    name: str,
) -> None:
    print(f"\n{'='*60}")
    print(f"  {name}  ({mp3_path.name})")
    print(f"{'='*60}")

    if not mp3_path.exists():
        print(f"  [SKIP] file not found: {mp3_path}")
        return

    # ── 1. Create or find Person ──────────────────────────────────────────────
    r = await client.get("/people", headers=_h(token))
    r.raise_for_status()
    existing = {p["name"].lower(): p["id"] for p in r.json()}

    if name.lower() in existing:
        person_id = existing[name.lower()]
        print(f"  [PERSON] already exists — {person_id}")
    else:
        r = await client.post("/people", headers=_h(token), json={"name": name})
        r.raise_for_status()
        person_id = r.json()["id"]
        print(f"  [PERSON] created — {person_id}")

    # ── 2. Enroll voice ───────────────────────────────────────────────────────
    print(f"  [ENROLL] uploading {mp3_path.name}...")
    with open(mp3_path, "rb") as f:
        r = await client.post(
            f"/people/{person_id}/enroll",
            headers=_h(token),
            files={"audio": (mp3_path.name, f, "audio/mpeg")},
            timeout=60,
        )
    r.raise_for_status()
    print(f"  [ENROLL] done")

    # ── 3. Create session pre-linked to this person ───────────────────────────
    r = await client.post("/sessions", headers=_h(token), json={"person_id": person_id})
    r.raise_for_status()
    session_id = r.json()["id"]
    print(f"  [SESSION] created — {session_id}")

    # ── 4. Stream audio over WebSocket ────────────────────────────────────────
    ws_url = f"{WS_BASE}/ws/stream/{session_id}?token={token}"
    print(f"  [STREAM] sending at realtime pace...")

    segments_received = 0

    async def receive_loop(ws):
        nonlocal segments_received
        async for raw in ws:
            try:
                msg = json.loads(raw)
                if msg.get("type") == "segment" and msg.get("text"):
                    role = msg.get("speaker_role") or "pending"
                    print(f"  [SEG] [{role}] {msg['text'][:80]}")
                    segments_received += 1
            except Exception:
                pass

    async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
        recv_task = asyncio.create_task(receive_loop(ws))
        sent = 0
        for chunk in _pcm_chunks(mp3_path):
            await ws.send(chunk)
            sent += len(chunk)
            await asyncio.sleep(CHUNK_SLEEP)

        await asyncio.sleep(4)  # let AssemblyAI flush final segment
        recv_task.cancel()

    duration_s = sent / (16000 * 2)
    print(f"  [STREAM] {duration_s:.1f}s audio, {segments_received} segments received")

    # ── 5. End session ────────────────────────────────────────────────────────
    r = await client.post(f"/sessions/{session_id}/end", headers=_h(token))
    r.raise_for_status()
    print(f"  [END] session ended")

    if segments_received == 0:
        print(f"  [WARN] no segments received — AssemblyAI may not have transcribed anything")
        print(f"         check backend logs; you can re-run extract manually via Postman")
        return

    # ── 6. Extract knowledge graph ────────────────────────────────────────────
    r = await client.post(f"/sessions/{session_id}/extract", headers=_h(token), timeout=90)
    r.raise_for_status()
    triples = r.json().get("triples_stored", 0)
    print(f"  [EXTRACT] {triples} triples stored")

    if triples == 0:
        print(f"  [WARN] 0 triples — segments may all have speaker_role=null")
        print(f"         SELECT speaker_role, text FROM segment WHERE session_id='{session_id}';")


async def main() -> None:
    email    = os.getenv("JARVIS_EMAIL")
    password = os.getenv("JARVIS_PASSWORD")
    token    = os.getenv("JARVIS_TOKEN")

    if not token:
        if not email or not password:
            sys.exit("Set JARVIS_EMAIL + JARVIS_PASSWORD (or JARVIS_TOKEN)")
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
            r = await client.post("/auth/login", json={"email": email, "password": password})
            r.raise_for_status()
            token = r.json()["access_token"]
        print(f"[LOGIN] authenticated as {email}")

    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        for filename, name in PERSONAS:
            mp3_path = VOICE_DIR / filename
            await ingest_persona(client, token, mp3_path, name)

    print(f"\n[DONE] all personas ingested")
    print(f"       GET /people  to see the full list")


if __name__ == "__main__":
    asyncio.run(main())
