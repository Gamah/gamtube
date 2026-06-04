"""add thumbnail_url to videos

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-04

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("thumbnail_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "thumbnail_url")
