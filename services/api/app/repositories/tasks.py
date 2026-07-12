from __future__ import annotations

from builtins import list as builtin_list
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Task
from app.models.enums import TaskStatus
from app.schemas.domain import TaskCreate, TaskUpdate


class TaskRepository:
    async def list(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        status: TaskStatus | None = None,
        course: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[Task], int]:
        filters = [Task.user_id == user_id]
        if status is not None:
            filters.append(Task.status == status)
        if course is not None:
            filters.append(func.lower(Task.course) == course.strip().lower())
        count = await session.scalar(select(func.count(Task.id)).where(*filters))
        result = await session.scalars(
            select(Task)
            .where(*filters)
            .order_by(Task.due_at.asc().nullslast(), Task.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result), int(count or 0)

    async def get(self, session: AsyncSession, user_id: str, task_id: str) -> Task | None:
        task: Task | None = await session.scalar(
            select(Task).where(Task.id == task_id, Task.user_id == user_id)
        )
        return task

    async def get_for_update(
        self, session: AsyncSession, user_id: str, task_id: str
    ) -> Task | None:
        task: Task | None = await session.scalar(
            select(Task).where(Task.id == task_id, Task.user_id == user_id).with_for_update()
        )
        return task

    async def find_by_title(
        self,
        session: AsyncSession,
        user_id: str,
        title: str,
    ) -> builtin_list[Task]:
        normalized = title.strip().lower()
        if not normalized:
            return []
        return list(
            await session.scalars(
                select(Task)
                .where(Task.user_id == user_id, func.lower(Task.title) == normalized)
                .order_by(Task.due_at.asc().nullslast(), Task.created_at.desc())
                .limit(20)
            )
        )

    async def create(self, session: AsyncSession, user_id: str, data: TaskCreate) -> Task:
        task = Task(user_id=user_id, **data.model_dump())
        session.add(task)
        await session.flush()
        return task

    async def update(self, session: AsyncSession, task: Task, data: TaskUpdate) -> Task:
        values = data.model_dump(exclude_unset=True, exclude={"expected_version"})
        for key, value in values.items():
            setattr(task, key, value)
        task.version += 1
        await session.flush()
        return task

    async def delete(self, session: AsyncSession, task: Task) -> None:
        await session.delete(task)
        await session.flush()

    async def find_duplicates(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        title: str,
        course: str | None,
        due_at: datetime | None,
        exclude_id: str | None = None,
    ) -> builtin_list[Task]:
        filters = [Task.user_id == user_id, func.lower(Task.title) == title.strip().lower()]
        if exclude_id:
            filters.append(Task.id != exclude_id)
        if course:
            filters.append(func.lower(Task.course) == course.strip().lower())
        else:
            filters.append(Task.course.is_(None))
        if due_at is None:
            filters.append(Task.due_at.is_(None))
        else:
            filters.extend(
                [
                    Task.due_at >= due_at - timedelta(minutes=5),
                    Task.due_at <= due_at + timedelta(minutes=5),
                ]
            )
        return list(await session.scalars(select(Task).where(*filters).order_by(Task.created_at)))
