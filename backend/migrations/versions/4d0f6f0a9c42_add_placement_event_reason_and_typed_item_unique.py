"""add placement event reason and typed answer item uniqueness

Revision ID: 4d0f6f0a9c42
Revises: 0ef2b5af9e21
Create Date: 2026-06-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4d0f6f0a9c42"
down_revision: Union[str, Sequence[str], None] = "0ef2b5af9e21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("placement_events", sa.Column("problematic_reason", sa.String(), nullable=True))
    op.create_index(
        "ix_typed_answers_session_item",
        "typed_study_answers",
        ["study_session_id", "session_item_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_typed_answers_session_item", table_name="typed_study_answers")
    op.drop_column("placement_events", "problematic_reason")
