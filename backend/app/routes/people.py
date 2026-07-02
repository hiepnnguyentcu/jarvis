import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import cypher, get_session
from app.dependencies import get_current_user
from app.models.person import Person, VoiceEmbedding
from app.models.user import User
from app.services.identity import compute_embedding

router = APIRouter(prefix="/people", tags=["people"])

_ALLOWED_AUDIO = {
    "audio/wav", "audio/wave", "audio/x-wav",
    "audio/mpeg", "audio/mp4", "audio/ogg",
    "audio/webm", "application/octet-stream",
}
_MAX_AUDIO_BYTES = 50 * 1024 * 1024


class PersonCreate(BaseModel):
    name: str


class PersonOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    created_at: datetime
    has_voice_embedding: bool = False
    is_wearer: bool = False


@router.post("", response_model=PersonOut, status_code=201)
async def create_person(
    body: PersonCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> PersonOut:
    # Prevent accidental duplicate of the wearer's own Person node
    if user.wearer_person_id:
        wearer = await db.get(Person, user.wearer_person_id)
        if wearer and wearer.name.lower() == body.name.strip().lower():
            raise HTTPException(
                status_code=409,
                detail=f"A person named '{wearer.name}' already exists as the enrolled wearer. Use GET /people/{wearer.id} instead.",
            )

    person = Person(user_id=user.id, name=body.name)
    db.add(person)
    await db.commit()
    await db.refresh(person)

    # Create corresponding AGE :Person vertex
    pid = str(person.id)
    uid = str(user.id)
    name_safe = body.name.replace("'", "\\'")
    await cypher(
        db,
        f"CREATE (:Person {{person_id: '{pid}', name: '{name_safe}', user_id: '{uid}'}})",
    )
    await db.commit()

    return PersonOut(
        id=person.id,
        user_id=person.user_id,
        name=person.name,
        created_at=person.created_at,
        has_voice_embedding=False,
    )


@router.get("", response_model=list[PersonOut])
async def list_people(
    search: Optional[str] = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> list[PersonOut]:
    query = select(Person).where(Person.user_id == user.id)
    if search:
        query = query.where(Person.name.ilike(f"%{search}%"))
    result = await db.execute(query.order_by(Person.created_at.desc()))
    people = result.scalars().all()

    # Fetch which people have embeddings
    emb_result = await db.execute(
        select(VoiceEmbedding.person_id).where(
            VoiceEmbedding.person_id.in_([p.id for p in people]),
            VoiceEmbedding.embedding.is_not(None),
        )
    )
    enrolled_ids = {row[0] for row in emb_result}

    return [
        PersonOut(
            id=p.id,
            user_id=p.user_id,
            name=p.name,
            created_at=p.created_at,
            has_voice_embedding=(
                p.id in enrolled_ids
                or (p.id == user.wearer_person_id and user.voice_enrolled)
            ),
            is_wearer=(p.id == user.wearer_person_id),
        )
        for p in people
    ]


@router.get("/{person_id}", response_model=PersonOut)
async def get_person(
    person_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> PersonOut:
    person = await db.get(Person, person_id)
    if not person or person.user_id != user.id:
        raise HTTPException(status_code=404, detail="Person not found")

    emb = await db.execute(
        select(VoiceEmbedding)
        .where(VoiceEmbedding.person_id == person_id)
        .where(VoiceEmbedding.embedding.is_not(None))
        .limit(1)
    )
    has_emb = (
        emb.scalar_one_or_none() is not None
        or (person_id == user.wearer_person_id and user.voice_enrolled)
    )
    is_wearer = (person_id == user.wearer_person_id)

    return PersonOut(
        id=person.id,
        user_id=person.user_id,
        name=person.name,
        created_at=person.created_at,
        has_voice_embedding=has_emb,
        is_wearer=is_wearer,
    )


@router.get("/{person_id}/graph")
async def get_graph(
    person_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Return entity graph (nodes + SPO edges) for a person."""
    from app.services.graph_extraction import get_person_graph

    person = await db.get(Person, person_id)
    if not person or person.user_id != user.id:
        raise HTTPException(status_code=404, detail="Person not found")
    return await get_person_graph(person_id, user.id, db)


@router.get("/{person_id}/recap")
async def get_recap(
    person_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    """Generate and return a spoken recap about a known person."""
    from app.services.recap import build_recap

    person = await db.get(Person, person_id)
    if not person or person.user_id != user.id:
        raise HTTPException(status_code=404, detail="Person not found")
    recap = await build_recap(person_id, user.id, db)
    return {"recap": recap}


@router.post("/{person_id}/enroll", response_model=PersonOut)
async def enroll_voice(
    person_id: uuid.UUID,
    audio: UploadFile,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
) -> PersonOut:
    """Upload audio clip to set this person's voice embedding."""
    person = await db.get(Person, person_id)
    if not person or person.user_id != user.id:
        raise HTTPException(status_code=404, detail="Person not found")

    if audio.content_type and audio.content_type not in _ALLOWED_AUDIO:
        raise HTTPException(status_code=422, detail=f"Unsupported audio type: {audio.content_type}")

    data = await audio.read()
    if len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file too large (max 50MB)")
    if len(data) < 1024:
        raise HTTPException(status_code=422, detail="Audio file too small")

    embedding = compute_embedding(data)

    # Upsert VoiceEmbedding
    existing = await db.execute(
        select(VoiceEmbedding).where(VoiceEmbedding.person_id == person_id).limit(1)
    )
    ve = existing.scalar_one_or_none()
    if ve:
        ve.embedding = embedding
    else:
        ve = VoiceEmbedding(person_id=person_id, embedding=embedding)
        db.add(ve)

    await db.commit()

    return PersonOut(
        id=person.id,
        user_id=person.user_id,
        name=person.name,
        created_at=person.created_at,
        has_voice_embedding=True,
    )
