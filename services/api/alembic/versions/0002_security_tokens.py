"""Add replay-safe confirmation nonces and one-time WebSocket tickets.

Revision ID: 0002_security_tokens
Revises: 0001_initial_schema
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_security_tokens"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "confirmation_nonces",
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("pending_action_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "stage > 0 AND stage <= 2",
            name="ck_confirmation_nonces_confirmation_stage_range",
        ),
        sa.ForeignKeyConstraint(
            ["pending_action_id"],
            ["pending_actions.id"],
            name="fk_confirmation_nonces_pending_action_id_pending_actions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_confirmation_nonces_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("nonce_hash", name="pk_confirmation_nonces"),
        sa.UniqueConstraint(
            "pending_action_id",
            "stage",
            name="uq_confirmation_nonces_action_stage",
        ),
    )
    op.create_index(
        "ix_confirmation_nonces_user_action",
        "confirmation_nonces",
        ["user_id", "pending_action_id"],
    )

    op.create_table(
        "websocket_tickets",
        sa.Column("ticket_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=500), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_websocket_tickets_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ticket_hash", name="pk_websocket_tickets"),
    )
    op.create_index(
        "ix_websocket_tickets_user_expires",
        "websocket_tickets",
        ["user_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_websocket_tickets_user_expires", table_name="websocket_tickets")
    op.drop_table("websocket_tickets")

    op.drop_index(
        "ix_confirmation_nonces_user_action",
        table_name="confirmation_nonces",
    )
    op.drop_table("confirmation_nonces")
