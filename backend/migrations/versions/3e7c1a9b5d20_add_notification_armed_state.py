"""Add threshold notification armed state.

Revision ID: 3e7c1a9b5d20
Revises: f2a7c9e1d4b6
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3e7c1a9b5d20"
down_revision: Union[str, Sequence[str], None] = "f2a7c9e1d4b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "review_reminder_state",
        sa.Column(
            "notification_armed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.execute(
        """
        UPDATE review_reminder_state
        SET notification_armed = CASE WHEN last_sent_at IS NULL THEN 1 ELSE 0 END
        """
    )


def downgrade() -> None:
    op.drop_column("review_reminder_state", "notification_armed")
