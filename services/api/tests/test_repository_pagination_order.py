from unittest.mock import AsyncMock

import pytest

from app.repositories.actions import ActionRepository
from app.repositories.documents import DocumentRepository
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
        "tasks.due_at IS NULL ASC",
        "tasks.due_at ASC",
        "tasks.created_at DESC",
        "tasks.id ASC",
    ]


@pytest.mark.asyncio
async def test_document_sorting_keeps_null_dates_last_without_nulls_last_sql() -> None:
    list_session = AsyncMock()
    list_session.scalars.return_value = []
    await DocumentRepository().list(list_session, "user-1")

    list_statement = list_session.scalars.await_args.args[0]
    expected = [
        "documents.publish_date IS NULL ASC",
        "documents.publish_date DESC",
        "documents.created_at DESC",
    ]
    assert _order_by_strings(list_statement) == expected

    count_session = AsyncMock()
    count_session.execute.return_value = []
    await DocumentRepository().list_with_chunk_counts(count_session, "user-1")

    count_statement = count_session.execute.await_args.args[0]
    assert _order_by_strings(count_statement) == expected


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
