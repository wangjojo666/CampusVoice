"""Repair notice current heads and legacy migration receipts.

Revision ID: 0008_notice_current_and_receipt_repair
Revises: 0007_notice_migration_safety
Create Date: 2026-07-15
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic.util import CommandError

from alembic import op

revision: str = "0008_notice_current_and_receipt_repair"
down_revision: str | None = "0007_notice_migration_safety"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OFFLINE_DIALECTS = {"postgresql", "sqlite"}


def _calendar_bounds(value: str) -> str:
    year = f"CAST(substr({value}, 1, 4) AS INTEGER)"
    month = f"CAST(substr({value}, 6, 2) AS INTEGER)"
    day = f"CAST(substr({value}, 9, 2) AS INTEGER)"
    hour = f"CAST(substr({value}, 12, 2) AS INTEGER)"
    minute = f"CAST(substr({value}, 15, 2) AS INTEGER)"
    second = f"CAST(substr({value}, 18, 2) AS INTEGER)"
    offset_hour = f"CAST(substr({value}, length({value}) - 4, 2) AS INTEGER)"
    offset_minute = f"CAST(substr({value}, length({value}) - 1, 2) AS INTEGER)"
    last_day = (
        f"CASE {month} "
        "WHEN 2 THEN CASE "
        f"WHEN ({year} % 400 = 0 OR ({year} % 4 = 0 AND {year} % 100 != 0)) "
        "THEN 29 ELSE 28 END "
        f"WHEN 4 THEN 30 WHEN 6 THEN 30 WHEN 9 THEN 30 WHEN 11 THEN 30 ELSE 31 END"
    )
    return (
        f"{year} BETWEEN 1 AND 9999 "
        f"AND {month} BETWEEN 1 AND 12 "
        f"AND {day} BETWEEN 1 AND ({last_day}) "
        f"AND {hour} BETWEEN 0 AND 23 "
        f"AND {minute} BETWEEN 0 AND 59 "
        f"AND {second} BETWEEN 0 AND 59 "
        f"AND {offset_hour} BETWEEN 0 AND 23 "
        f"AND {offset_minute} BETWEEN 0 AND 59"
    )


def _sqlite_iso_datetime(value: str) -> str:
    digit_parts = (
        f"substr({value}, 1, 4) NOT GLOB '*[^0-9]*'",
        f"substr({value}, 6, 2) NOT GLOB '*[^0-9]*'",
        f"substr({value}, 9, 2) NOT GLOB '*[^0-9]*'",
        f"substr({value}, 12, 2) NOT GLOB '*[^0-9]*'",
        f"substr({value}, 15, 2) NOT GLOB '*[^0-9]*'",
        f"substr({value}, 18, 2) NOT GLOB '*[^0-9]*'",
        f"substr({value}, length({value}) - 4, 2) NOT GLOB '*[^0-9]*'",
        f"substr({value}, length({value}) - 1, 2) NOT GLOB '*[^0-9]*'",
    )
    shape = (
        f"length({value}) BETWEEN 25 AND 32 "
        f"AND substr({value}, 5, 1) = '-' "
        f"AND substr({value}, 8, 1) = '-' "
        f"AND substr({value}, 11, 1) = 'T' "
        f"AND substr({value}, 14, 1) = ':' "
        f"AND substr({value}, 17, 1) = ':' "
        f"AND substr({value}, length({value}) - 2, 1) = ':' "
        f"AND substr({value}, length({value}) - 5, 1) IN ('+', '-') "
        f"AND (length({value}) = 25 OR ("
        f"length({value}) BETWEEN 27 AND 32 "
        f"AND substr({value}, 20, 1) = '.' "
        f"AND substr({value}, 21, length({value}) - 26) NOT GLOB '*[^0-9]*'"
        "))"
    )
    return f"({shape} AND {' AND '.join(digit_parts)} AND {_calendar_bounds(value)})"


def _postgresql_iso_datetime(value: str) -> str:
    canonical_shape = (
        "'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        "([.][0-9]{1,6})?[+-][0-9]{2}:[0-9]{2}$'"
    )
    return (
        f"(CASE WHEN {value} ~ ({canonical_shape}) THEN ({_calendar_bounds(value)}) ELSE false END)"
    )


def _sqlite_receipt_predicate(
    target: str,
    operation: str,
    *,
    receipt_type: str,
) -> str:
    source = "verification_json"
    verified_at = f"json_extract({source}, '$.verified_at')"
    common = [
        f"json_type({source}) = 'object'",
        f"NOT EXISTS (SELECT 1 FROM json_each({source}) GROUP BY key HAVING count(*) > 1)",
        f"json_type({source}, '$.operation') = 'text'",
        f"json_extract({source}, '$.operation') = '{operation}'",
        f"json_type({source}, '$.verified') IN ('true', 'false')",
        f"json_type({source}, '$.verified_at') = 'text'",
        _sqlite_iso_datetime(verified_at),
        f"json_type({target}) = 'object'",
        f"json({target}) = '{{}}'",
    ]
    if receipt_type == "plan":
        verified_count = f"({source} -> '$.verified_count')"
        total_count = f"({source} -> '$.total_count')"
        common.extend(
            [
                f"json_type({source}, '$.verified_count') = 'integer'",
                f"json_type({source}, '$.total_count') = 'integer'",
                f"{verified_count} NOT GLOB '-*'",
                f"{total_count} NOT GLOB '-*'",
                f"(length({verified_count}) < length({total_count}) OR ("
                f"length({verified_count}) = length({total_count}) "
                f"AND {verified_count} <= {total_count} COLLATE BINARY))",
                f"json_type({source}, '$.status') = 'text'",
                f"length(json_extract({source}, '$.status')) > 0",
            ]
        )
    else:
        common.extend(
            [
                f"json_type({source}, '$.expected_snapshot') = 'object'",
                f"json_type({source}, '$.database_snapshot') = 'object'",
            ]
        )
    predicate = " AND\n        ".join(common)
    return (
        "CASE\n"
        f"        WHEN json_valid({source}) = 1 AND json_valid({target}) = 1\n"
        f"        THEN ({predicate})\n"
        "        ELSE 0\n"
        "    END"
    )


def _postgresql_receipt_predicate(
    target: str,
    operation: str,
    *,
    receipt_type: str,
) -> str:
    source = "verification_json"
    verified_at = f"({source} ->> 'verified_at')"
    common = [
        f"json_typeof({source}) = 'object'",
        "NOT EXISTS (SELECT 1 FROM json_object_keys("
        f"CASE WHEN json_typeof({source}) = 'object' "
        f"THEN {source} ELSE CAST('{{}}' AS json) END"
        ") AS receipt_keys(receipt_key) "
        "GROUP BY receipt_key HAVING count(*) > 1)",
        f"json_typeof({source} -> 'operation') = 'string'",
        f"{source} ->> 'operation' = '{operation}'",
        f"json_typeof({source} -> 'verified') = 'boolean'",
        f"json_typeof({source} -> 'verified_at') = 'string'",
        _postgresql_iso_datetime(verified_at),
        f"json_typeof({target}) = 'object'",
        "NOT EXISTS (SELECT 1 FROM json_object_keys("
        f"CASE WHEN json_typeof({target}) = 'object' "
        f"THEN {target} ELSE CAST('{{}}' AS json) END))",
    ]
    if receipt_type == "plan":
        verified_count = f"({source} ->> 'verified_count')"
        total_count = f"({source} ->> 'total_count')"
        common.extend(
            [
                f"json_typeof({source} -> 'verified_count') = 'number'",
                f"json_typeof({source} -> 'total_count') = 'number'",
                f"{verified_count} ~ '^(0|[1-9][0-9]*)$'",
                f"{total_count} ~ '^(0|[1-9][0-9]*)$'",
                f"(length({verified_count}) < length({total_count}) OR ("
                f"length({verified_count}) = length({total_count}) "
                f'AND {verified_count} COLLATE "C" <= {total_count} COLLATE "C"))',
                f"json_typeof({source} -> 'status') = 'string'",
                f"length({source} ->> 'status') > 0",
            ]
        )
    else:
        common.extend(
            [
                f"json_typeof({source} -> 'expected_snapshot') = 'object'",
                f"json_typeof({source} -> 'database_snapshot') = 'object'",
            ]
        )
    return " AND\n    ".join(common)


def _repair_receipts_offline(dialect: str) -> None:
    specifications = (
        ("impact_migration_plans", "execute_receipt_json", "execute", "plan"),
        ("impact_migration_plans", "undo_receipt_json", "undo", "plan"),
        ("impact_migration_items", "execute_verification_json", "execute", "item"),
        ("impact_migration_items", "undo_verification_json", "undo", "item"),
    )
    predicate_factory = (
        _sqlite_receipt_predicate if dialect == "sqlite" else _postgresql_receipt_predicate
    )
    for table, target, operation, receipt_type in specifications:
        predicate = predicate_factory(target, operation, receipt_type=receipt_type)
        op.execute(
            sa.text(
                f"""
                UPDATE {table}
                SET {target} = verification_json
                WHERE {predicate}
                """
            )
        )


def _json_object(raw: object) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _has_valid_operation(value: dict[str, Any]) -> bool:
    return value.get("operation") in {"execute", "undo"}


def _is_plan_receipt(value: dict[str, Any]) -> bool:
    if not _has_valid_operation(value):
        return False
    verified = value.get("verified")
    verified_count = value.get("verified_count")
    total_count = value.get("total_count")
    status = value.get("status")
    verified_at = value.get("verified_at")
    if (
        type(verified) is not bool
        or type(verified_count) is not int
        or type(total_count) is not int
        or verified_count < 0
        or total_count < 0
        or verified_count > total_count
        or not isinstance(status, str)
        or not status
        or not isinstance(verified_at, str)
    ):
        return False
    try:
        datetime.fromisoformat(verified_at)
    except ValueError:
        return False
    return True


def _is_item_receipt(value: dict[str, Any]) -> bool:
    if not _has_valid_operation(value):
        return False
    verified_at = value.get("verified_at")
    if (
        type(value.get("verified")) is not bool
        or not isinstance(verified_at, str)
        or not isinstance(value.get("expected_snapshot"), dict)
        or not isinstance(value.get("database_snapshot"), dict)
    ):
        return False
    try:
        datetime.fromisoformat(verified_at)
    except ValueError:
        return False
    return True


def _repair_receipts() -> None:
    plans = sa.table(
        "impact_migration_plans",
        sa.column("id", sa.String(64)),
        sa.column("verification_json", sa.JSON()),
        sa.column("execute_receipt_json", sa.JSON()),
        sa.column("undo_receipt_json", sa.JSON()),
    )
    items = sa.table(
        "impact_migration_items",
        sa.column("id", sa.String(64)),
        sa.column("verification_json", sa.JSON()),
        sa.column("execute_verification_json", sa.JSON()),
        sa.column("undo_verification_json", sa.JSON()),
    )
    connection = op.get_bind()

    plan_rows = connection.execute(
        sa.select(
            plans.c.id,
            sa.cast(plans.c.verification_json, sa.Text),
            sa.cast(plans.c.execute_receipt_json, sa.Text),
            sa.cast(plans.c.undo_receipt_json, sa.Text),
        )
    ).all()
    for plan_id, legacy_raw, execute_raw, undo_raw in plan_rows:
        legacy = _json_object(legacy_raw)
        if legacy is None or not _is_plan_receipt(legacy):
            continue
        operation = str(legacy["operation"])
        target_raw = execute_raw if operation == "execute" else undo_raw
        if _json_object(target_raw) != {}:
            continue
        target = (
            plans.c.execute_receipt_json if operation == "execute" else plans.c.undo_receipt_json
        )
        connection.execute(plans.update().where(plans.c.id == plan_id).values({target: legacy}))

    item_rows = connection.execute(
        sa.select(
            items.c.id,
            sa.cast(items.c.verification_json, sa.Text),
            sa.cast(items.c.execute_verification_json, sa.Text),
            sa.cast(items.c.undo_verification_json, sa.Text),
        )
    ).all()
    for item_id, legacy_raw, execute_raw, undo_raw in item_rows:
        legacy = _json_object(legacy_raw)
        if legacy is None or not _is_item_receipt(legacy):
            continue
        operation = str(legacy["operation"])
        target_raw = execute_raw if operation == "execute" else undo_raw
        if _json_object(target_raw) != {}:
            continue
        target = (
            items.c.execute_verification_json
            if operation == "execute"
            else items.c.undo_verification_json
        )
        connection.execute(items.update().where(items.c.id == item_id).values({target: legacy}))


def upgrade() -> None:
    migration_context = op.get_context()
    offline_dialect: str | None = None
    if migration_context.as_sql:
        offline_dialect = migration_context.dialect.name
        if offline_dialect not in _OFFLINE_DIALECTS:
            raise CommandError(
                "Revision 0008 offline SQL supports only SQLite and PostgreSQL; "
                f"received dialect {offline_dialect!r}"
            )
    if migration_context.dialect.name == "postgresql":
        # Alembic creates version_num as VARCHAR(32), but this published
        # revision identifier is longer. Widen before Alembic stamps 0008.
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(32),
            type_=sa.Text(),
            existing_nullable=False,
        )

    # Legacy databases can contain more than one current row. Select a single
    # deterministic head before adding the invariant that prevents recurrence.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY series_id
                           ORDER BY
                               CASE WHEN revision_number IS NULL THEN 1 ELSE 0 END,
                               revision_number DESC,
                               created_at DESC,
                               id DESC
                       ) AS position
                FROM documents
                WHERE series_id IS NOT NULL
            )
            UPDATE documents
            SET is_current = CASE
                WHEN id IN (SELECT id FROM ranked WHERE position = 1) THEN true
                ELSE false
            END
            WHERE series_id IS NOT NULL
            """
        )
    )
    op.create_index(
        "uq_documents_series_current",
        "documents",
        ["series_id"],
        unique=True,
        sqlite_where=sa.text("series_id IS NOT NULL AND is_current = 1"),
        postgresql_where=sa.text("series_id IS NOT NULL AND is_current"),
    )
    if offline_dialect is None:
        _repair_receipts()
    else:
        _repair_receipts_offline(offline_dialect)


def downgrade() -> None:
    # Receipt copies are intentionally retained: downgrade must not destroy
    # recoverable execution history in columns already present in revision 0007.
    op.drop_index("uq_documents_series_current", table_name="documents")
