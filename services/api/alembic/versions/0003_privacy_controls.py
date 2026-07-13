"""Add replay-safe privacy deletion challenges.

Revision ID: 0003_privacy_controls
Revises: 0002_security_tokens
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_privacy_controls"
down_revision: str | None = "0002_security_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "privacy_deletion_challenges",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "scope = 'business_data'",
            name="ck_privacy_deletion_challenges_privacy_scope_supported",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_privacy_deletion_challenges_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_privacy_deletion_challenges"),
        sa.UniqueConstraint(
            "nonce_hash",
            name="uq_privacy_deletion_challenges_nonce_hash",
        ),
    )
    op.create_index(
        "ix_privacy_deletion_challenges_user_expires",
        "privacy_deletion_challenges",
        ["user_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_privacy_deletion_challenges_user_expires",
        table_name="privacy_deletion_challenges",
    )
    op.drop_table("privacy_deletion_challenges")
