"""Replace Web Push subscriptions with Discord reminder state

Revision ID: a1b2c3d4e5f6
Revises: f421ac95d0f9
Create Date: 2026-06-22 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f421ac95d0f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "review_reminder_state",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_sent_target_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.drop_index("ix_push_subscriptions_next_reminder_at", table_name="push_subscriptions")
    op.drop_index("ix_push_subscriptions_enabled", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")


def downgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("next_reminder_at", sa.DateTime(), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_sent_target_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("endpoint"),
    )
    op.create_index("ix_push_subscriptions_enabled", "push_subscriptions", ["enabled"], unique=False)
    op.create_index("ix_push_subscriptions_next_reminder_at", "push_subscriptions", ["next_reminder_at"], unique=False)
    op.drop_table("review_reminder_state")
