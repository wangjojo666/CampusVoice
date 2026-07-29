"""Repair legacy cross-user and dangling course references.

Revision ID: 0009_repair_legacy_course_ownership
Revises: 0008_notice_current_and_receipt_repair
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic.util import CommandError

from alembic import op

revision: str = "0009_repair_legacy_course_ownership"
down_revision: str | None = "0008_notice_current_and_receipt_repair"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUPPORTED_DIALECTS = {"postgresql", "sqlite"}


def _malformed_course_reference(table: str, dialect: str) -> str:
    if dialect == "sqlite":
        whitespace = "char(9) || char(10) || char(11) || char(12) || char(13) || ' '"
        return (
            f"typeof({table}.course_id) <> 'text' "
            f"OR length(trim({table}.course_id, {whitespace})) = 0"
        )
    whitespace = "chr(9) || chr(10) || chr(11) || chr(12) || chr(13) || ' '"
    return f"length(btrim({table}.course_id, {whitespace})) = 0"


def _clear_invalid_course_references(table: str, dialect: str) -> None:
    malformed = _malformed_course_reference(table, dialect)
    updated_at = (
        "CURRENT_TIMESTAMP" if dialect == "sqlite" else "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC')"
    )
    op.execute(
        sa.text(
            f"""
            UPDATE {table}
            SET course_id = NULL,
                version = version + 1,
                updated_at = {updated_at}
            WHERE course_id IS NOT NULL
              AND (
                  {malformed}
                  OR NOT EXISTS (
                      SELECT 1
                      FROM courses
                      WHERE courses.id = {table}.course_id
                        AND courses.user_id = {table}.user_id
                  )
              )
            """
        )
    )


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect not in _SUPPORTED_DIALECTS:
        raise CommandError(
            f"Revision 0009 supports only SQLite and PostgreSQL; received dialect {dialect!r}"
        )
    for table in ("tasks", "calendar_events"):
        _clear_invalid_course_references(table, dialect)


def downgrade() -> None:
    # Invalid tenant references are intentionally not restorable. The legacy
    # free-text course field is preserved by upgrade and remains available.
    pass
