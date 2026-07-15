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

from alembic import op

revision: str = "0008_notice_current_and_receipt_repair"
down_revision: str | None = "0007_notice_migration_safety"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    _repair_receipts()


def downgrade() -> None:
    # Receipt copies are intentionally retained: downgrade must not destroy
    # recoverable execution history in columns already present in revision 0007.
    op.drop_index("uq_documents_series_current", table_name="documents")
