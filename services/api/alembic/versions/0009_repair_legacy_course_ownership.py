"""Repair legacy cross-user and dangling course references.

Revision ID: 0009_repair_legacy_course_ownership
Revises: 0008_notice_current_and_receipt_repair
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_repair_legacy_course_ownership"
down_revision: str | None = "0008_notice_current_and_receipt_repair"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _clear_invalid_course_references(table: str) -> None:
    op.execute(
        sa.text(
            f"""
            UPDATE {table}
            SET course_id = NULL
            WHERE course_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM courses
                  WHERE courses.id = {table}.course_id
                    AND courses.user_id = {table}.user_id
              )
            """
        )
    )


def upgrade() -> None:
    for table in ("tasks", "calendar_events"):
        _clear_invalid_course_references(table)


def downgrade() -> None:
    # Invalid tenant references are intentionally not restorable. The legacy
    # free-text course field is preserved by upgrade and remains available.
    pass
