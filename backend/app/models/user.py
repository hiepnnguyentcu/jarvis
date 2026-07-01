import uuid
from datetime import datetime
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    first_name: str = Field(default="")
    last_name: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    voice_enrolled: bool = Field(default=False)
    wearer_person_id: Optional[uuid.UUID] = Field(default=None, foreign_key="person.id")


class UserVoiceEmbedding(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    embedding: Optional[Any] = Field(default=None, sa_column=Column(Vector(256)))
    created_at: datetime = Field(default_factory=datetime.utcnow)
