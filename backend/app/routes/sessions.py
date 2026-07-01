import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session as _db_session
from app.dependencies import get_current_user
from app.models.person import Person
from app.models.session import Segment, Session
from app.models.user import User

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    person_id: Optional[uuid.UUID] = None


class SessionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    person_id: Optional[uuid.UUID]
    started_at: datetime
    ended_at: Optional[datetime]
    audio_r2_key: Optional[str]
    identity_confidence: Optional[float]


class SegmentOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    speaker_label: str
    speaker_role: Optional[str]
    text: str
    start_ms: int
    end_ms: int


class ExtractOut(BaseModel):
    session_id: uuid.UUID
    triples_stored: int


@router.post("", response_model=SessionOut, status_code=201)
async def create_session(
    body: SessionCreate = Body(default_factory=SessionCreate),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_db_session),
) -> SessionOut:
    person_id = body.person_id if body else None
    if person_id:
        person = await db.get(Person, person_id)
        if not person or person.user_id != user.id:
            raise HTTPException(status_code=404, detail="Person not found")

    sess = Session(user_id=user.id, person_id=person_id)
    db.add(sess)
    await db.commit()
    await db.refresh(sess)
    return SessionOut.model_validate(sess, from_attributes=True)


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_db_session),
) -> list[SessionOut]:
    result = await db.execute(
        select(Session)
        .where(Session.user_id == user.id)
        .order_by(Session.started_at.desc())
    )
    return [SessionOut.model_validate(s, from_attributes=True) for s in result.scalars()]


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_db_session),
) -> SessionOut:
    sess = await db.get(Session, session_id)
    if not sess or sess.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionOut.model_validate(sess, from_attributes=True)


@router.get("/{session_id}/segments", response_model=list[SegmentOut])
async def list_segments(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_db_session),
) -> list[SegmentOut]:
    sess = await db.get(Session, session_id)
    if not sess or sess.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    result = await db.execute(
        select(Segment)
        .where(Segment.session_id == session_id)
        .order_by(Segment.start_ms)
    )
    return [SegmentOut.model_validate(s, from_attributes=True) for s in result.scalars()]


@router.post("/{session_id}/end", response_model=SessionOut)
async def end_session(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_db_session),
) -> SessionOut:
    sess = await db.get(Session, session_id)
    if not sess or sess.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    if not sess.ended_at:
        sess.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        await db.refresh(sess)
    return SessionOut.model_validate(sess, from_attributes=True)


@router.post("/{session_id}/extract", response_model=ExtractOut)
async def extract_graph(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(_db_session),
) -> ExtractOut:
    """
    Run knowledge graph extraction for all segments in this session.
    Requires the session to have a person_id. Idempotent — safe to call multiple times.
    """
    from app.services.graph_extraction import extract_and_store_for_session

    sess = await db.get(Session, session_id)
    if not sess or sess.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")

    if not sess.person_id and not user.wearer_person_id:
        raise HTTPException(
            status_code=422,
            detail="No person linked to this session. Enroll your voice first, or run a session with an identified contact.",
        )

    total = 0

    # Extract from the other speaker's segments (if a contact was identified)
    if sess.person_id:
        total += await extract_and_store_for_session(
            session_id=session_id,
            person_id=sess.person_id,
            user_id=user.id,
            db=db,
            speaker_role_filter="other",
        )

    # Extract from the wearer's own segments
    if user.wearer_person_id:
        total += await extract_and_store_for_session(
            session_id=session_id,
            person_id=user.wearer_person_id,
            user_id=user.id,
            db=db,
            speaker_role_filter="wearer",
        )

    return ExtractOut(session_id=session_id, triples_stored=total)
