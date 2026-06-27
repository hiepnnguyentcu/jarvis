import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models.session import Segment, Session
from app.models.user import User
from app.services.auth import decode_access_token
from app.services.transcription import (
    TranscriptSegment,
    run_mock_transcription,
    run_real_transcription,
)

router = APIRouter()

# 16kHz, 16-bit mono: 1ms of audio = 32 bytes
_BYTES_PER_MS = 32


async def _authenticate(token: str, db: AsyncSession) -> User | None:
    try:
        user_id = decode_access_token(token)
    except ValueError:
        return None
    return await db.get(User, user_id)


async def _backfill_speaker_role(
    session_id: uuid.UUID,
    speaker_label: str,
    speaker_role: str,
    person_id: uuid.UUID | None,
    db: AsyncSession,
) -> None:
    """Update all segments for a speaker label once identity is resolved."""
    await db.execute(
        update(Segment)
        .where(Segment.session_id == session_id)
        .where(Segment.speaker_label == speaker_label)
        .values(speaker_role=speaker_role)
    )
    await db.commit()


@router.websocket("/ws/stream/{session_id}")
async def ws_stream(
    websocket: WebSocket,
    session_id: uuid.UUID,
    token: str = Query(...),
    db: AsyncSession = Depends(get_session),
) -> None:
    user = await _authenticate(token, db)
    if not user:
        await websocket.close(code=1008)
        return

    sess = await db.get(Session, session_id)
    if not sess or sess.user_id != user.id or sess.ended_at:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    # ── Per-session identity state ────────────────────────────────────────────
    # Full PCM buffer — used to extract per-speaker slices by timestamp
    audio_buffer = bytearray()
    # Accumulated audio bytes per AssemblyAI speaker label
    speaker_audio: dict[str, bytearray] = {}
    # Resolved identities: label → (speaker_role, person_id)
    speaker_identity: dict[str, tuple[str, uuid.UUID | None]] = {}

    async def _try_resolve(label: str) -> None:
        """Attempt identity resolution once enough audio is accumulated."""
        if label in speaker_identity:
            return
        audio = bytes(speaker_audio[label])
        duration_s = len(audio) / (16000 * 2)
        if duration_s < settings.min_audio_seconds:
            return

        from app.services.identity import resolve_speaker
        role, person_id, confidence = await resolve_speaker(user.id, audio, db)
        speaker_identity[label] = (role, person_id)

        await _backfill_speaker_role(session_id, label, role, person_id, db)

        # Attach best-matched person to session if not already set
        if role == "other" and person_id and not sess.person_id:
            sess.person_id = person_id
            sess.identity_confidence = confidence
            await db.commit()

    async def on_segment(seg: TranscriptSegment) -> None:
        label = seg["speaker_label"]

        # Extract the audio slice for this utterance from the buffer
        start_byte = seg["start_ms"] * _BYTES_PER_MS
        end_byte = seg["end_ms"] * _BYTES_PER_MS
        chunk = bytes(audio_buffer[start_byte:end_byte])
        if chunk:
            if label not in speaker_audio:
                speaker_audio[label] = bytearray()
            speaker_audio[label] += chunk
            await _try_resolve(label)

        # Resolve role from identity state (may already be known)
        role, _ = speaker_identity.get(label, (None, None))

        segment = Segment(
            session_id=session_id,
            speaker_label=label,
            speaker_role=role,
            text=seg["text"],
            start_ms=seg["start_ms"],
            end_ms=seg["end_ms"],
        )
        db.add(segment)
        await db.commit()

        try:
            await websocket.send_json({
                "type": "segment",
                "speaker": label,
                "speaker_role": role,
                "text": seg["text"],
                "start_ms": seg["start_ms"],
                "end_ms": seg["end_ms"],
            })
        except Exception:
            pass

    # ── Mock path ─────────────────────────────────────────────────────────────
    if settings.mock_assemblyai:
        await run_mock_transcription(on_segment)

        sess.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()

        try:
            await websocket.send_json({"type": "done"})
            try:
                async with asyncio.timeout(5.0):
                    while True:
                        await websocket.receive()
            except (asyncio.TimeoutError, WebSocketDisconnect, Exception):
                pass
        except Exception:
            pass

    # ── Real path ─────────────────────────────────────────────────────────────
    else:
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        transcription_task = asyncio.create_task(
            run_real_transcription(audio_queue, on_segment)
        )

        try:
            async for chunk in websocket.iter_bytes():
                audio_buffer += chunk
                await audio_queue.put(chunk)
        except WebSocketDisconnect:
            pass
        finally:
            await audio_queue.put(None)
            try:
                await transcription_task
            except Exception:
                pass

        sess.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)

        if audio_buffer:
            from app.services.storage import upload_audio
            key = upload_audio(bytes(audio_buffer), user.id, session_id)
            if key:
                sess.audio_r2_key = key

        await db.commit()
