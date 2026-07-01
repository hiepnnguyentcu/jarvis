import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypedDict

from app.config import settings

_FIXTURE = Path(__file__).parent.parent.parent / "tests/fixtures/sample_diarized.json"
log = logging.getLogger("jarvis.transcription")


class TranscriptSegment(TypedDict):
    speaker_label: str
    text: str
    start_ms: int
    end_ms: int
    confidence: float


async def run_mock_transcription(
    on_segment: Callable[[TranscriptSegment], Awaitable[None]],
) -> None:
    """Replay fixture utterances with no delay — all segments fire immediately for fast tests."""
    data = json.loads(_FIXTURE.read_text())
    for utt in data["utterances"]:
        if utt["type"] == "FinalTranscript":
            await on_segment(
                TranscriptSegment(
                    speaker_label=utt["speaker"],
                    text=utt["text"],
                    start_ms=utt["start"],
                    end_ms=utt["end"],
                    confidence=utt.get("confidence", 1.0),
                )
            )
            await asyncio.sleep(0)


async def run_real_transcription(
    audio_queue: "asyncio.Queue[bytes | None]",
    on_segment: Callable[[TranscriptSegment], Awaitable[None]],
) -> None:
    """AssemblyAI Streaming v3. Runs in a thread pool alongside the async audio queue."""
    import concurrent.futures
    import time

    from assemblyai.streaming.v3 import StreamingClient, models as sm

    loop = asyncio.get_running_loop()

    # Monotonic clock offset so we can synthesise start_ms / end_ms per turn
    session_start = time.monotonic()

    def on_turn(_client: object, turn: sm.TurnEvent) -> None:
        if not turn.transcript:
            return
        label = turn.speaker_label or "A"
        now_ms = int((time.monotonic() - session_start) * 1000)
        seg = TranscriptSegment(
            speaker_label=label,
            text=turn.transcript,
            start_ms=max(0, now_ms - 2000),
            end_ms=now_ms,
            confidence=1.0,
        )
        log.info("Segment [%s]: %s", label, turn.transcript[:80])
        asyncio.run_coroutine_threadsafe(on_segment(seg), loop)

    def on_error(_client: object, err: sm.ErrorEvent) -> None:
        log.error("AssemblyAI error: %s", err)

    client = StreamingClient(sm.StreamingClientOptions(api_key=settings.assemblyai_api_key))
    client.on(sm.StreamingEvents.Turn, on_turn)
    client.on(sm.StreamingEvents.Error, on_error)

    # v3 requires frames between 50ms–1000ms; iOS sends 20ms (640 bytes).
    # Buffer to 100ms (3200 bytes) before sending.
    _BYTES_PER_MS = 32  # 16kHz × 16-bit mono
    _MIN_FRAME = 100 * _BYTES_PER_MS  # 3200 bytes = 100ms

    bytes_sent = 0
    send_buf = bytearray()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        log.info("Connecting to AssemblyAI streaming v3...")
        await loop.run_in_executor(
            pool,
            lambda: client.connect(sm.StreamingParameters(
                sample_rate=16_000,
                speaker_labels=True,
            )),
        )
        log.info("AssemblyAI connected")
        try:
            while True:
                chunk = await audio_queue.get()
                if chunk is None:
                    if send_buf:
                        await loop.run_in_executor(pool, client.stream, bytes(send_buf))
                        bytes_sent += len(send_buf)
                    break
                send_buf += chunk
                if len(send_buf) >= _MIN_FRAME:
                    await loop.run_in_executor(pool, client.stream, bytes(send_buf))
                    bytes_sent += len(send_buf)
                    send_buf = bytearray()
        finally:
            log.info("AssemblyAI session ended — %d bytes sent", bytes_sent)
            await loop.run_in_executor(pool, client.disconnect)
