"""add_user_name_and_wearer_person

Revision ID: b4cef2acedf7
Revises: 9fbc406ce2b9
Create Date: 2026-07-01 13:53:07.614777

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b4cef2acedf7'
down_revision: Union[str, None] = '9fbc406ce2b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user', sa.Column('first_name', sa.String(), nullable=False, server_default=''))
    op.add_column('user', sa.Column('last_name', sa.String(), nullable=False, server_default=''))
    op.add_column('user', sa.Column('wearer_person_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_user_wearer_person_id',
        'user', 'person',
        ['wearer_person_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_user_wearer_person_id', 'user', type_='foreignkey')
    op.drop_column('user', 'wearer_person_id')
    op.drop_column('user', 'last_name')
    op.drop_column('user', 'first_name')
