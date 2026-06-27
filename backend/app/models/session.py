import uuid
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Session(SQLModel, table=True):
    __tablename__ = "session"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    person_id: Optional[uuid.UUID] = Field(default=None, foreign_key="person.id")
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    raw_transcript: Optional[str] = None
    audio_r2_key: Optional[str] = None
    identity_confidence: Optional[float] = None


class Segment(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    session_id: uuid.UUID = Field(foreign_key="session.id", index=True)
    speaker_label: str
    speaker_role: Optional[str] = None  # 'wearer' | 'other'
    text: str
    start_ms: int
    end_ms: int
