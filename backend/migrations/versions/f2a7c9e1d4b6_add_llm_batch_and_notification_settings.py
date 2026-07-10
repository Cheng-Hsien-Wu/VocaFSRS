"""Add LLM batching and notification threshold settings.

Revision ID: f2a7c9e1d4b6
Revises: e6a4c2b8d931
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a7c9e1d4b6"
down_revision: Union[str, Sequence[str], None] = "e6a4c2b8d931"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_settings",
        sa.Column("fallback_providers_json", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "llm_settings",
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="10"),
    )
    op.add_column(
        "llm_settings",
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "review_reminder_state",
        sa.Column("minimum_due_count", sa.Integer(), nullable=False, server_default="10"),
    )


def downgrade() -> None:
    op.drop_column("review_reminder_state", "minimum_due_count")
    op.drop_column("llm_settings", "max_concurrency")
    op.drop_column("llm_settings", "batch_size")
    op.drop_column("llm_settings", "fallback_providers_json")
