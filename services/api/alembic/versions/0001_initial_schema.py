"""Create the initial CampusVoice persistence schema.

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-07-12

The metadata used here is the initial schema's authoritative SQLAlchemy metadata.
The table list in downgrade is deliberately explicit so downgrade order remains
safe under SQLite foreign-key enforcement.
"""

from collections.abc import Sequence

from alembic import op
from app import models  # noqa: F401
from app.db.base import Base

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=False)


def downgrade() -> None:
    for table_name in (
        "undo_records",
        "action_logs",
        "pending_actions",
        "conversations",
        "correction_records",
        "transcriptions",
        "voice_sessions",
        "calendar_events",
        "tasks",
        "document_chunks",
        "documents",
        "courses",
        "hotwords",
        "user_settings",
        "users",
    ):
        op.drop_table(table_name)
