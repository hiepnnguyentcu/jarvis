import uuid
from datetime import datetime
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column
from sqlmodel import Field, SQLModel


class Person(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class VoiceEmbedding(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    person_id: uuid.UUID = Field(foreign_key="person.id", index=True)
    embedding: Optional[Any] = Field(default=None, sa_column=Column(Vector(256)))
    created_at: datetime = Field(default_factory=datetime.utcnow)
