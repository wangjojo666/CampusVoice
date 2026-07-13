"""Add evidence-backed notice versions and atomic impact migrations.

Revision ID: 0006_notice_impact_migrations
Revises: 0005_oidc_sessions
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_notice_impact_migrations"
down_revision: str | None = "0005_oidc_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notice_series",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("canonical_key", sa.String(240), nullable=False),
        sa.Column("normalized_title", sa.String(240), nullable=False),
        sa.Column("department", sa.String(160), nullable=True),
        sa.Column("source_key", sa.String(240), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(trim(canonical_key)) > 0", name="notice_series_key_not_blank"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "canonical_key", name="uq_notice_series_user_key"),
    )
    op.create_index("ix_notice_series_user_updated", "notice_series", ["user_id", "updated_at"])

    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("series_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("supersedes_document_id", sa.String(64), nullable=True))
        batch.add_column(sa.Column("revision_number", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("effective_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("ingest_source", sa.String(40), nullable=False, server_default="upload")
        )
        batch.create_foreign_key(
            "fk_documents_series_id_notice_series",
            "notice_series",
            ["series_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_documents_supersedes_document_id_documents",
            "documents",
            ["supersedes_document_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_documents_series_id", ["series_id"])
        batch.create_unique_constraint(
            "uq_documents_series_revision", ["series_id", "revision_number"]
        )

    op.create_table(
        "notice_claims",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(64), nullable=False),
        sa.Column("chunk_id", sa.String(64), nullable=False),
        sa.Column("claim_key", sa.String(120), nullable=False),
        sa.Column("claim_type", sa.String(40), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("normalized_value_json", sa.JSON(), nullable=False),
        sa.Column("audience_rule_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_start", sa.Integer(), nullable=False),
        sa.Column("evidence_end", sa.Integer(), nullable=False),
        sa.Column("extractor_version", sa.String(40), nullable=False),
        sa.Column("review_state", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="notice_claim_confidence_range"
        ),
        sa.CheckConstraint("evidence_start >= 0", name="notice_claim_evidence_start_non_negative"),
        sa.CheckConstraint("evidence_end > evidence_start", name="notice_claim_evidence_range"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chunk_id"], ["document_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "claim_key",
            "extractor_version",
            name="uq_notice_claim_document_key_version",
        ),
    )
    op.create_index("ix_notice_claims_user_document", "notice_claims", ["user_id", "document_id"])

    for table in ("tasks", "calendar_events"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("source_chunk_id", sa.String(64), nullable=True))
            batch.add_column(sa.Column("source_claim_id", sa.String(64), nullable=True))
            batch.add_column(
                sa.Column("source_history", sa.JSON(), nullable=False, server_default="[]")
            )
            batch.create_foreign_key(
                f"fk_{table}_source_chunk_id_document_chunks",
                "document_chunks",
                ["source_chunk_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch.create_foreign_key(
                f"fk_{table}_source_claim_id_notice_claims",
                "notice_claims",
                ["source_claim_id"],
                ["id"],
                ondelete="SET NULL",
            )

    op.create_table(
        "notice_change_sets",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("series_id", sa.String(64), nullable=False),
        sa.Column("from_document_id", sa.String(64), nullable=False),
        sa.Column("to_document_id", sa.String(64), nullable=False),
        sa.Column("algorithm_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["series_id"], ["notice_series.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["from_document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "from_document_id",
            "to_document_id",
            "algorithm_version",
            name="uq_notice_change_set_pair_algorithm",
        ),
    )
    op.create_index(
        "ix_notice_change_sets_user_created", "notice_change_sets", ["user_id", "created_at"]
    )

    op.create_table(
        "notice_change_items",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("change_set_id", sa.String(64), nullable=False),
        sa.Column("claim_key", sa.String(120), nullable=False),
        sa.Column("change_type", sa.String(16), nullable=False),
        sa.Column("before_claim_id", sa.String(64), nullable=True),
        sa.Column("after_claim_id", sa.String(64), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("review_state", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="notice_change_confidence_range"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["change_set_id"], ["notice_change_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["before_claim_id"], ["notice_claims.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["after_claim_id"], ["notice_claims.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("change_set_id", "claim_key", name="uq_notice_change_item_set_key"),
    )
    op.create_index(
        "ix_notice_change_items_user_set", "notice_change_items", ["user_id", "change_set_id"]
    )

    op.create_table(
        "impact_cases",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("change_item_id", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(24), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("entity_version", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("current_snapshot", sa.JSON(), nullable=False),
        sa.Column("proposed_patch", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("migration_plan_id", sa.String(64), nullable=True),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["change_item_id"], ["notice_change_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "change_item_id",
            "entity_type",
            "entity_id",
            name="uq_impact_change_entity",
        ),
    )
    op.create_index("ix_impact_cases_user_status", "impact_cases", ["user_id", "status"])
    op.create_index("ix_impact_cases_migration_plan_id", "impact_cases", ["migration_plan_id"])

    op.create_table(
        "impact_migration_plans",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("change_set_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("conflicts_json", sa.JSON(), nullable=False),
        sa.Column("verification_json", sa.JSON(), nullable=False),
        sa.Column("execution_idempotency_key", sa.String(120), nullable=True),
        sa.Column("undo_idempotency_key", sa.String(120), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("undone_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("version > 0", name="migration_plan_version_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["change_set_id"], ["notice_change_sets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "change_set_id", name="uq_migration_plan_user_change_set"),
        sa.UniqueConstraint(
            "user_id", "execution_idempotency_key", name="uq_migration_execution_key"
        ),
    )
    op.create_index(
        "ix_migration_plans_user_status", "impact_migration_plans", ["user_id", "status"]
    )

    op.create_table(
        "impact_migration_items",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("plan_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(24), nullable=False),
        sa.Column("entity_id", sa.String(64), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=False),
        sa.Column("proposed_patch", sa.JSON(), nullable=False),
        sa.Column("after_snapshot", sa.JSON(), nullable=True),
        sa.Column("source_claim_ids", sa.JSON(), nullable=False),
        sa.Column("verification_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["impact_migration_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id", "entity_type", "entity_id", name="uq_migration_item_plan_entity"
        ),
    )
    op.create_index("ix_migration_items_plan", "impact_migration_items", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_migration_items_plan", table_name="impact_migration_items")
    op.drop_table("impact_migration_items")
    op.drop_index("ix_migration_plans_user_status", table_name="impact_migration_plans")
    op.drop_table("impact_migration_plans")
    op.drop_index("ix_impact_cases_migration_plan_id", table_name="impact_cases")
    op.drop_index("ix_impact_cases_user_status", table_name="impact_cases")
    op.drop_table("impact_cases")
    op.drop_index("ix_notice_change_items_user_set", table_name="notice_change_items")
    op.drop_table("notice_change_items")
    op.drop_index("ix_notice_change_sets_user_created", table_name="notice_change_sets")
    op.drop_table("notice_change_sets")

    for table in ("calendar_events", "tasks"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_source_claim_id_notice_claims", type_="foreignkey")
            batch.drop_constraint(f"fk_{table}_source_chunk_id_document_chunks", type_="foreignkey")
            batch.drop_column("source_history")
            batch.drop_column("source_claim_id")
            batch.drop_column("source_chunk_id")

    op.drop_index("ix_notice_claims_user_document", table_name="notice_claims")
    op.drop_table("notice_claims")
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_series_id")
        batch.drop_constraint("uq_documents_series_revision", type_="unique")
        batch.drop_constraint("fk_documents_supersedes_document_id_documents", type_="foreignkey")
        batch.drop_constraint("fk_documents_series_id_notice_series", type_="foreignkey")
        batch.drop_column("ingest_source")
        batch.drop_column("is_current")
        batch.drop_column("effective_at")
        batch.drop_column("revision_number")
        batch.drop_column("supersedes_document_id")
        batch.drop_column("series_id")
    op.drop_index("ix_notice_series_user_updated", table_name="notice_series")
    op.drop_table("notice_series")
