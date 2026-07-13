"""Create the frozen v0.1 CampusVoice persistence schema.

Revision ID: 0001_initial_schema
Revises: None
Create Date: 2026-07-12

This revision is an immutable snapshot of the schema published in commit
32f033d.  It deliberately contains no v0.2 tables and never imports runtime
ORM metadata.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )

    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("major", sa.String(length=120), nullable=True),
        sa.Column("grade", sa.String(length=40), nullable=True),
        sa.Column("current_courses", sa.JSON(), nullable=False),
        sa.Column("teacher_names", sa.JSON(), nullable=False),
        sa.Column("default_reminder_minutes", sa.Integer(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("asr_model_config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "default_reminder_minutes >= 0",
            name="ck_user_settings_default_reminder_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_settings_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_settings"),
    )

    op.create_table(
        "hotwords",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("term", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=11), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "category IN ('course', 'course_code', 'teacher', 'ai_term', 'document', 'custom')",
            name="ck_hotwords_hotword_category",
        ),
        sa.CheckConstraint("length(trim(term)) > 0", name="ck_hotwords_term_not_blank"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_hotwords_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hotwords"),
        sa.UniqueConstraint(
            "user_id",
            "term",
            "category",
            name="uq_hotwords_user_term_category",
        ),
    )
    op.create_index("ix_hotwords_user_active", "hotwords", ["user_id", "is_active"])

    op.create_table(
        "courses",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("teacher", sa.String(length=120), nullable=True),
        sa.Column("term", sa.String(length=80), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_courses_name_not_blank"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_courses_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_courses"),
        sa.UniqueConstraint("user_id", "code", "term", name="uq_courses_user_code_term"),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("department", sa.String(length=160), nullable=True),
        sa.Column("publish_date", sa.Date(), nullable=True),
        sa.Column("applicable_group", sa.String(length=240), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("version", sa.String(length=80), nullable=True),
        sa.Column("file_type", sa.String(length=32), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('uploaded', 'processing', 'ready', 'failed')",
            name="ck_documents_document_status",
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_documents_title_not_blank"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_documents_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("user_id", "content_sha256", name="uq_documents_user_sha256"),
    )
    op.create_index(
        "ix_documents_user_publish_date",
        "documents",
        ["user_id", "publish_date"],
    )

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_document_chunks_ordinal_non_negative"),
        sa.CheckConstraint(
            "page_number IS NULL OR page_number > 0",
            name="ck_document_chunks_page_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_chunks_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
        sa.UniqueConstraint(
            "document_id",
            "ordinal",
            name="uq_document_chunks_document_ordinal",
        ),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("course_id", sa.String(length=64), nullable=True),
        sa.Column("course", sa.String(length=160), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("reminder_at", sa.DateTime(), nullable=True),
        sa.Column("priority", sa.String(length=6), nullable=False),
        sa.Column("status", sa.String(length=11), nullable=False),
        sa.Column("source_type", sa.String(length=8), nullable=False),
        sa.Column("source_document_id", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "priority IN ('low', 'medium', 'high')",
            name="ck_tasks_task_priority",
        ),
        sa.CheckConstraint(
            "source_type IN ('manual', 'voice', 'document', 'system')",
            name="ck_tasks_task_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'cancelled')",
            name="ck_tasks_task_status",
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_tasks_title_not_blank"),
        sa.CheckConstraint("version > 0", name="ck_tasks_version_positive"),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_tasks_course_id_courses",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["documents.id"],
            name="fk_tasks_source_document_id_documents",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_tasks_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tasks"),
    )
    op.create_index(
        "ix_tasks_user_status_due",
        "tasks",
        ["user_id", "status", "due_at"],
    )

    op.create_table(
        "calendar_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("course_id", sa.String(length=64), nullable=True),
        sa.Column("course", sa.String(length=160), nullable=True),
        sa.Column("start_at", sa.DateTime(), nullable=False),
        sa.Column("end_at", sa.DateTime(), nullable=False),
        sa.Column("location", sa.String(length=240), nullable=True),
        sa.Column("reminder_minutes", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=8), nullable=False),
        sa.Column("source_document_id", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("end_at > start_at", name="ck_calendar_events_end_after_start"),
        sa.CheckConstraint(
            "reminder_minutes >= 0",
            name="ck_calendar_events_reminder_non_negative",
        ),
        sa.CheckConstraint(
            "source_type IN ('manual', 'voice', 'document', 'system')",
            name="ck_calendar_events_event_source_type",
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_calendar_events_title_not_blank",
        ),
        sa.CheckConstraint("version > 0", name="ck_calendar_events_version_positive"),
        sa.ForeignKeyConstraint(
            ["course_id"],
            ["courses.id"],
            name="fk_calendar_events_course_id_courses",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["documents.id"],
            name="fk_calendar_events_source_document_id_documents",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_calendar_events_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_calendar_events"),
    )
    op.create_index(
        "ix_calendar_events_user_start_end",
        "calendar_events",
        ["user_id", "start_at", "end_at"],
    )

    op.create_table(
        "voice_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=9), nullable=False),
        sa.Column("asr_provider", sa.String(length=80), nullable=False),
        sa.Column("asr_model", sa.String(length=160), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("audio_reference", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_voice_sessions_duration_non_negative",
        ),
        sa.CheckConstraint(
            "status IN ('created', 'streaming', 'completed', 'failed')",
            name="ck_voice_sessions_voice_session_status",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_voice_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_voice_sessions"),
    )
    op.create_index("ix_voice_sessions_user_id", "voice_sessions", ["user_id"])

    op.create_table(
        "transcriptions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("voice_session_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_transcriptions_confidence_range",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_transcriptions_latency_non_negative",
        ),
        sa.CheckConstraint("sequence >= 0", name="ck_transcriptions_sequence_non_negative"),
        sa.ForeignKeyConstraint(
            ["voice_session_id"],
            ["voice_sessions.id"],
            name="fk_transcriptions_voice_session_id_voice_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_transcriptions"),
        sa.UniqueConstraint(
            "voice_session_id",
            "sequence",
            name="uq_transcriptions_session_sequence",
        ),
    )
    op.create_index(
        "ix_transcriptions_voice_session_id",
        "transcriptions",
        ["voice_session_id"],
    )

    op.create_table(
        "correction_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("transcription_id", sa.String(length=64), nullable=True),
        sa.Column("original_text", sa.Text(), nullable=False),
        sa.Column("corrected_text", sa.Text(), nullable=False),
        sa.Column("modifications", sa.JSON(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("user_confirmed", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_correction_records_confidence_range",
        ),
        sa.ForeignKeyConstraint(
            ["transcription_id"],
            ["transcriptions.id"],
            name="fk_correction_records_transcription_id_transcriptions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_correction_records_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_correction_records"),
    )
    op.create_index("ix_correction_records_user_id", "correction_records", ["user_id"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("active_intent", sa.String(length=80), nullable=True),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("is_closed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_conversations_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    op.create_table(
        "pending_actions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("action_type", sa.String(length=12), nullable=False),
        sa.Column("entity_type", sa.String(length=5), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("execution_options", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=28), nullable=False),
        sa.Column("risk_level", sa.String(length=6), nullable=False),
        sa.Column("risk_factors", sa.JSON(), nullable=False),
        sa.Column("missing_fields", sa.JSON(), nullable=False),
        sa.Column("ambiguities", sa.JSON(), nullable=False),
        sa.Column("blocking_reasons", sa.JSON(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("required_confirmations", sa.Integer(), nullable=False),
        sa.Column("confirmations_received", sa.Integer(), nullable=False),
        sa.Column("confirmation_history", sa.JSON(), nullable=False),
        sa.Column("confirmed_payload", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.Column("executed_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "action_type IN ('create_task', 'update_task', 'delete_task', "
            "'create_event', 'update_event', 'delete_event')",
            name="ck_pending_actions_action_type",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_pending_actions_attempt_count_non_negative",
        ),
        sa.CheckConstraint(
            "confirmations_received >= 0 AND confirmations_received <= 2",
            name="ck_pending_actions_confirmations_received_range",
        ),
        sa.CheckConstraint(
            "entity_type IN ('task', 'event')",
            name="ck_pending_actions_entity_type",
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name="ck_pending_actions_max_attempts_positive",
        ),
        sa.CheckConstraint(
            "state IN ('needs_input', 'awaiting_confirmation', "
            "'awaiting_second_confirmation', 'ready', 'executing', 'executed', "
            "'cancelled', 'failed', 'undone', 'expired')",
            name="ck_pending_actions_pending_action_state",
        ),
        sa.CheckConstraint(
            "required_confirmations >= 0 AND required_confirmations <= 2",
            name="ck_pending_actions_required_confirmations_range",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')",
            name="ck_pending_actions_risk_level",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_pending_actions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_pending_actions"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_pending_actions_user_idempotency",
        ),
    )
    op.create_index(
        "ix_pending_actions_user_state",
        "pending_actions",
        ["user_id", "state"],
    )

    op.create_table(
        "action_logs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("pending_action_id", sa.String(length=64), nullable=True),
        sa.Column("voice_session_id", sa.String(length=64), nullable=True),
        sa.Column("transcription_id", sa.String(length=64), nullable=True),
        sa.Column("action_type", sa.String(length=12), nullable=False),
        sa.Column("entity_type", sa.String(length=5), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("corrected_text", sa.Text(), nullable=True),
        sa.Column("recognized_intent", sa.String(length=80), nullable=True),
        sa.Column("extracted_slots", sa.JSON(), nullable=False),
        sa.Column("risk_level", sa.String(length=6), nullable=False),
        sa.Column("user_confirmed", sa.Boolean(), nullable=False),
        sa.Column("before_snapshot", sa.JSON(), nullable=True),
        sa.Column("after_snapshot", sa.JSON(), nullable=True),
        sa.Column("verification_result", sa.JSON(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "action_type IN ('create_task', 'update_task', 'delete_task', "
            "'create_event', 'update_event', 'delete_event')",
            name="ck_action_logs_action_log_action_type",
        ),
        sa.CheckConstraint(
            "entity_type IN ('task', 'event')",
            name="ck_action_logs_action_log_entity_type",
        ),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high')",
            name="ck_action_logs_action_log_risk_level",
        ),
        sa.ForeignKeyConstraint(
            ["pending_action_id"],
            ["pending_actions.id"],
            name="fk_action_logs_pending_action_id_pending_actions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["transcription_id"],
            ["transcriptions.id"],
            name="fk_action_logs_transcription_id_transcriptions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_action_logs_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["voice_session_id"],
            ["voice_sessions.id"],
            name="fk_action_logs_voice_session_id_voice_sessions",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_action_logs"),
    )
    op.create_index("ix_action_logs_pending_action_id", "action_logs", ["pending_action_id"])
    op.create_index(
        "ix_action_logs_user_created",
        "action_logs",
        ["user_id", "created_at"],
    )

    op.create_table(
        "undo_records",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("action_log_id", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=5), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=False),
        sa.Column("undo_action", sa.String(length=32), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(length=9), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("undone_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "entity_type IN ('task', 'event')",
            name="ck_undo_records_undo_entity_type",
        ),
        sa.CheckConstraint(
            "state IN ('available', 'undone', 'failed', 'expired')",
            name="ck_undo_records_undo_state",
        ),
        sa.ForeignKeyConstraint(
            ["action_log_id"],
            ["action_logs.id"],
            name="fk_undo_records_action_log_id_action_logs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_undo_records_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_undo_records"),
        sa.UniqueConstraint("action_log_id", name="uq_undo_records_action_log_id"),
    )
    op.create_index("ix_undo_records_user_id", "undo_records", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_undo_records_user_id", table_name="undo_records")
    op.drop_table("undo_records")

    op.drop_index("ix_action_logs_user_created", table_name="action_logs")
    op.drop_index("ix_action_logs_pending_action_id", table_name="action_logs")
    op.drop_table("action_logs")

    op.drop_index("ix_pending_actions_user_state", table_name="pending_actions")
    op.drop_table("pending_actions")

    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")

    op.drop_index("ix_correction_records_user_id", table_name="correction_records")
    op.drop_table("correction_records")

    op.drop_index("ix_transcriptions_voice_session_id", table_name="transcriptions")
    op.drop_table("transcriptions")

    op.drop_index("ix_voice_sessions_user_id", table_name="voice_sessions")
    op.drop_table("voice_sessions")

    op.drop_index("ix_calendar_events_user_start_end", table_name="calendar_events")
    op.drop_table("calendar_events")

    op.drop_index("ix_tasks_user_status_due", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")

    op.drop_index("ix_documents_user_publish_date", table_name="documents")
    op.drop_table("documents")

    op.drop_table("courses")

    op.drop_index("ix_hotwords_user_active", table_name="hotwords")
    op.drop_table("hotwords")

    op.drop_table("user_settings")
    op.drop_table("users")
