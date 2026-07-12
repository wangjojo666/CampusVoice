from enum import StrEnum


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SourceType(StrEnum):
    MANUAL = "manual"
    VOICE = "voice"
    DOCUMENT = "document"
    SYSTEM = "system"


class HotwordCategory(StrEnum):
    COURSE = "course"
    COURSE_CODE = "course_code"
    TEACHER = "teacher"
    AI_TERM = "ai_term"
    DOCUMENT = "document"
    CUSTOM = "custom"


class ActionType(StrEnum):
    CREATE_TASK = "create_task"
    UPDATE_TASK = "update_task"
    DELETE_TASK = "delete_task"
    CREATE_EVENT = "create_event"
    UPDATE_EVENT = "update_event"
    DELETE_EVENT = "delete_event"


class EntityType(StrEnum):
    TASK = "task"
    EVENT = "event"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PendingActionState(StrEnum):
    NEEDS_INPUT = "needs_input"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_SECOND_CONFIRMATION = "awaiting_second_confirmation"
    READY = "ready"
    EXECUTING = "executing"
    EXECUTED = "executed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNDONE = "undone"
    EXPIRED = "expired"


class UndoState(StrEnum):
    AVAILABLE = "available"
    UNDONE = "undone"
    FAILED = "failed"
    EXPIRED = "expired"


class VoiceSessionStatus(StrEnum):
    CREATED = "created"
    STREAMING = "streaming"
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
