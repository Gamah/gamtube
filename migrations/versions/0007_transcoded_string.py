"""change transcoded column from boolean to string (yes/no/skipped)

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-04

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("videos") as batch_op:
        batch_op.alter_column(
            "transcoded",
            existing_type=sa.Boolean(),
            type_=sa.String(10),
            nullable=True,
        )
    # SQLite stored booleans as 0/1 integers; convert to string labels
    op.execute("UPDATE videos SET transcoded = 'yes' WHERE transcoded IN ('1', 1)")
    op.execute("UPDATE videos SET transcoded = 'no' WHERE transcoded IN ('0', 0)")


def downgrade() -> None:
    op.execute("UPDATE videos SET transcoded = 1 WHERE transcoded = 'yes'")
    op.execute("UPDATE videos SET transcoded = 0 WHERE transcoded IN ('no', 'skipped')")
    with op.batch_alter_table("videos") as batch_op:
        batch_op.alter_column(
            "transcoded",
            existing_type=sa.String(10),
            type_=sa.Boolean(),
            nullable=True,
        )
