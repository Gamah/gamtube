"""add view_count to videos

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-04

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("videos", "view_count")
