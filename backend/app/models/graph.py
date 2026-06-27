import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel


class EntityEmbedding(SQLModel, table=True):
    """
    Registry of entity nodes in the AGE jarvis_kg graph.
    Each row corresponds to one AGE :Entity vertex, keyed by (user_id, entity_type, canonical_name).
    Dedup is by exact canonical_name match — Claude normalizes names during extraction.
    """

    __tablename__ = "entity_embedding"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="user.id", index=True)
    entity_type: str  # person | company | place | topic | event | misc
    canonical_name: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
