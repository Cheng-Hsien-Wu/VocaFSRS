"""add activation queue and import row indexes

Revision ID: 6b2f01a8d9c3
Revises: 4d0f6f0a9c42
Create Date: 2026-06-20 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "6b2f01a8d9c3"
down_revision: Union[str, Sequence[str], None] = "4d0f6f0a9c42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM activation_queue
        WHERE rowid NOT IN (
            SELECT rowid
            FROM (
                SELECT
                    rowid,
                    ROW_NUMBER() OVER (
                        PARTITION BY card_id
                        ORDER BY
                            CASE status WHEN 'pending' THEN 0 WHEN 'activated' THEN 1 WHEN 'skipped' THEN 2 ELSE 3 END,
                            priority DESC,
                            COALESCE(updated_at, created_at) DESC,
                            rowid DESC
                    ) AS rn
                FROM activation_queue
            )
            WHERE rn = 1
        )
        """
    )
    op.create_index("ix_activation_queue_card_id", "activation_queue", ["card_id"], unique=True)
    op.create_index("ix_import_row_results_job_row", "import_row_results", ["import_job_id", "row_index"])
    op.create_index("ix_import_row_results_job_classification", "import_row_results", ["import_job_id", "classification"])
    op.create_index("ix_import_row_results_job_action", "import_row_results", ["import_job_id", "action"])


def downgrade() -> None:
    op.drop_index("ix_import_row_results_job_action", table_name="import_row_results")
    op.drop_index("ix_import_row_results_job_classification", table_name="import_row_results")
    op.drop_index("ix_import_row_results_job_row", table_name="import_row_results")
    op.drop_index("ix_activation_queue_card_id", table_name="activation_queue")
