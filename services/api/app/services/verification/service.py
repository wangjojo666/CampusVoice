from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import CalendarEvent, Hotword, Task
from app.repositories.events import EventRepository
from app.repositories.hotwords import HotwordRepository
from app.repositories.tasks import TaskRepository
from app.schemas.domain import EventView, HotwordView, TaskView


@dataclass(frozen=True, slots=True)
class VerificationReport:
    success: bool
    verified_fields: dict[str, bool]
    side_effects: tuple[str, ...]
    record: Task | CalendarEvent | Hotword | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "verified_fields": self.verified_fields,
            "side_effects": list(self.side_effects),
        }


def _same(expected: Any, actual: Any) -> bool:
    return bool(_canonical(expected) == _canonical(actual))


def _canonical(value: Any) -> Any:
    if hasattr(value, "value"):
        value = value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC)
    return value


class VerificationService:
    def __init__(self) -> None:
        self.tasks = TaskRepository()
        self.events = EventRepository()
        self.hotwords = HotwordRepository()

    async def verify_task(
        self,
        session: AsyncSession,
        user_id: str,
        task_id: str,
        expected: dict[str, Any],
        *,
        should_exist: bool = True,
    ) -> VerificationReport:
        session.expire_all()
        task = await self.tasks.get(session, user_id, task_id)
        if not should_exist:
            return VerificationReport(task is None, {"absent": task is None}, (), None)
        if task is None:
            return VerificationReport(False, {key: False for key in expected}, (), None)

        actual = TaskView.model_validate(task).model_dump(mode="json")
        fields = {key: _same(value, actual.get(key)) for key, value in expected.items()}
        duplicates = await self.tasks.find_duplicates(
            session,
            user_id,
            title=task.title,
            course=task.course,
            due_at=task.due_at,
            exclude_id=task.id,
        )
        side_effects = ("duplicate_task_created",) if duplicates else ()
        return VerificationReport(
            all(fields.values()) and not duplicates, fields, side_effects, task
        )

    async def verify_event(
        self,
        session: AsyncSession,
        user_id: str,
        event_id: str,
        expected: dict[str, Any],
        *,
        should_exist: bool = True,
        allow_conflict: bool = False,
    ) -> VerificationReport:
        session.expire_all()
        event = await self.events.get(session, user_id, event_id)
        if not should_exist:
            return VerificationReport(event is None, {"absent": event is None}, (), None)
        if event is None:
            return VerificationReport(False, {key: False for key in expected}, (), None)

        actual = EventView.model_validate(event).model_dump(mode="json")
        fields = {key: _same(value, actual.get(key)) for key, value in expected.items()}
        duplicates = await self.events.find_duplicates(
            session,
            user_id,
            title=event.title,
            start_at=event.start_at,
            end_at=event.end_at,
            location=event.location,
            exclude_id=event.id,
        )
        conflicts = await self.events.conflicts(
            session,
            user_id,
            start_at=event.start_at,
            end_at=event.end_at,
            exclude_id=event.id,
        )
        side_effects: list[str] = []
        if duplicates:
            side_effects.append("duplicate_event_created")
        if conflicts:
            side_effects.append("time_conflict")
        success = all(fields.values()) and not duplicates and (allow_conflict or not conflicts)
        return VerificationReport(success, fields, tuple(side_effects), event)

    async def verify_hotword(
        self,
        session: AsyncSession,
        user_id: str,
        hotword_id: str,
        expected: dict[str, Any],
        *,
        should_exist: bool = True,
    ) -> VerificationReport:
        session.expire_all()
        hotword = await self.hotwords.get(session, user_id, hotword_id)
        if not should_exist:
            return VerificationReport(hotword is None, {"absent": hotword is None}, (), None)
        if hotword is None:
            return VerificationReport(False, {key: False for key in expected}, (), None)
        actual = HotwordView.model_validate(hotword).model_dump(mode="json")
        fields = {key: _same(value, actual.get(key)) for key, value in expected.items()}
        return VerificationReport(all(fields.values()), fields, (), hotword)
