from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.entities import (
    Document,
    DocumentChunk,
    NoticeChangeSet,
    NoticeClaim,
    NoticeSeries,
)
from app.services.notices.service import _BoundedRadarCards
from tests.helpers import confirmed_write


def _count_selects(
    client: TestClient,
    request: Callable[[], Response],
    *,
    execution_options: list[dict[str, Any]] | None = None,
) -> tuple[Response, list[str]]:
    application = cast(FastAPI, client.app)
    engine = application.state.database_engine.sync_engine
    statements: list[str] = []

    def record_select(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
            if execution_options is not None:
                execution_options.append(dict(_context.execution_options))

    event.listen(engine, "before_cursor_execute", record_select)
    try:
        response = request()
    finally:
        event.remove(engine, "before_cursor_execute", record_select)
    return response, statements


def _create_series(client: TestClient, index: int) -> dict[str, Any]:
    response = confirmed_write(
        client,
        "POST",
        "/api/notice-radar/series",
        {
            "canonical_key": f"performance-{index}",
            "title": f"Performance notice {index}",
            "department": "Performance",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _add_version(
    client: TestClient,
    series_id: str,
    revision: int,
    *,
    supersedes_document_id: str | None = None,
) -> dict[str, Any]:
    response = confirmed_write(
        client,
        "POST",
        f"/api/notice-radar/series/{series_id}/versions",
        {
            "title": f"Performance notice {series_id}",
            "content": (
                f"General campus bulletin {series_id}, revision {revision}. "
                "No scheduled action is required."
            ),
            "revision_number": revision,
            "version_label": f"v{revision}",
            "supersedes_document_id": supersedes_document_id,
            "ingest_source": "seed",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


async def _tie_series_and_documents(
    factory: async_sessionmaker[AsyncSession],
    series_ids: list[str],
    document_ids: list[str],
) -> None:
    tied_at = datetime(2026, 7, 19, tzinfo=UTC)
    async with factory() as session:
        await session.execute(
            update(NoticeSeries).where(NoticeSeries.id.in_(series_ids)).values(updated_at=tied_at)
        )
        await session.execute(
            update(Document).where(Document.id.in_(document_ids)).values(created_at=tied_at)
        )
        await session.commit()


async def _tie_series_and_change_sets(
    factory: async_sessionmaker[AsyncSession],
    series_ids: list[str],
) -> list[str]:
    tied_at = datetime(2026, 7, 19, tzinfo=UTC)
    async with factory() as session:
        change_set_ids = list(
            await session.scalars(
                select(NoticeChangeSet.id).where(
                    NoticeChangeSet.user_id == "user_demo",
                    NoticeChangeSet.series_id.in_(series_ids),
                )
            )
        )
        await session.execute(
            update(NoticeSeries).where(NoticeSeries.id.in_(series_ids)).values(updated_at=tied_at)
        )
        await session.execute(
            update(NoticeChangeSet)
            .where(NoticeChangeSet.id.in_(change_set_ids))
            .values(created_at=tied_at)
        )
        await session.commit()
    return change_set_ids


async def _seed_tied_deadlines(
    factory: async_sessionmaker[AsyncSession],
    document_id: str,
    earlier_due_at: datetime,
    selected_due_at: datetime,
) -> None:
    tied_at = datetime(2026, 7, 19, tzinfo=UTC)
    async with factory() as session:
        chunk_id = await session.scalar(
            select(DocumentChunk.id)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.ordinal)
        )
        assert chunk_id is not None
        for claim_id, extractor_version, due_at in (
            ("ncl_deadline_tie_a", "tie-a", earlier_due_at),
            ("ncl_deadline_tie_z", "tie-z", selected_due_at),
        ):
            session.add(
                NoticeClaim(
                    id=claim_id,
                    user_id="user_demo",
                    document_id=document_id,
                    chunk_id=chunk_id,
                    claim_key="task.due_at",
                    claim_type="datetime",
                    value_json={"text": "deadline"},
                    normalized_value_json={"iso": due_at.isoformat()},
                    audience_rule_json={},
                    confidence=1.0,
                    evidence_start=0,
                    evidence_end=1,
                    extractor_version=extractor_version,
                    review_state="approved",
                    created_at=tied_at,
                )
            )
        await session.commit()


def _card_fields(index: int) -> dict[str, Any]:
    return {
        "card_type": "new_notice",
        "change_set_id": None,
        "document_id": f"document-{index}",
        "series_id": "series",
        "title": f"card-{index}",
        "from_revision": 0,
        "to_revision": 1,
        "change_count": 0,
        "affected_tasks": 0,
        "affected_events": 0,
        "needs_review": False,
        "applicability": "applicable",
        "applicability_reason": "No audience restriction was extracted",
        "message": f"card-{index}",
    }


def test_list_series_query_count_is_constant(client: TestClient) -> None:
    for index in range(12):
        _create_series(client, index)

    response, statements = _count_selects(
        client,
        lambda: client.get("/api/notice-radar/series"),
    )

    assert response.status_code == 200, response.text
    assert len(response.json()) == 12
    # Demo authentication performs two fixed principal/profile SELECTs before
    # the handler; list_series itself remains fixed at two SELECTs.
    assert len(statements) == 4


def test_radar_query_count_and_total_are_bounded_and_exact(client: TestClient) -> None:
    chain = _create_series(client, 0)
    first = _add_version(client, str(chain["id"]), 1)
    _add_version(
        client,
        str(chain["id"]),
        2,
        supersedes_document_id=str(first["id"]),
    )
    for index in range(1, 9):
        series = _create_series(client, index)
        _add_version(client, str(series["id"]), 1)

    execution_options: list[dict[str, Any]] = []
    response, statements = _count_selects(
        client,
        lambda: client.get("/api/notice-radar", params={"limit": 1}),
        execution_options=execution_options,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["total"] == 9
    # The same two authentication SELECTs precede the seven fixed radar
    # SELECTs, regardless of the number of current documents.
    assert len(statements) == 9
    radar_stream_options = [
        options
        for statement, options in zip(statements, execution_options, strict=True)
        if "radar_audience_claim" in statement
    ]
    assert len(radar_stream_options) == 1
    assert radar_stream_options[0]["yield_per"] == 100


def test_bounded_radar_cards_materialize_only_limit_and_keep_stable_order() -> None:
    created_at = datetime(2026, 7, 19, tzinfo=UTC)
    tied = _BoundedRadarCards(limit=2)
    for index in range(5):
        tied.add(created_at=created_at, **_card_fields(index))

    assert tied.total == 5
    assert tied.retained_count == 2
    assert [item.title for item in tied.items()] == ["card-0", "card-1"]

    newest = _BoundedRadarCards(limit=1)
    for index in range(20):
        newest.add(
            created_at=created_at + timedelta(seconds=index),
            **_card_fields(index),
        )

    assert newest.total == 20
    assert newest.retained_count == 1
    assert [item.title for item in newest.items()] == ["card-19"]


def test_equal_timestamps_keep_series_pages_and_change_cards_deterministic(
    client: TestClient,
) -> None:
    series_ids: list[str] = []
    for index in range(20, 23):
        series = _create_series(client, index)
        first = _add_version(client, str(series["id"]), 1)
        _add_version(
            client,
            str(series["id"]),
            2,
            supersedes_document_id=str(first["id"]),
        )
        series_ids.append(str(series["id"]))

    assert client.portal is not None
    change_set_ids = client.portal.call(
        _tie_series_and_change_sets,
        client.app.state.session_factory,
        series_ids,
    )

    paged_series_ids = [
        client.get(
            "/api/notice-radar/series",
            params={"limit": 1, "offset": offset},
        ).json()[0]["id"]
        for offset in range(3)
    ]
    assert paged_series_ids == sorted(series_ids, reverse=True)

    radar = client.get("/api/notice-radar", params={"limit": 2})
    assert radar.status_code == 200, radar.text
    assert radar.json()["total"] == 3
    assert [item["change_set_id"] for item in radar.json()["items"]] == sorted(
        change_set_ids,
        reverse=True,
    )[:2]


def test_equal_timestamps_keep_current_document_cards_deterministic(
    client: TestClient,
) -> None:
    series_ids: list[str] = []
    document_ids: list[str] = []
    for index in range(30, 33):
        series = _create_series(client, index)
        document = _add_version(client, str(series["id"]), 1)
        series_ids.append(str(series["id"]))
        document_ids.append(str(document["id"]))

    assert client.portal is not None
    client.portal.call(
        _tie_series_and_documents,
        client.app.state.session_factory,
        series_ids,
        document_ids,
    )

    radar = client.get("/api/notice-radar", params={"limit": 2})
    assert radar.status_code == 200, radar.text
    assert radar.json()["total"] == 3
    assert [item["document_id"] for item in radar.json()["items"]] == sorted(
        document_ids,
        reverse=True,
    )[:2]


def test_equal_deadline_timestamps_choose_the_highest_claim_id(client: TestClient) -> None:
    series = _create_series(client, 40)
    document = _add_version(client, str(series["id"]), 1)
    now = datetime.now(UTC).replace(microsecond=0)
    earlier_due_at = now + timedelta(days=2)
    selected_due_at = now + timedelta(days=3)

    assert client.portal is not None
    client.portal.call(
        _seed_tied_deadlines,
        client.app.state.session_factory,
        str(document["id"]),
        earlier_due_at,
        selected_due_at,
    )

    radar = client.get("/api/notice-radar")
    assert radar.status_code == 200, radar.text
    deadline = next(
        item for item in radar.json()["items"] if item["card_type"] == "upcoming_deadline"
    )
    actual_due_at = datetime.fromisoformat(str(deadline["deadline_at"]).replace("Z", "+00:00"))
    assert actual_due_at == selected_due_at
