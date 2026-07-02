import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.dependencies import get_current_user
from app.models.person import Person
from app.models.user import User, UserVoiceEmbedding
from app.services.identity import compute_embedding
from app.services.auth import (
    create_access_token,
    create_refresh_token,
    hash_password,
    revoke_refresh_token,
    verify_password,
    verify_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_ALLOWED_AUDIO_TYPES = {
    "audio/wav", "audio/wave", "audio/x-wav",
    "audio/mpeg", "audio/mp4", "audio/ogg",
    "audio/webm", "application/octet-stream",
}
_MAX_AUDIO_BYTES = 50 * 1024 * 1024  # 50MB


# ── Schemas ──────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    first_name: str = ""
    last_name: str = ""


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    voice_enrolled: bool
    is_admin: bool
    wearer_person_id: Optional[uuid.UUID]
    created_at: datetime


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse)
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_session)):
    existing = await session.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=await create_refresh_token(user.id),
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=await create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    try:
        user_id = await verify_refresh_token(body.refresh_token)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    await revoke_refresh_token(body.refresh_token)

    return TokenResponse(
        access_token=create_access_token(user_id),
        refresh_token=await create_refresh_token(user_id),
    )


class UpdateProfileRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None


@router.patch("/me", response_model=UserResponse)
async def update_me(
    body: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if body.first_name is not None:
        user.first_name = body.first_name
    if body.last_name is not None:
        user.last_name = body.last_name
    session.add(user)
    await session.commit()
    return UserResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        voice_enrolled=user.voice_enrolled,
        is_admin=user.is_admin,
        wearer_person_id=user.wearer_person_id,
        created_at=user.created_at,
    )


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return UserResponse(
        id=user.id,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        voice_enrolled=user.voice_enrolled,
        is_admin=user.is_admin,
        wearer_person_id=user.wearer_person_id,
        created_at=user.created_at,
    )


@router.post("/enroll-voice", status_code=status.HTTP_204_NO_CONTENT)
async def enroll_voice(
    audio: UploadFile,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Accept a 10–15s audio clip of the wearer speaking.
    Stores the audio reference for embedding extraction in Chunk 4.
    Sets voice_enrolled = True so sessions can start.
    """
    if audio.content_type and audio.content_type not in _ALLOWED_AUDIO_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported audio type: {audio.content_type}",
        )

    data = await audio.read()
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Audio file too large (max 50MB)",
        )
    if len(data) < 1024:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Audio file too small",
        )

    embedding = compute_embedding(data)

    # Upsert UserVoiceEmbedding with computed embedding
    result = await session.execute(
        select(UserVoiceEmbedding).where(UserVoiceEmbedding.user_id == user.id)
    )
    existing_emb = result.scalar_one_or_none()
    if existing_emb:
        existing_emb.embedding = embedding
    else:
        session.add(UserVoiceEmbedding(user_id=user.id, embedding=embedding))

    user.voice_enrolled = True

    # Auto-create a Person record for the wearer if not already linked
    if not user.wearer_person_id:
        display_name = " ".join(filter(None, [user.first_name, user.last_name])) or user.email.split("@")[0]
        person = Person(user_id=user.id, name=display_name)
        session.add(person)
        await session.flush()

        # Create AGE vertex for the wearer
        from app.db import cypher
        safe_name = display_name.replace("'", "\\'")
        await cypher(
            session,
            f"CREATE (:Person {{person_id: '{person.id}', name: '{safe_name}', user_id: '{user.id}'}})",
        )

        user.wearer_person_id = person.id

    session.add(user)
    await session.commit()


@router.post("/check-voice")
async def check_voice(
    audio: UploadFile,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Debug endpoint: upload an audio clip and see if it matches your enrolled voice.
    Returns similarity score and whether it crosses the wearer threshold.
    """
    if not user.voice_enrolled:
        raise HTTPException(status_code=400, detail="No voice enrolled yet")

    data = await audio.read()
    if len(data) < 1024:
        raise HTTPException(status_code=422, detail="Audio file too small")

    from app.services.identity import cosine_similarity

    new_embedding = compute_embedding(data)

    result = await session.execute(
        select(UserVoiceEmbedding).where(UserVoiceEmbedding.user_id == user.id).limit(1)
    )
    enrolled = result.scalar_one_or_none()
    if not enrolled or enrolled.embedding is None:
        raise HTTPException(status_code=404, detail="Enrolled embedding not found")

    similarity = cosine_similarity(list(enrolled.embedding), new_embedding)
    threshold = settings.wearer_match_threshold

    return {
        "similarity": round(similarity, 4),
        "threshold": threshold,
        "is_wearer": similarity >= threshold,
        "verdict": "MATCH — recognised as wearer" if similarity >= threshold else "NO MATCH — not recognised as wearer",
    }
