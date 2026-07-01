"""
Voice identity service: embedding computation, wearer anchoring, person matching.

Uses resemblyzer (GE2E model) for 256d speaker embeddings. Falls back to a
deterministic mock when resemblyzer is not yet loaded (first call triggers model
download/load which takes ~2s).

Flow during a live session:
  1. Raw PCM bytes buffered per speaker label (from AssemblyAI timestamps)
  2. Once >= min_audio_seconds accumulated: compute_embedding()
  3. is_wearer() → if True, speaker_role = "wearer"
  4. match_person() → if sim >= threshold, speaker_role = "other", person identified
  5. Backfill all prior segments for that speaker label
"""

import hashlib
import io
import tempfile
import uuid
from typing import Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.person import Person, VoiceEmbedding
from app.models.user import UserVoiceEmbedding

# Load resemblyzer once at module level — model weights cached after first load
_encoder = None
_HAS_RESEMBLYZER = False

try:
    from resemblyzer import VoiceEncoder
    _encoder = VoiceEncoder()
    _HAS_RESEMBLYZER = True
except Exception:
    pass


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 16000, channels: int = 1, bits: int = 16) -> bytes:
    """Wrap raw PCM bytes in a minimal RIFF/WAV header."""
    data_size = len(pcm_bytes)
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = (
        b"RIFF" + (36 + data_size).to_bytes(4, "little") +
        b"WAVE" +
        b"fmt " + (16).to_bytes(4, "little") +
        (1).to_bytes(2, "little") +           # PCM
        channels.to_bytes(2, "little") +
        sample_rate.to_bytes(4, "little") +
        byte_rate.to_bytes(4, "little") +
        block_align.to_bytes(2, "little") +
        bits.to_bytes(2, "little") +
        b"data" + data_size.to_bytes(4, "little")
    )
    return header + pcm_bytes


def _preprocess_and_embed(audio_bytes: bytes) -> list[float]:
    """
    Write bytes to a temp file, preprocess via resemblyzer, compute 256d embedding.
    Supports WAV, MP3, M4A, OGG, and raw PCM (16kHz 16-bit mono).
    """
    # Detect format from magic bytes
    if audio_bytes[:4] == b"fLaC":
        suffix = ".flac"
    elif audio_bytes[:3] == b"ID3" or audio_bytes[:2] == b"\xff\xfb":
        suffix = ".mp3"
    elif audio_bytes[4:8] == b"ftyp":
        suffix = ".m4a"
    elif audio_bytes[:4] == b"OggS":
        suffix = ".ogg"
    elif audio_bytes[:4] == b"RIFF":
        suffix = ".wav"
    else:
        # Assume raw PCM (16kHz, 16-bit, mono) — wrap in WAV header
        audio_bytes = _pcm_to_wav(audio_bytes)
        suffix = ".wav"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name

    try:
        import librosa
        wav, sr = librosa.load(tmp_path, sr=None, mono=True)
        if sr != 16000:
            wav = librosa.resample(wav, orig_sr=sr, target_sr=16000)
        return _encoder.embed_utterance(wav).tolist()
    finally:
        import os
        os.unlink(tmp_path)


def _mock_embedding(audio_bytes: bytes) -> list[float]:
    """Deterministic 256d unit vector. Same bytes → same vector."""
    seed = int(hashlib.sha256(audio_bytes).hexdigest()[:16], 16) % (2**32)
    rng = np.random.default_rng(seed)
    vec = rng.standard_normal(256).astype(np.float32)
    vec /= np.linalg.norm(vec) + 1e-8
    return vec.tolist()


import logging as _logging
_log = _logging.getLogger("jarvis.identity")


def compute_embedding(audio_bytes: bytes) -> list[float]:
    """256d speaker embedding. Uses resemblyzer when available, mock otherwise."""
    if _HAS_RESEMBLYZER and _encoder is not None:
        try:
            emb = _preprocess_and_embed(audio_bytes)
            _log.info("compute_embedding: real resemblyzer, %d bytes input", len(audio_bytes))
            return emb
        except Exception as e:
            _log.error("compute_embedding: resemblyzer failed (%s), falling back to mock", e)
    _log.warning("compute_embedding: using MOCK embedding")
    return _mock_embedding(audio_bytes)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    na, nb = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    return float(np.dot(na, nb) / (np.linalg.norm(na) * np.linalg.norm(nb) + 1e-8))


async def is_wearer(user_id: uuid.UUID, embedding: list[float], db: AsyncSession) -> bool:
    """True if embedding matches the wearer's enrolled voice."""
    result = await db.execute(
        select(UserVoiceEmbedding).where(UserVoiceEmbedding.user_id == user_id).limit(1)
    )
    uv = result.scalar_one_or_none()
    if not uv or uv.embedding is None:
        return False
    return cosine_similarity(list(uv.embedding), embedding) >= settings.wearer_match_threshold


async def match_person(
    user_id: uuid.UUID,
    embedding: list[float],
    db: AsyncSession,
) -> tuple[Optional[Person], float]:
    """
    Find the closest enrolled person by voice similarity.
    Returns (person, similarity) or (None, 0.0).
    """
    result = await db.execute(
        select(VoiceEmbedding, Person)
        .join(Person, VoiceEmbedding.person_id == Person.id)
        .where(Person.user_id == user_id)
        .where(VoiceEmbedding.embedding.is_not(None))
    )
    rows = result.all()

    best_person: Optional[Person] = None
    best_sim = 0.0
    for ve, person in rows:
        sim = cosine_similarity(list(ve.embedding), embedding)
        if sim > best_sim:
            best_sim = sim
            best_person = person

    return best_person, best_sim


async def resolve_speaker(
    user_id: uuid.UUID,
    audio_bytes: bytes,
    db: AsyncSession,
) -> tuple[str, Optional[uuid.UUID], float]:
    """
    Identify who is speaking from accumulated audio bytes.

    Returns:
        (speaker_role, person_id, confidence)
        speaker_role: "wearer" | "other" | "unknown"
        person_id: UUID if a known person matched, else None
        confidence: cosine similarity score
    """
    embedding = compute_embedding(audio_bytes)

    # Check wearer first
    result = await db.execute(
        select(UserVoiceEmbedding).where(UserVoiceEmbedding.user_id == user_id).limit(1)
    )
    uv = result.scalar_one_or_none()
    if uv and uv.embedding is not None:
        sim = cosine_similarity(list(uv.embedding), embedding)
        _log.info("resolve_speaker: wearer similarity=%.4f (threshold=%.2f)", sim, settings.wearer_match_threshold)
        if sim >= settings.wearer_match_threshold:
            return "wearer", None, sim
    else:
        _log.warning("resolve_speaker: no enrolled wearer embedding found")

    # Check enrolled people
    person, sim = await match_person(user_id, embedding, db)
    _log.info("resolve_speaker: best person match similarity=%.4f (threshold=%.2f)", sim, settings.identity_low_confidence)
    if person and sim >= settings.identity_low_confidence:
        return "other", person.id, sim

    return "unknown", None, 0.0
