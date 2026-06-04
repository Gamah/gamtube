"""add transcoded column to videos

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-04

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("transcoded", sa.Boolean(), nullable=True))
    # All existing records were always transcoded under the old behaviour
    op.execute("UPDATE videos SET transcoded = 1 WHERE status = 'ready'")


def downgrade() -> None:
    op.drop_column("videos", "transcoded")
