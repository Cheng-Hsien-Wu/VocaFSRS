"""add typed study answers and study plan

Revision ID: 9f2c4b1a6e0d
Revises: 7f87c4301797
Create Date: 2026-06-19 14:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '9f2c4b1a6e0d'
down_revision: Union[str, Sequence[str], None] = '7f87c4301797'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'study_plan',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('target_days', sa.Integer(), nullable=False),
        sa.Column('target_end_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_table(
        'typed_study_answers',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('study_session_id', sa.String(), nullable=False),
        sa.Column('session_item_id', sa.String(), nullable=False),
        sa.Column('card_id', sa.String(), nullable=False),
        sa.Column('typed_answer', sa.String(), nullable=False),
        sa.Column('expected_answer', sa.String(), nullable=False),
        sa.Column('answered_at', sa.DateTime(), nullable=False),
        sa.Column('adjudication_status', sa.String(), nullable=False),
        sa.Column('verdict', sa.String(), nullable=True),
        sa.Column('rating', sa.String(), nullable=True),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('provider', sa.String(), nullable=True),
        sa.Column('model', sa.String(), nullable=True),
        sa.Column('error_message', sa.String(), nullable=True),
        sa.Column('next_due', sa.DateTime(), nullable=True),
        sa.Column('idempotency_key', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.ForeignKeyConstraint(['card_id'], ['cards.id']),
        sa.ForeignKeyConstraint(['session_item_id'], ['session_items.id']),
        sa.ForeignKeyConstraint(['study_session_id'], ['study_sessions.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_typed_answers_idempotency', 'typed_study_answers', ['idempotency_key'], unique=True)
    op.create_index('ix_typed_answers_session_status', 'typed_study_answers', ['study_session_id', 'adjudication_status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_typed_answers_session_status', table_name='typed_study_answers')
    op.drop_index('ix_typed_answers_idempotency', table_name='typed_study_answers')
    op.drop_table('typed_study_answers')
    op.drop_table('study_plan')
