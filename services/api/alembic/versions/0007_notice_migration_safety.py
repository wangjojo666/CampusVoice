"""Harden notice impact migration generations, receipts, and recommendations.

Revision ID: 0007_notice_migration_safety
Revises: 0006_notice_impact_migrations
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_notice_migration_safety"
down_revision: str | None = "0006_notice_impact_migrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _collapse_plan_generations_for_downgrade() -> None:
    """Retain the latest plan when returning to the single-generation schema."""

    plans = sa.table(
        "impact_migration_plans",
        sa.column("id", sa.String(64)),
        sa.column("user_id", sa.String(64)),
        sa.column("change_set_id", sa.String(64)),
        sa.column("generation", sa.Integer()),
    )
    impacts = sa.table(
        "impact_cases",
        sa.column("migration_plan_id", sa.String(64)),
    )
    items = sa.table(
        "impact_migration_items",
        sa.column("plan_id", sa.String(64)),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            plans.c.id,
            plans.c.user_id,
            plans.c.change_set_id,
            plans.c.generation,
        ).order_by(
            plans.c.user_id,
            plans.c.change_set_id,
            plans.c.generation.desc(),
            plans.c.id.desc(),
        )
    ).all()

    latest_by_change_set: dict[tuple[str, str], str] = {}
    superseded: list[tuple[str, str]] = []
    for plan_id, user_id, change_set_id, _generation in rows:
        key = (str(user_id), str(change_set_id))
        latest_id = latest_by_change_set.setdefault(key, str(plan_id))
        if latest_id != plan_id:
            superseded.append((str(plan_id), latest_id))

    # Revision 0006 can store only one plan per user/change-set pair. Preserve
    # the latest generation and keep impact links valid rather than failing the
    # batch-table copy with an opaque uniqueness error.
    for superseded_id, latest_id in superseded:
        connection.execute(
            impacts.update()
            .where(impacts.c.migration_plan_id == superseded_id)
            .values(migration_plan_id=latest_id)
        )
        connection.execute(items.delete().where(items.c.plan_id == superseded_id))
        connection.execute(plans.delete().where(plans.c.id == superseded_id))


def upgrade() -> None:
    with op.batch_alter_table("impact_cases") as batch:
        batch.add_column(
            sa.Column("recommended_action", sa.String(24), nullable=False, server_default="apply")
        )
        batch.add_column(
            sa.Column(
                "requires_manual_review", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.create_check_constraint(
            "impact_recommended_action_valid",
            "recommended_action IN ('apply', 'keep', 'cancel', 'manual_review')",
        )
        batch.create_check_constraint(
            "impact_manual_review_flag_consistent",
            "recommended_action != 'manual_review' OR requires_manual_review = true",
        )

    # Rows created before recommendation semantics existed cannot be proven
    # safe to auto-apply. Keep the defaults for new rows, but conservatively
    # require a fresh review for every legacy impact.
    impact_cases = sa.table(
        "impact_cases",
        sa.column("recommended_action", sa.String(24)),
        sa.column("requires_manual_review", sa.Boolean()),
    )
    op.execute(
        impact_cases.update().values(
            recommended_action="manual_review",
            requires_manual_review=True,
        )
    )

    with op.batch_alter_table("impact_migration_plans") as batch:
        batch.drop_constraint("uq_migration_plan_user_change_set", type_="unique")
        batch.add_column(sa.Column("generation", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(
            sa.Column("execute_receipt_json", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(
            sa.Column("undo_receipt_json", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.create_check_constraint(
            "migration_plan_generation_positive",
            "generation > 0",
        )
        batch.create_unique_constraint(
            "uq_migration_plan_user_change_set_generation",
            ["user_id", "change_set_id", "generation"],
        )
        batch.create_unique_constraint("uq_migration_undo_key", ["user_id", "undo_idempotency_key"])

    with op.batch_alter_table("impact_migration_items") as batch:
        batch.add_column(
            sa.Column("execute_verification_json", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(
            sa.Column("undo_verification_json", sa.JSON(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    _collapse_plan_generations_for_downgrade()

    with op.batch_alter_table("impact_migration_items") as batch:
        batch.drop_column("undo_verification_json")
        batch.drop_column("execute_verification_json")

    with op.batch_alter_table("impact_migration_plans") as batch:
        batch.drop_constraint("uq_migration_undo_key", type_="unique")
        batch.drop_constraint("uq_migration_plan_user_change_set_generation", type_="unique")
        batch.drop_constraint("migration_plan_generation_positive", type_="check")
        batch.create_unique_constraint(
            "uq_migration_plan_user_change_set", ["user_id", "change_set_id"]
        )
        batch.drop_column("undo_receipt_json")
        batch.drop_column("execute_receipt_json")
        batch.drop_column("generation")

    with op.batch_alter_table("impact_cases") as batch:
        batch.drop_constraint("impact_manual_review_flag_consistent", type_="check")
        batch.drop_constraint("impact_recommended_action_valid", type_="check")
        batch.drop_column("requires_manual_review")
        batch.drop_column("recommended_action")
