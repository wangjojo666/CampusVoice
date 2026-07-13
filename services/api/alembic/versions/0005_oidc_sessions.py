"""Add server-side OIDC login transactions and sessions.

Revision ID: 0005_oidc_sessions
Revises: 0004_write_challenges
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_oidc_sessions"
down_revision: str | None = "0004_write_challenges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oidc_login_transactions",
        sa.Column("flow_hash", sa.String(length=64), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("flow_hash", name="pk_oidc_login_transactions"),
        sa.UniqueConstraint("state_hash", name="uq_oidc_login_transactions_state_hash"),
    )
    op.create_index(
        "ix_oidc_login_transactions_expires",
        "oidc_login_transactions",
        ["expires_at"],
    )

    op.create_table(
        "oidc_sessions",
        sa.Column("session_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("roles", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_oidc_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_hash", name="pk_oidc_sessions"),
    )
    op.create_index("ix_oidc_sessions_expires", "oidc_sessions", ["expires_at"])
    op.create_index(
        "ix_oidc_sessions_user_expires",
        "oidc_sessions",
        ["user_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_oidc_sessions_user_expires", table_name="oidc_sessions")
    op.drop_index("ix_oidc_sessions_expires", table_name="oidc_sessions")
    op.drop_table("oidc_sessions")
    op.drop_index(
        "ix_oidc_login_transactions_expires",
        table_name="oidc_login_transactions",
    )
    op.drop_table("oidc_login_transactions")
