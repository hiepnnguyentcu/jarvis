"""
End-to-end Jarvis pipeline test.

Runs inside the Docker backend container (all deps present).

Usage:
    docker compose exec \\
      -e JARVIS_EMAIL=you@example.com \\
      -e JARVIS_PASSWORD=yourpassword \\
      backend python /app/scripts/stream_session.py

The script:
  1. Logs in (or uses JARVIS_TOKEN env var directly)
  2. Checks enrollment; enrolls from introduction.m4a if wearer_person_id is null
  3. Creates a session
  4. Streams aboutme.m4a over WebSocket at realtime pace
  5. Ends session, runs extraction, fetches recap
  6. Prints everything to stdout
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets

BASE = os.getenv("JARVIS_BASE_URL", "http://localhost:8000")
WS_BASE = BASE.replace("http://", "ws://").replace("https://", "wss://")

VOICE_DIR = Path("/app/voice-sample")
INTRO_FILE = VOICE_DIR / "hiep-introduction.m4a"
ABOUTME_FILE = VOICE_DIR / "hiep-aboutme.m4a"

CHUNK_BYTES = 3200   # 100ms of PCM (16kHz × 16-bit mono = 32 bytes/ms)
CHUNK_SLEEP = 0.10   # realtime pace


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _pcm_stream(path: Path):
    """Yield raw PCM chunks from an audio file via ffmpeg."""
    cmd = [
        "ffmpeg", "-loglevel", "error",
        "-i", str(path),
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            chunk = proc.stdout.read(CHUNK_BYTES)
            if not chunk:
                break
            yield chunk
    finally:
        proc.stdout.close()
        proc.wait()


async def main() -> None:
    email = os.getenv("JARVIS_EMAIL")
    password = os.getenv("JARVIS_PASSWORD")
    token = os.getenv("JARVIS_TOKEN")

    if not token:
        if not email or not password:
            sys.exit("Set JARVIS_EMAIL + JARVIS_PASSWORD (or JARVIS_TOKEN)")

    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:

        # ── 1. Login ──────────────────────────────────────────────────────────
        if not token:
            r = await client.post("/auth/login", json={"email": email, "password": password})
            r.raise_for_status()
            token = r.json()["access_token"]
            print(f"[LOGIN] token acquired")

        # ── 2. Check wearer enrollment ────────────────────────────────────────
        r = await client.get("/auth/me", headers=_h(token))
        r.raise_for_status()
        me = r.json()
        wearer_person_id = me.get("wearer_person_id")
        print(f"[ME] {me['email']} | voice_enrolled={me['voice_enrolled']} | wearer_person_id={wearer_person_id}")

        force_enroll = os.getenv("FORCE_ENROLL", "0") == "1"
        if not wearer_person_id or force_enroll:
            if not INTRO_FILE.exists():
                sys.exit(f"Intro file not found: {INTRO_FILE}")
            print(f"[ENROLL] enrolling from {INTRO_FILE.name}...")
            with open(INTRO_FILE, "rb") as f:
                r = await client.post(
                    "/auth/enroll-voice",
                    headers=_h(token),
                    files={"audio": (INTRO_FILE.name, f, "audio/mp4")},
                    timeout=60,
                )
            r.raise_for_status()
            r2 = await client.get("/auth/me", headers=_h(token))
            me = r2.json()
            wearer_person_id = me.get("wearer_person_id")
            print(f"[ENROLL] done — wearer_person_id={wearer_person_id}")

        if not wearer_person_id:
            sys.exit("[ERROR] Still no wearer_person_id after enrollment. Check backend logs.")

        # ── 3. Create session ─────────────────────────────────────────────────
        r = await client.post("/sessions", headers=_h(token), json={})
        r.raise_for_status()
        session_id = r.json()["id"]
        print(f"[SESSION] created: {session_id}")

        # ── 4. Stream audio over WebSocket ────────────────────────────────────
        if not ABOUTME_FILE.exists():
            sys.exit(f"About-me file not found: {ABOUTME_FILE}")

        ws_url = f"{WS_BASE}/ws/stream/{session_id}?token={token}"
        print(f"[STREAM] sending {ABOUTME_FILE.name} at realtime pace...")

        segments = []

        async def receive_loop(ws):
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                    if msg.get("type") == "segment" and msg.get("text"):
                        label = msg.get("speaker", "?")
                        role = msg.get("speaker_role") or "pending"
                        text = msg["text"]
                        segments.append(msg)
                        print(f"[SEGMENT] [{role}] {text}")
                except Exception:
                    pass

        async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
            recv_task = asyncio.create_task(receive_loop(ws))

            loop = asyncio.get_running_loop()
            sent = 0
            for chunk in _pcm_stream(ABOUTME_FILE):
                await ws.send(chunk)
                sent += len(chunk)
                await asyncio.sleep(CHUNK_SLEEP)

            print(f"[STREAM] done — {sent} bytes sent")
            await asyncio.sleep(3)  # let AssemblyAI flush final transcript
            recv_task.cancel()

        duration_s = sent / (16000 * 2)
        print(f"[STREAM] audio duration: {duration_s:.1f}s")

        # ── 5. End session ────────────────────────────────────────────────────
        r = await client.post(f"/sessions/{session_id}/end", headers=_h(token))
        r.raise_for_status()
        print(f"[END] session ended")

        if not segments:
            print("[WARN] No segments received. Transcription may have failed — check backend logs.")
            print("       You can still run extract manually via Postman.")
            return

        # ── 6. Extract knowledge graph ────────────────────────────────────────
        r = await client.post(f"/sessions/{session_id}/extract", headers=_h(token), timeout=60)
        r.raise_for_status()
        triples = r.json().get("triples_stored", 0)
        print(f"[EXTRACT] {triples} triples stored")

        if triples == 0:
            print("[WARN] 0 triples — segments may all have speaker_role=null (identity not resolved)")
            print(f"       Check: docker compose exec postgres psql -U jarvis -d jarvis \\")
            print(f"              -c \"SELECT speaker_role, text FROM segment WHERE session_id='{session_id}';\"")

        # ── 7. Recap ──────────────────────────────────────────────────────────
        r = await client.get(f"/people/{wearer_person_id}/recap", headers=_h(token), timeout=30)
        r.raise_for_status()
        recap = r.json().get("recap", "(no recap)")
        print(f"\n[RECAP]\n{recap}\n")

        print(f"\n[DONE] Check graph:  GET /people/{wearer_person_id}/graph")


if __name__ == "__main__":
    asyncio.run(main())
