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

_PYTHON_STRIP_WHITESPACE_CODEPOINTS = (
    *range(0x0009, 0x000E),
    *range(0x001C, 0x0020),
    0x0020,
    0x0085,
    0x00A0,
    0x1680,
    *range(0x2000, 0x200B),
    0x2028,
    0x2029,
    0x202F,
    0x205F,
    0x3000,
)


def _strip_whitespace_sql(dialect: str) -> str:
    character_function = "char" if dialect == "sqlite" else "chr"
    return " || ".join(
        f"{character_function}({codepoint})" for codepoint in _PYTHON_STRIP_WHITESPACE_CODEPOINTS
    )


def _malformed_course_reference(table: str, dialect: str) -> str:
    whitespace = _strip_whitespace_sql(dialect)
    if dialect == "sqlite":
        return f"typeof({table}.course_id) <> 'text' OR trim({table}.course_id, {whitespace}) = ''"
    return f"btrim({table}.course_id, {whitespace}) = ''"


def _invalid_course_reference(table: str, dialect: str) -> str:
    malformed = _malformed_course_reference(table, dialect)
    return (
        f"{table}.course_id IS NOT NULL "
        "AND ("
        f"{malformed} "
        "OR NOT EXISTS ("
        "SELECT 1 "
        "FROM courses "
        f"WHERE courses.id = {table}.course_id "
        f"AND courses.user_id = {table}.user_id"
        ")"
        ")"
    )


def _assert_version_can_advance(table: str, dialect: str) -> None:
    invalid_reference = _invalid_course_reference(table, dialect)
    error_message = f"revision 0009 cannot safely advance {table}.version"
    if dialect == "sqlite":
        guard_table = f"_alembic_0009_{table}_version_guard"
        guard_constraint = f"ck_0009_{table}_version_advanceable"
        op.execute(sa.text(f"DROP TABLE IF EXISTS temp.{guard_table}"))
        op.execute(
            sa.text(
                f"""
                CREATE TEMPORARY TABLE {guard_table} (
                    is_advanceable INTEGER NOT NULL,
                    CONSTRAINT {guard_constraint} CHECK (is_advanceable = 1)
                )
                """
            )
        )
        op.execute(
            sa.text(
                f"""
                INSERT INTO {guard_table} (is_advanceable)
                SELECT CASE WHEN EXISTS (
                    SELECT 1
                    FROM {table}
                    WHERE {invalid_reference}
                      AND (
                          typeof({table}.version) <> 'integer'
                          OR {table}.version < 1
                          OR {table}.version >= 9223372036854775807
                      )
                ) THEN 0 ELSE 1 END
                """
            )
        )
        op.execute(sa.text(f"DROP TABLE {guard_table}"))
        return

    op.execute(
        sa.text(
            f"""
            DO $alembic_0009$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM {table}
                    WHERE {invalid_reference}
                      AND (
                          {table}.version < 1
                          OR {table}.version >= 2147483647
                      )
                ) THEN
                    RAISE EXCEPTION USING ERRCODE = '22003', MESSAGE = '{error_message}';
                END IF;
            END
            $alembic_0009$
            """
        )
    )


def _assert_updated_at_can_advance(table: str, dialect: str) -> None:
    invalid_reference = _invalid_course_reference(table, dialect)
    if dialect == "sqlite":
        guard_table = f"_alembic_0009_{table}_updated_at_guard"
        guard_constraint = f"ck_0009_{table}_updated_at_advanceable"
        op.execute(sa.text(f"DROP TABLE IF EXISTS temp.{guard_table}"))
        op.execute(
            sa.text(
                f"""
                CREATE TEMPORARY TABLE {guard_table} (
                    is_advanceable INTEGER NOT NULL,
                    CONSTRAINT {guard_constraint} CHECK (is_advanceable = 1)
                )
                """
            )
        )
        op.execute(
            sa.text(
                f"""
                INSERT INTO {guard_table} (is_advanceable)
                SELECT CASE WHEN EXISTS (
                    SELECT 1
                    FROM {table}
                    WHERE {invalid_reference}
                      AND (
                          julianday({table}.updated_at) IS NULL
                          OR (
                              julianday({table}.updated_at) >= julianday(CURRENT_TIMESTAMP)
                              AND datetime({table}.updated_at, '+1 second') IS NULL
                          )
                      )
                ) THEN 0 ELSE 1 END
                """
            )
        )
        op.execute(sa.text(f"DROP TABLE {guard_table}"))
        return

    error_message = f"revision 0009 cannot strictly advance {table}.updated_at"
    op.execute(
        sa.text(
            f"""
            DO $alembic_0009$
            DECLARE
                candidate RECORD;
                advanced TIMESTAMP WITHOUT TIME ZONE;
            BEGIN
                FOR candidate IN
                    SELECT {table}.updated_at
                    FROM {table}
                    WHERE {invalid_reference}
                LOOP
                    IF candidate.updated_at = 'infinity'::TIMESTAMP WITHOUT TIME ZONE
                       OR candidate.updated_at >= TIMESTAMP '9999-12-31 23:59:59.999999'
                    THEN
                        RAISE EXCEPTION USING ERRCODE = '22008', MESSAGE = '{error_message}';
                    END IF;
                    IF candidate.updated_at >= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC') THEN
                        BEGIN
                            advanced := candidate.updated_at + INTERVAL '1 microsecond';
                        EXCEPTION WHEN datetime_field_overflow THEN
                            RAISE EXCEPTION USING ERRCODE = '22008', MESSAGE = '{error_message}';
                        END;
                        IF advanced <= candidate.updated_at THEN
                            RAISE EXCEPTION USING ERRCODE = '22008', MESSAGE = '{error_message}';
                        END IF;
                    END IF;
                END LOOP;
            END
            $alembic_0009$
            """
        )
    )


def _advanced_updated_at(table: str, dialect: str) -> str:
    if dialect == "sqlite":
        return (
            "CASE "
            f"WHEN julianday({table}.updated_at) >= julianday(CURRENT_TIMESTAMP) "
            f"THEN datetime({table}.updated_at, '+1 second') "
            "ELSE CURRENT_TIMESTAMP "
            "END"
        )
    return (
        "GREATEST("
        "(CURRENT_TIMESTAMP AT TIME ZONE 'UTC'), "
        f"{table}.updated_at + INTERVAL '1 microsecond'"
        ")"
    )


def _clear_invalid_course_references(table: str, dialect: str) -> None:
    invalid_reference = _invalid_course_reference(table, dialect)
    updated_at = _advanced_updated_at(table, dialect)
    op.execute(
        sa.text(
            f"""
            UPDATE {table}
            SET course_id = NULL,
                version = version + 1,
                updated_at = {updated_at}
            WHERE {invalid_reference}
            """
        )
    )


def upgrade() -> None:
    dialect = op.get_context().dialect.name
    if dialect not in _SUPPORTED_DIALECTS:
        raise CommandError(
            f"Revision 0009 supports only SQLite and PostgreSQL; received dialect {dialect!r}"
        )
    if dialect == "postgresql":
        # Freeze both the ownership predicate and repair targets. NOWAIT makes
        # concurrent writers/row lockers an explicit, retryable migration failure.
        op.execute(sa.text("LOCK TABLE courses, tasks, calendar_events IN EXCLUSIVE MODE NOWAIT"))
    for table in ("tasks", "calendar_events"):
        _assert_version_can_advance(table, dialect)
        _assert_updated_at_can_advance(table, dialect)
    for table in ("tasks", "calendar_events"):
        _clear_invalid_course_references(table, dialect)


def downgrade() -> None:
    # Invalid tenant references are intentionally not restorable. The legacy
    # free-text course field is preserved by upgrade and remains available.
    pass
