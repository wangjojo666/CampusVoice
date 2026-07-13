from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime, utc_now
from app.models.enums import (
    ActionType,
    DocumentStatus,
    EntityType,
    HotwordCategory,
    PendingActionState,
    RiskLevel,
    SourceType,
    TaskPriority,
    TaskStatus,
    UndoState,
    VoiceSessionStatus,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def enum_type(enum_class: type[Any], name: str) -> Enum:
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("usr"))
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UserSettings(TimestampMixin, Base):
    __tablename__ = "user_settings"
    __table_args__ = (
        CheckConstraint("default_reminder_minutes >= 0", name="default_reminder_non_negative"),
    )

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    major: Mapped[str | None] = mapped_column(String(120))
    grade: Mapped[str | None] = mapped_column(String(40))
    current_courses: Mapped[list[dict[str, Any]]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    teacher_names: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    default_reminder_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai", nullable=False)
    asr_model_config: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )


class Hotword(TimestampMixin, Base):
    __tablename__ = "hotwords"
    __table_args__ = (
        UniqueConstraint("user_id", "term", "category", name="uq_hotwords_user_term_category"),
        CheckConstraint("length(trim(term)) > 0", name="term_not_blank"),
        Index("ix_hotwords_user_active", "user_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("hot"))
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    term: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[HotwordCategory] = mapped_column(
        enum_type(HotwordCategory, "hotword_category"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(80), default="user", nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Course(TimestampMixin, Base):
    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("user_id", "code", "term", name="uq_courses_user_code_term"),
        CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("crs"))
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    teacher: Mapped[str | None] = mapped_column(String(120))
    term: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("user_id", "content_sha256", name="uq_documents_user_sha256"),
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        Index("ix_documents_user_publish_date", "user_id", "publish_date"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("doc"))
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    department: Mapped[str | None] = mapped_column(String(160))
    publish_date: Mapped[date | None]
    applicable_group: Mapped[str | None] = mapped_column(String(240))
    source_url: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(String(80))
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        enum_type(DocumentStatus, "document_status"),
        default=DocumentStatus.UPLOADED,
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_document_chunks_document_ordinal"),
        CheckConstraint("ordinal >= 0", name="ordinal_non_negative"),
        CheckConstraint("page_number IS NULL OR page_number > 0", name="page_number_positive"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("chk"))
    document_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(MutableList.as_mutable(JSON))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        CheckConstraint("version > 0", name="version_positive"),
        Index("ix_tasks_user_status_due", "user_id", "status", "due_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("tsk"))
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    course_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("courses.id", ondelete="SET NULL")
    )
    course: Mapped[str | None] = mapped_column(String(160))
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    reminder_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    priority: Mapped[TaskPriority] = mapped_column(
        enum_type(TaskPriority, "task_priority"), default=TaskPriority.MEDIUM, nullable=False
    )
    status: Mapped[TaskStatus] = mapped_column(
        enum_type(TaskStatus, "task_status"), default=TaskStatus.PENDING, nullable=False
    )
    source_type: Mapped[SourceType] = mapped_column(
        enum_type(SourceType, "task_source_type"), default=SourceType.MANUAL, nullable=False
    )
    source_document_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class CalendarEvent(TimestampMixin, Base):
    __tablename__ = "calendar_events"
    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0", name="title_not_blank"),
        CheckConstraint("end_at > start_at", name="end_after_start"),
        CheckConstraint("reminder_minutes >= 0", name="reminder_non_negative"),
        CheckConstraint("version > 0", name="version_positive"),
        Index("ix_calendar_events_user_start_end", "user_id", "start_at", "end_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("evt"))
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    course_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("courses.id", ondelete="SET NULL")
    )
    course: Mapped[str | None] = mapped_column(String(160))
    start_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    location: Mapped[str | None] = mapped_column(String(240))
    reminder_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        enum_type(SourceType, "event_source_type"), default=SourceType.MANUAL, nullable=False
    )
    source_document_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("documents.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class VoiceSession(TimestampMixin, Base):
    __tablename__ = "voice_sessions"
    __table_args__ = (
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("voi"))
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[VoiceSessionStatus] = mapped_column(
        enum_type(VoiceSessionStatus, "voice_session_status"),
        default=VoiceSessionStatus.CREATED,
        nullable=False,
    )
    asr_provider: Mapped[str] = mapped_column(String(80), nullable=False)
    asr_model: Mapped[str] = mapped_column(String(160), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    audio_reference: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)


class Transcription(Base):
    __tablename__ = "transcriptions"
    __table_args__ = (
        CheckConstraint("sequence >= 0", name="sequence_non_negative"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"
        ),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_non_negative"),
        UniqueConstraint("voice_session_id", "sequence", name="uq_transcriptions_session_sequence"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("trn"))
    voice_session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("voice_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class CorrectionRecord(Base):
    __tablename__ = "correction_records"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cor"))
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    transcription_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("transcriptions.id", ondelete="SET NULL")
    )
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_text: Mapped[str] = mapped_column(Text, nullable=False)
    modifications: Mapped[list[dict[str, Any]]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    user_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cnv"))
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    active_intent: Mapped[str | None] = mapped_column(String(80))
    context: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PendingAction(TimestampMixin, Base):
    __tablename__ = "pending_actions"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_pending_actions_user_idempotency"),
        CheckConstraint(
            "required_confirmations >= 0 AND required_confirmations <= 2",
            name="required_confirmations_range",
        ),
        CheckConstraint(
            "confirmations_received >= 0 AND confirmations_received <= 2",
            name="confirmations_received_range",
        ),
        CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        Index("ix_pending_actions_user_state", "user_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("act"))
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action_type: Mapped[ActionType] = mapped_column(
        enum_type(ActionType, "action_type"), nullable=False
    )
    entity_type: Mapped[EntityType] = mapped_column(
        enum_type(EntityType, "entity_type"), nullable=False
    )
    target_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    execution_options: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    state: Mapped[PendingActionState] = mapped_column(
        enum_type(PendingActionState, "pending_action_state"), nullable=False
    )
    risk_level: Mapped[RiskLevel] = mapped_column(
        enum_type(RiskLevel, "risk_level"), nullable=False
    )
    risk_factors: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    missing_fields: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    ambiguities: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    blocking_reasons: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    diagnostics: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    required_confirmations: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmations_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confirmation_history: Mapped[list[dict[str, Any]]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    confirmed_payload: Mapped[dict[str, Any] | None] = mapped_column(MutableDict.as_mutable(JSON))
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    executed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(MutableDict.as_mutable(JSON))


class ConfirmationNonce(Base):
    __tablename__ = "confirmation_nonces"
    __table_args__ = (
        UniqueConstraint(
            "pending_action_id",
            "stage",
            name="uq_confirmation_nonces_action_stage",
        ),
        CheckConstraint("stage > 0 AND stage <= 2", name="confirmation_stage_range"),
        Index("ix_confirmation_nonces_user_action", "user_id", "pending_action_id"),
    )

    nonce_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    pending_action_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("pending_actions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class WebSocketTicket(Base):
    __tablename__ = "websocket_tickets"
    __table_args__ = (Index("ix_websocket_tickets_user_expires", "user_id", "expires_at"),)

    ticket_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    origin: Mapped[str] = mapped_column(String(500), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class WriteChallenge(Base):
    __tablename__ = "write_challenges"
    __table_args__ = (
        UniqueConstraint("flow_id", "stage", name="uq_write_challenges_flow_stage"),
        CheckConstraint(
            "required_stages >= 1 AND required_stages <= 2",
            name="write_required_stages_range",
        ),
        CheckConstraint(
            "stage >= 1 AND stage <= required_stages",
            name="write_stage_range",
        ),
        Index("ix_write_challenges_user_expires", "user_id", "expires_at"),
        Index("ix_write_challenges_flow_stage", "flow_id", "stage"),
    )

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    flow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    required_stages: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class PrivacyDeletionChallenge(Base):
    __tablename__ = "privacy_deletion_challenges"
    __table_args__ = (
        CheckConstraint("scope = 'business_data'", name="privacy_scope_supported"),
        UniqueConstraint("nonce_hash", name="uq_privacy_deletion_challenges_nonce_hash"),
        Index("ix_privacy_deletion_challenges_user_expires", "user_id", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pdc"))
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    nonce_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class ActionLog(Base):
    __tablename__ = "action_logs"
    __table_args__ = (Index("ix_action_logs_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("log"))
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    pending_action_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("pending_actions.id", ondelete="SET NULL"), index=True
    )
    voice_session_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("voice_sessions.id", ondelete="SET NULL")
    )
    transcription_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("transcriptions.id", ondelete="SET NULL")
    )
    action_type: Mapped[ActionType] = mapped_column(
        enum_type(ActionType, "action_log_action_type"), nullable=False
    )
    entity_type: Mapped[EntityType] = mapped_column(
        enum_type(EntityType, "action_log_entity_type"), nullable=False
    )
    target_id: Mapped[str | None] = mapped_column(String(64))
    source_text: Mapped[str | None] = mapped_column(Text)
    corrected_text: Mapped[str | None] = mapped_column(Text)
    recognized_intent: Mapped[str | None] = mapped_column(String(80))
    extracted_slots: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    risk_level: Mapped[RiskLevel] = mapped_column(
        enum_type(RiskLevel, "action_log_risk_level"), nullable=False
    )
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    before_snapshot: Mapped[dict[str, Any] | None] = mapped_column(MutableDict.as_mutable(JSON))
    after_snapshot: Mapped[dict[str, Any] | None] = mapped_column(MutableDict.as_mutable(JSON))
    verification_result: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class UndoRecord(Base):
    __tablename__ = "undo_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("und"))
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action_log_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("action_logs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    entity_type: Mapped[EntityType] = mapped_column(
        enum_type(EntityType, "undo_entity_type"), nullable=False
    )
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    undo_action: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(MutableDict.as_mutable(JSON))
    state: Mapped[UndoState] = mapped_column(
        enum_type(UndoState, "undo_state"), default=UndoState.AVAILABLE, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    undone_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
