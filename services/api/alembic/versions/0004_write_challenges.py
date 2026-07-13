"""Add request-bound one-time write challenges.

Revision ID: 0004_write_challenges
Revises: 0003_privacy_controls
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_write_challenges"
down_revision: str | None = "0003_privacy_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "write_challenges",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("flow_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("body_hash", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("required_stages", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "required_stages >= 1 AND required_stages <= 2",
            name="ck_write_challenges_write_required_stages_range",
        ),
        sa.CheckConstraint(
            "stage >= 1 AND stage <= required_stages",
            name="ck_write_challenges_write_stage_range",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_write_challenges_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name="pk_write_challenges"),
        sa.UniqueConstraint(
            "flow_id",
            "stage",
            name="uq_write_challenges_flow_stage",
        ),
    )
    op.create_index(
        "ix_write_challenges_user_expires",
        "write_challenges",
        ["user_id", "expires_at"],
    )
    op.create_index(
        "ix_write_challenges_flow_stage",
        "write_challenges",
        ["flow_id", "stage"],
    )


def downgrade() -> None:
    op.drop_index("ix_write_challenges_flow_stage", table_name="write_challenges")
    op.drop_index("ix_write_challenges_user_expires", table_name="write_challenges")
    op.drop_table("write_challenges")
