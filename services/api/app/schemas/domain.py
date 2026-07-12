from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.models.enums import (
    ActionType,
    EntityType,
    HotwordCategory,
    RiskLevel,
    SourceType,
    TaskPriority,
    TaskStatus,
)
from app.schemas.common import StrictModel

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class TaskCreate(StrictModel):
    title: NonBlank
    description: str | None = None
    course_id: str | None = None
    course: str | None = None
    due_at: AwareDatetime | None = None
    reminder_at: AwareDatetime | None = None
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    source_type: SourceType = SourceType.MANUAL
    source_document_id: str | None = None

    @model_validator(mode="after")
    def reminder_precedes_due(self) -> "TaskCreate":
        if self.due_at and self.reminder_at and self.reminder_at > self.due_at:
            raise ValueError("reminder_at must not be later than due_at")
        return self


class TaskUpdate(StrictModel):
    title: NonBlank | None = None
    description: str | None = None
    course_id: str | None = None
    course: str | None = None
    due_at: AwareDatetime | None = None
    reminder_at: AwareDatetime | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    source_type: SourceType | None = None
    source_document_id: str | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def has_changes(self) -> "TaskUpdate":
        changed = self.model_fields_set - {"expected_version"}
        if not changed:
            raise ValueError("at least one task field must be supplied")
        if self.due_at and self.reminder_at and self.reminder_at > self.due_at:
            raise ValueError("reminder_at must not be later than due_at")
        return self


class TaskDraft(StrictModel):
    title: NonBlank | None = None
    description: str | None = None
    course_id: str | None = None
    course: str | None = None
    due_at: AwareDatetime | None = None
    reminder_at: AwareDatetime | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    source_type: SourceType | None = None
    source_document_id: str | None = None
    expected_version: int | None = Field(default=None, ge=1)


class TaskView(StrictModel):
    id: str
    title: str
    description: str | None
    course_id: str | None
    course: str | None
    due_at: datetime | None
    reminder_at: datetime | None
    priority: TaskPriority
    status: TaskStatus
    source_type: SourceType
    source_document_id: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class TaskList(StrictModel):
    items: list[TaskView]
    total: int = Field(ge=0)


class EventCreate(StrictModel):
    title: NonBlank
    description: str | None = None
    course_id: str | None = None
    course: str | None = None
    start_at: AwareDatetime
    end_at: AwareDatetime | None = None
    location: str | None = None
    reminder_minutes: int = Field(default=30, ge=0, le=525_600)
    source_type: SourceType = SourceType.MANUAL
    source_document_id: str | None = None
    allow_conflict: bool = False

    @model_validator(mode="after")
    def end_follows_start(self) -> "EventCreate":
        if self.end_at is not None and self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        return self


class EventUpdate(StrictModel):
    title: NonBlank | None = None
    description: str | None = None
    course_id: str | None = None
    course: str | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    location: str | None = None
    reminder_minutes: int | None = Field(default=None, ge=0, le=525_600)
    source_type: SourceType | None = None
    source_document_id: str | None = None
    allow_conflict: bool = False
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_patch(self) -> "EventUpdate":
        changed = self.model_fields_set - {"allow_conflict", "expected_version"}
        if not changed:
            raise ValueError("at least one event field must be supplied")
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        return self


class EventDraft(StrictModel):
    title: NonBlank | None = None
    description: str | None = None
    course_id: str | None = None
    course: str | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    location: str | None = None
    reminder_minutes: int | None = Field(default=None, ge=0, le=525_600)
    source_type: SourceType | None = None
    source_document_id: str | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def end_follows_start(self) -> "EventDraft":
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        return self


class EventView(StrictModel):
    id: str
    title: str
    description: str | None
    course_id: str | None
    course: str | None
    start_at: datetime
    end_at: datetime
    location: str | None
    reminder_minutes: int
    source_type: SourceType
    source_document_id: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class EventList(StrictModel):
    items: list[EventView]
    total: int = Field(ge=0)


class ConflictCheckRequest(StrictModel):
    start_at: AwareDatetime
    end_at: AwareDatetime
    exclude_event_id: str | None = None

    @model_validator(mode="after")
    def end_follows_start(self) -> "ConflictCheckRequest":
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        return self


class ConflictCheckResponse(StrictModel):
    has_conflict: bool
    conflicts: list[EventView]


class HotwordCreate(StrictModel):
    term: NonBlank
    category: HotwordCategory = HotwordCategory.CUSTOM
    source: NonBlank = "user"
    weight: float = Field(default=1.0, gt=0, le=10)


class HotwordView(StrictModel):
    id: str
    term: str
    category: HotwordCategory
    source: str
    weight: float
    is_active: bool
    created_at: datetime
    updated_at: datetime


class HotwordList(StrictModel):
    items: list[HotwordView]
    total: int = Field(ge=0)


class VerifiedMutation(StrictModel):
    success: Literal[True]
    action: str
    record_id: str
    verified_fields: dict[str, bool]
    side_effects: list[str]
    message: str


class TaskMutationResponse(VerifiedMutation):
    record: TaskView | None


class EventMutationResponse(VerifiedMutation):
    record: EventView | None


class HotwordMutationResponse(VerifiedMutation):
    record: HotwordView | None


class ActionLogView(StrictModel):
    id: str
    pending_action_id: str | None
    voice_session_id: str | None
    transcription_id: str | None
    action_type: ActionType
    entity_type: EntityType
    target_id: str | None
    source_text: str | None
    corrected_text: str | None
    recognized_intent: str | None
    extracted_slots: dict[str, Any]
    risk_level: RiskLevel
    user_confirmed: bool
    before_snapshot: dict[str, Any] | None
    after_snapshot: dict[str, Any] | None
    verification_result: dict[str, Any]
    success: bool
    error_message: str | None
    created_at: datetime


class ActionLogList(StrictModel):
    items: list[ActionLogView]
    total: int = Field(ge=0)


class DocumentView(StrictModel):
    id: str
    title: str
    department: str | None
    publish_date: date | None
    applicable_group: str | None
    source_url: str | None
    version: str | None
    file_type: str
    storage_path: str
    content_sha256: str
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime
