from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, StringConstraints, field_validator, model_validator

from app.schemas.common import StrictModel

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CoursePreference(StrictModel):
    id: str | None = None
    code: str | None = None
    name: str | None = None
    teacher: str | None = None

    @model_validator(mode="after")
    def has_identity(self) -> "CoursePreference":
        if not self.id and not self.code and not self.name:
            raise ValueError("a current course needs an id, code, or name")
        return self


class UserSettingsView(StrictModel):
    major: str | None
    grade: str | None
    current_courses: list[CoursePreference]
    teacher_names: list[str]
    default_reminder_minutes: int
    timezone: str
    asr_provider: str
    asr_model: str
    asr_device: str
    updated_at: datetime


class UserSettingsUpdate(StrictModel):
    major: str | None = None
    grade: str | None = None
    current_courses: list[CoursePreference] | None = None
    teacher_names: list[NonBlank] | None = None
    default_reminder_minutes: int | None = Field(default=None, ge=0, le=525_600)
    timezone: NonBlank | None = None
    asr_provider: NonBlank | None = None
    asr_model: NonBlank | None = None
    asr_device: NonBlank | None = None

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @field_validator("teacher_names")
    @classmethod
    def unique_teachers(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def has_changes(self) -> "UserSettingsUpdate":
        if not self.model_fields_set:
            raise ValueError("at least one setting must be supplied")
        required_when_set = {
            "timezone",
            "asr_provider",
            "asr_model",
            "asr_device",
            "default_reminder_minutes",
        }
        null_fields = {
            name
            for name in self.model_fields_set & required_when_set
            if getattr(self, name) is None
        }
        if null_fields:
            raise ValueError(f"settings cannot be null: {', '.join(sorted(null_fields))}")
        return self


class UserSettingsMutationResponse(StrictModel):
    success: Literal[True]
    verified_fields: dict[str, bool]
    message: str
    settings: UserSettingsView
