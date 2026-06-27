"""add_knowledge_graph

Revision ID: af056275ab6a
Revises: 13d5c7441c83
Create Date: 2026-06-24

- Drops `personfact` (flat fact store — replaced by AGE graph)
- Creates AGE extension + `jarvis_kg` property graph
- Creates `entity_embedding` bridge table (pgvector for entity dedup,
  canonical_name maps to AGE vertex)
"""
from typing import Sequence, Union

import pgvector.sqlalchemy.vector
import sqlalchemy as sa
import sqlmodel.sql.sqltypes
from alembic import op

revision: str = "af056275ab6a"
down_revision: Union[str, None] = "13d5c7441c83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. AGE graph extension ────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS age CASCADE")
    # create_graph lives in ag_catalog; scope the search_path only for this call
    # then immediately reset so subsequent DDL lands in public.
    op.execute('SET search_path = ag_catalog, "$user", public')
    op.execute("SELECT create_graph('jarvis_kg')")
    op.execute("SET search_path = public")

    # ── 2. Entity embedding bridge table ─────────────────────────────────────
    # Maps AGE vertex canonical_name → pgvector embedding for entity resolution.
    op.create_table(
        "entity_embedding",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("canonical_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(1536), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entity_embedding_user_id", "entity_embedding", ["user_id"])
    op.create_index("ix_entity_embedding_canonical_name", "entity_embedding", ["canonical_name"])

    # ── 3. Drop old flat-fact table ───────────────────────────────────────────
    op.drop_table("personfact")


def downgrade() -> None:
    op.create_table(
        "personfact",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("person_id", sa.UUID(), nullable=False),
        sa.Column("source_session_id", sa.UUID(), nullable=True),
        sa.Column("fact", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("fact_embedding", pgvector.sqlalchemy.vector.VECTOR(1536), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["person.id"]),
        sa.ForeignKeyConstraint(["source_session_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_personfact_person_id", "personfact", ["person_id"])

    op.drop_index("ix_entity_embedding_canonical_name", "entity_embedding")
    op.drop_index("ix_entity_embedding_user_id", "entity_embedding")
    op.drop_table("entity_embedding")

    op.execute('SET search_path = ag_catalog, "$user", public')
    op.execute("SELECT drop_graph('jarvis_kg', true)")
    op.execute("SET search_path = public")
    op.execute("DROP EXTENSION IF EXISTS age CASCADE")
