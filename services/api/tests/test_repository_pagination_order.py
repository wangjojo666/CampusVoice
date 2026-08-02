from unittest.mock import AsyncMock

import pytest

from app.repositories.actions import ActionRepository
from app.repositories.events import EventRepository
from app.repositories.tasks import TaskRepository


def _order_by_strings(statement: object) -> list[str]:
    return [str(clause) for clause in statement._order_by_clauses]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_task_offset_pagination_has_a_unique_tie_breaker() -> None:
    session = AsyncMock()
    session.scalar.return_value = 2
    session.scalars.return_value = []

    await TaskRepository().list(session, "user-1", limit=1, offset=1)

    statement = session.scalars.await_args.args[0]
    assert _order_by_strings(statement) == [
        "tasks.due_at ASC NULLS LAST",
        "tasks.created_at DESC",
        "tasks.id ASC",
    ]


@pytest.mark.asyncio
async def test_event_offset_pagination_has_a_unique_tie_breaker() -> None:
    session = AsyncMock()
    session.scalar.return_value = 2
    session.scalars.return_value = []

    await EventRepository().list(session, "user-1", limit=1, offset=1)

    statement = session.scalars.await_args.args[0]
    assert _order_by_strings(statement) == [
        "calendar_events.start_at ASC",
        "calendar_events.id ASC",
    ]


@pytest.mark.asyncio
async def test_action_log_offset_pagination_has_a_unique_tie_breaker() -> None:
    session = AsyncMock()
    session.scalar.return_value = 2
    session.scalars.return_value = []

    await ActionRepository().list_logs(session, "user-1", limit=1, offset=1)

    statement = session.scalars.await_args.args[0]
    assert _order_by_strings(statement) == [
        "action_logs.created_at DESC",
        "action_logs.id DESC",
    ]
