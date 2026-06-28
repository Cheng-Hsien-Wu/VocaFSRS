"""Harden adjudication and placement audit claims.

Revision ID: c7d8e9f0a1b2
Revises: a1b2c3d4e5f6
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("typed_study_answers", sa.Column("adjudication_claim_token", sa.String(), nullable=True))
    op.add_column("typed_study_answers", sa.Column("adjudication_claimed_at", sa.DateTime(), nullable=True))
    op.execute(
        """
        DELETE FROM placement_audit_events
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM placement_audit_events
            GROUP BY placement_audit_item_id
        )
        """
    )
    op.create_index(
        "ix_placement_audit_events_item",
        "placement_audit_events",
        ["placement_audit_item_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_placement_audit_events_item", table_name="placement_audit_events")
    op.drop_column("typed_study_answers", "adjudication_claimed_at")
    op.drop_column("typed_study_answers", "adjudication_claim_token")
