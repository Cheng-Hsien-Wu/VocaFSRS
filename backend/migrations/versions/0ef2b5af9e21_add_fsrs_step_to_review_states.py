"""add fsrs step to review states

Revision ID: 0ef2b5af9e21
Revises: 9f2c4b1a6e0d
Create Date: 2026-06-20 10:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0ef2b5af9e21'
down_revision: Union[str, Sequence[str], None] = '9f2c4b1a6e0d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('review_states', sa.Column('step', sa.Integer(), nullable=True))

    # Existing rows were created before FSRS Card.step was persisted.  Infer the
    # current learning step from the scheduled learning interval so already
    # tested cards can graduate to Review on their next successful recall.
    op.execute(
        """
        UPDATE review_states
        SET step = CASE
            WHEN state = 1
                 AND due IS NOT NULL
                 AND last_review IS NOT NULL
                 AND ((julianday(due) - julianday(last_review)) * 86400.0) >= 540.0
                THEN 1
            WHEN state IN (1, 3)
                THEN 0
            ELSE NULL
        END
        WHERE step IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column('review_states', 'step')
