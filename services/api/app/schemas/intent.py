from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentName(StrEnum):
    CREATE_TASK = "create_task"
    UPDATE_TASK = "update_task"
    DELETE_TASK = "delete_task"
    CREATE_EVENT = "create_event"
    UPDATE_EVENT = "update_event"
    DELETE_EVENT = "delete_event"
    SEARCH_NOTICE = "search_notice"
    QUERY_SCHEDULE = "query_schedule"
    UNKNOWN = "unknown"


class IntentSlots(_StrictModel):
    title: str | None = None
    new_title: str | None = None
    description: str | None = None
    course: str | None = None
    date: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    location: str | None = None
    reminder_minutes: int | None = Field(default=None, ge=0, le=525_600)
    due_date: str | None = None
    due_time: str | None = None
    priority: str | None = None
    status: str | None = None
    task_id: str | None = None
    event_id: str | None = None
    query: str | None = None

    @field_validator("date", "due_date")
    @classmethod
    def validate_iso_date(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parts = value.split("-")
        if len(parts) != 3 or tuple(map(len, parts)) != (4, 2, 2):
            raise ValueError("date fields must use YYYY-MM-DD")
        return value

    @field_validator("start_time", "end_time", "due_time")
    @classmethod
    def validate_clock_time(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parts = value.split(":")
        if len(parts) != 2 or tuple(map(len, parts)) != (2, 2):
            raise ValueError("time fields must use HH:MM")
        hours, minutes = map(int, parts)
        if not 0 <= hours <= 23 or not 0 <= minutes <= 59:
            raise ValueError("invalid clock time")
        return value


class IntentResult(_StrictModel):
    intent: IntentName
    confidence: float = Field(ge=0, le=1)
    slots: IntentSlots = Field(default_factory=IntentSlots)
    missing_fields: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    source_text: str = Field(min_length=1, max_length=10_000)
    requires_confirmation: bool
    conversation_id: str | None = None


class IntentParseRequest(_StrictModel):
    text: str = Field(min_length=1, max_length=10_000)
    asr_confidence: float | None = Field(default=None, ge=0, le=1)
    context: list[str] = Field(default_factory=list, max_length=20)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=64)


class IntentParseErrorResponse(_StrictModel):
    code: str
    message: str
