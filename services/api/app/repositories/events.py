from __future__ import annotations

from builtins import list as builtin_list
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import CalendarEvent
from app.schemas.domain import EventCreate, EventUpdate


class EventRepository:
    async def list(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        starts_after: datetime | None = None,
        starts_before: datetime | None = None,
        course: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[CalendarEvent], int]:
        filters = [CalendarEvent.user_id == user_id]
        if starts_after is not None:
            filters.append(CalendarEvent.end_at > starts_after)
        if starts_before is not None:
            filters.append(CalendarEvent.start_at < starts_before)
        if course is not None:
            filters.append(func.lower(CalendarEvent.course) == course.strip().lower())
        count = await session.scalar(select(func.count(CalendarEvent.id)).where(*filters))
        result = await session.scalars(
            select(CalendarEvent)
            .where(*filters)
            .order_by(CalendarEvent.start_at.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(result), int(count or 0)

    async def get(self, session: AsyncSession, user_id: str, event_id: str) -> CalendarEvent | None:
        event: CalendarEvent | None = await session.scalar(
            select(CalendarEvent).where(
                CalendarEvent.id == event_id, CalendarEvent.user_id == user_id
            )
        )
        return event

    async def get_for_update(
        self, session: AsyncSession, user_id: str, event_id: str
    ) -> CalendarEvent | None:
        event: CalendarEvent | None = await session.scalar(
            select(CalendarEvent)
            .where(CalendarEvent.id == event_id, CalendarEvent.user_id == user_id)
            .with_for_update()
        )
        return event

    async def find_by_title(
        self,
        session: AsyncSession,
        user_id: str,
        title: str,
    ) -> builtin_list[CalendarEvent]:
        normalized = title.strip().lower()
        if not normalized:
            return []
        return list(
            await session.scalars(
                select(CalendarEvent)
                .where(
                    CalendarEvent.user_id == user_id,
                    func.lower(CalendarEvent.title) == normalized,
                )
                .order_by(CalendarEvent.start_at.asc())
                .limit(20)
            )
        )

    async def create(self, session: AsyncSession, user_id: str, data: EventCreate) -> CalendarEvent:
        values = data.model_dump(exclude={"allow_conflict"})
        if values["end_at"] is None:
            values["end_at"] = values["start_at"] + timedelta(hours=1)
        event = CalendarEvent(user_id=user_id, **values)
        session.add(event)
        await session.flush()
        return event

    async def update(
        self, session: AsyncSession, event: CalendarEvent, data: EventUpdate
    ) -> CalendarEvent:
        values = data.model_dump(exclude_unset=True, exclude={"allow_conflict", "expected_version"})
        for key, value in values.items():
            setattr(event, key, value)
        event.version += 1
        await session.flush()
        return event

    async def delete(self, session: AsyncSession, event: CalendarEvent) -> None:
        await session.delete(event)
        await session.flush()

    async def conflicts(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
        exclude_id: str | None = None,
    ) -> builtin_list[CalendarEvent]:
        filters = [
            CalendarEvent.user_id == user_id,
            CalendarEvent.start_at < end_at,
            CalendarEvent.end_at > start_at,
        ]
        if exclude_id:
            filters.append(CalendarEvent.id != exclude_id)
        return list(
            await session.scalars(
                select(CalendarEvent).where(*filters).order_by(CalendarEvent.start_at)
            )
        )

    async def find_duplicates(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        title: str,
        start_at: datetime,
        end_at: datetime,
        location: str | None,
        exclude_id: str | None = None,
    ) -> builtin_list[CalendarEvent]:
        filters = [
            CalendarEvent.user_id == user_id,
            func.lower(CalendarEvent.title) == title.strip().lower(),
            CalendarEvent.start_at >= start_at - timedelta(minutes=1),
            CalendarEvent.start_at <= start_at + timedelta(minutes=1),
            CalendarEvent.end_at >= end_at - timedelta(minutes=1),
            CalendarEvent.end_at <= end_at + timedelta(minutes=1),
        ]
        if exclude_id:
            filters.append(CalendarEvent.id != exclude_id)
        if location:
            filters.append(func.lower(CalendarEvent.location) == location.strip().lower())
        else:
            filters.append(CalendarEvent.location.is_(None))
        return list(await session.scalars(select(CalendarEvent).where(*filters)))
