import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypedDict

from app.config import settings

_FIXTURE = Path(__file__).parent.parent.parent / "tests/fixtures/sample_diarized.json"


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
            await asyncio.sleep(0)  # yield between segments


async def run_real_transcription(
    audio_queue: "asyncio.Queue[bytes | None]",
    on_segment: Callable[[TranscriptSegment], Awaitable[None]],
) -> None:
    """AssemblyAI real-time streaming. Runs SDK's sync transcriber in a thread pool."""
    import concurrent.futures

    import assemblyai as aai

    loop = asyncio.get_running_loop()

    def on_data(t: aai.RealtimeTranscript) -> None:
        if isinstance(t, aai.RealtimeFinalTranscript) and t.text:
            seg = TranscriptSegment(
                speaker_label=getattr(t, "speaker", "A") or "A",
                text=t.text,
                start_ms=t.audio_start,
                end_ms=t.audio_end,
                confidence=t.confidence or 1.0,
            )
            asyncio.run_coroutine_threadsafe(on_segment(seg), loop)

    transcriber = aai.RealtimeTranscriber(
        sample_rate=16_000,
        on_data=on_data,
        on_error=lambda _: None,
        api_key=settings.assemblyai_api_key,
        speaker_labels=True,
    )

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        await loop.run_in_executor(pool, transcriber.connect)
        try:
            while True:
                chunk = await audio_queue.get()
                if chunk is None:
                    break
                await loop.run_in_executor(pool, transcriber.stream, [chunk])
        finally:
            await loop.run_in_executor(pool, transcriber.close)
