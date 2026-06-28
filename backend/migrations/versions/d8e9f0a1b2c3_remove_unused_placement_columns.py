"""Remove unused placement columns.

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("activation_queue") as batch_op:
        batch_op.drop_column("source_placement_item_id")
    with op.batch_alter_table("placement_items") as batch_op:
        batch_op.drop_column("audit_correct")
        batch_op.drop_column("was_audited")
    with op.batch_alter_table("placement_sessions") as batch_op:
        batch_op.drop_column("manifest_version")


def downgrade() -> None:
    with op.batch_alter_table("placement_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("manifest_version", sa.Integer(), nullable=True),
        )
    with op.batch_alter_table("placement_items") as batch_op:
        batch_op.add_column(
            sa.Column("was_audited", sa.Boolean(), nullable=True),
        )
        batch_op.add_column(
            sa.Column("audit_correct", sa.Boolean(), nullable=True),
        )
    with op.batch_alter_table("activation_queue") as batch_op:
        batch_op.add_column(
            sa.Column(
                "source_placement_item_id",
                sa.String(),
                sa.ForeignKey(
                    "placement_items.id",
                    name="fk_activation_queue_source_placement_item_id",
                ),
                nullable=True,
            ),
        )
