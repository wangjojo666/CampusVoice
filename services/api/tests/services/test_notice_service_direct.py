import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.models.entities import Document, NoticeClaim
from app.schemas.notice_radar import NoticeSeriesCreate, NoticeVersionCreate
from app.services.errors import ConflictError, DomainError, NotFoundError
from app.services.notices import NoticeRadarService


@pytest.mark.asyncio
async def test_direct_service_series_and_owned_resource_guards(client: TestClient) -> None:
    assert client.get("/api/settings").status_code == 200
    factory = client.app.state.session_factory
    service = NoticeRadarService(factory)
    data = NoticeSeriesCreate(
        canonical_key="direct-service-series",
        title="Direct Service Notice",
        department="Testing",
    )
    async with factory() as session:
        created = await service.create_series(session, "user_demo", data)
        assert created.canonical_key == "direct-service-series"
        listed = await service.list_series(session, "user_demo", limit=10, offset=0)
        assert [item.id for item in listed] == [created.id]
        with pytest.raises(ConflictError, match="already exists"):
            await service.create_series(session, "user_demo", data)
        await session.rollback()
        with pytest.raises(NotFoundError):
            await service._owned_series(session, "user_demo", "missing")
        with pytest.raises(NotFoundError):
            await service._owned_document(session, "user_demo", "missing")
        with pytest.raises(NotFoundError):
            await service._owned_change_set(session, "user_demo", "missing")
        with pytest.raises(NotFoundError):
            await service._owned_plan(session, "user_demo", "missing")
        with pytest.raises(NotFoundError):
            await service._owned_entity(session, "user_demo", "task", "missing")
        with pytest.raises(NotFoundError):
            await service._owned_entity(session, "user_demo", "event", "missing")
        with pytest.raises(DomainError, match="Only task and event"):
            await service._owned_entity(session, "user_demo", "other", "missing")


@pytest.mark.asyncio
async def test_direct_reanalysis_rebuilds_missing_versioned_claims(client: TestClient) -> None:
    assert client.get("/api/settings").status_code == 200
    factory = client.app.state.session_factory
    service = NoticeRadarService(factory)
    async with factory() as session:
        series = await service.create_series(
            session,
            "user_demo",
            NoticeSeriesCreate(
                canonical_key="direct-reanalysis",
                title="Reanalysis Notice",
            ),
        )
        version = await service.add_version(
            session,
            "user_demo",
            series.id,
            NoticeVersionCreate(
                title="Reanalysis Notice",
                content="适用于全体学生。考试时间：2026-08-01 09:00–11:00。地点：A101。",
                revision_number=1,
                version_label="v1",
                ingest_source="seed",
            ),
        )
        assert version.claims
        await session.execute(delete(NoticeClaim).where(NoticeClaim.document_id == version.id))
        await session.commit()
        rebuilt = await service.reanalyze(session, "user_demo", version.id)
        assert {claim.claim_key for claim in rebuilt} >= {
            "event.start_at",
            "event.end_at",
            "event.location",
        }


@pytest.mark.asyncio
async def test_concurrent_identical_notice_version_requests_share_one_winner(
    client: TestClient,
) -> None:
    assert client.get("/api/settings").status_code == 200
    factory = client.app.state.session_factory
    service = NoticeRadarService(factory)
    async with factory() as session:
        series = await service.create_series(
            session,
            "user_demo",
            NoticeSeriesCreate(canonical_key="concurrent-identical", title="Notice"),
        )
        first = await service.add_version(
            session,
            "user_demo",
            series.id,
            NoticeVersionCreate(
                title="Notice",
                content="Initial content.",
                revision_number=1,
                version_label="v1",
                ingest_source="api",
            ),
        )

    async def add_identical() -> object:
        async with factory() as session:
            return await service.add_version(
                session,
                "user_demo",
                series.id,
                NoticeVersionCreate(
                    title="Notice",
                    content="Exactly one revision two.",
                    revision_number=2,
                    version_label="v2",
                    supersedes_document_id=first.id,
                    ingest_source="api",
                ),
            )

    left, right = await asyncio.gather(add_identical(), add_identical())
    assert left.id == right.id  # type: ignore[union-attr]
    async with factory() as session:
        rows = list(
            await session.scalars(
                select(Document).where(Document.series_id == series.id).order_by(Document.id)
            )
        )
    assert len(rows) == 2
    assert [row.id for row in rows if row.is_current] == [left.id]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_concurrent_notice_successors_serialize_on_series_write_lock(
    client: TestClient,
) -> None:
    assert client.get("/api/settings").status_code == 200
    factory = client.app.state.session_factory
    service = NoticeRadarService(factory)
    async with factory() as session:
        series = await service.create_series(
            session,
            "user_demo",
            NoticeSeriesCreate(canonical_key="concurrent-successors", title="Notice"),
        )
        first = await service.add_version(
            session,
            "user_demo",
            series.id,
            NoticeVersionCreate(
                title="Notice",
                content="Initial successor content.",
                revision_number=1,
                version_label="v1",
                ingest_source="api",
            ),
        )

    async def add_successor(revision: int) -> object:
        async with factory() as session:
            try:
                return await service.add_version(
                    session,
                    "user_demo",
                    series.id,
                    NoticeVersionCreate(
                        title="Notice",
                        content=f"Concurrent successor {revision}.",
                        revision_number=revision,
                        version_label=f"v{revision}",
                        supersedes_document_id=first.id,
                        ingest_source="api",
                    ),
                )
            except ConflictError as error:
                return error

    results = await asyncio.gather(add_successor(2), add_successor(3))
    conflicts = [result for result in results if isinstance(result, ConflictError)]
    winners = [result for result in results if not isinstance(result, ConflictError)]
    assert len(winners) == len(conflicts) == 1
    assert conflicts[0].code == "ambiguous_version_chain"

    async with factory() as session:
        rows = list(await session.scalars(select(Document).where(Document.series_id == series.id)))
    assert len(rows) == 2
    assert sum(row.is_current for row in rows) == 1


@pytest.mark.asyncio
async def test_predecessor_cas_miss_rolls_back_without_creating_successor(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert client.get("/api/settings").status_code == 200
    factory = client.app.state.session_factory
    service = NoticeRadarService(factory)
    async with factory() as session:
        series = await service.create_series(
            session,
            "user_demo",
            NoticeSeriesCreate(canonical_key="predecessor-cas-miss", title="Notice"),
        )
        first = await service.add_version(
            session,
            "user_demo",
            series.id,
            NoticeVersionCreate(
                title="Notice",
                content="Initial CAS revision.",
                revision_number=1,
                version_label="v1",
                ingest_source="api",
            ),
        )

    class EmptyReturningResult:
        @staticmethod
        def scalar_one_or_none() -> None:
            return None

    async with factory() as session:
        original_execute = session.execute

        async def execute_with_cas_miss(
            statement: object, *args: object, **kwargs: object
        ) -> object:
            table = getattr(statement, "table", None)
            if getattr(table, "name", None) == "documents":
                return EmptyReturningResult()
            return await original_execute(statement, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(session, "execute", execute_with_cas_miss)
        with pytest.raises(ConflictError) as caught:
            await service.add_version(
                session,
                "user_demo",
                series.id,
                NoticeVersionCreate(
                    title="Notice",
                    content="CAS successor must roll back.",
                    revision_number=2,
                    version_label="v2",
                    supersedes_document_id=first.id,
                    ingest_source="api",
                ),
            )
        assert caught.value.code == "ambiguous_version_chain"
        assert not session.in_transaction()

    async with factory() as session:
        rows = list(await session.scalars(select(Document).where(Document.series_id == series.id)))
    assert [(row.id, row.is_current) for row in rows] == [(first.id, True)]


@pytest.mark.asyncio
async def test_rejected_notice_version_releases_series_write_lock(client: TestClient) -> None:
    assert client.get("/api/settings").status_code == 200
    factory = client.app.state.session_factory
    service = NoticeRadarService(factory)
    async with factory() as session:
        series = await service.create_series(
            session,
            "user_demo",
            NoticeSeriesCreate(canonical_key="rollback-write-lock", title="Notice"),
        )
        first = await service.add_version(
            session,
            "user_demo",
            series.id,
            NoticeVersionCreate(
                title="Notice",
                content="First revision.",
                revision_number=1,
                version_label="v1",
                ingest_source="api",
            ),
        )
        with pytest.raises(DomainError) as caught:
            await service.add_version(
                session,
                "user_demo",
                series.id,
                NoticeVersionCreate(
                    title="Notice",
                    content="Missing explicit predecessor.",
                    revision_number=2,
                    version_label="v2",
                    ingest_source="api",
                ),
            )
        assert caught.value.code == "version_confirmation_required"
        assert not session.in_transaction()

    async with factory() as session:
        second = await service.add_version(
            session,
            "user_demo",
            series.id,
            NoticeVersionCreate(
                title="Notice",
                content="Valid second revision.",
                revision_number=2,
                version_label="v2",
                supersedes_document_id=first.id,
                ingest_source="api",
            ),
        )
    assert second.revision_number == 2


@pytest.mark.asyncio
async def test_unrelated_notice_integrity_error_is_not_mapped_to_concurrency_conflict(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert client.get("/api/settings").status_code == 200
    factory = client.app.state.session_factory
    service = NoticeRadarService(factory)
    async with factory() as session:
        series = await service.create_series(
            session,
            "user_demo",
            NoticeSeriesCreate(canonical_key="unrelated-integrity", title="Notice"),
        )
        first = await service.add_version(
            session,
            "user_demo",
            series.id,
            NoticeVersionCreate(
                title="Notice",
                content="First unrelated integrity revision.",
                revision_number=1,
                version_label="v1",
                ingest_source="api",
            ),
        )

    original_error = IntegrityError(
        "simulated unrelated claim constraint",
        {},
        RuntimeError("claim constraint failed"),
    )
    monkeypatch.setattr(
        service,
        "_ensure_claim_version",
        AsyncMock(side_effect=original_error),
    )
    async with factory() as session:
        with pytest.raises(IntegrityError) as caught:
            await service.add_version(
                session,
                "user_demo",
                series.id,
                NoticeVersionCreate(
                    title="Notice",
                    content="Second unrelated integrity revision.",
                    revision_number=2,
                    version_label="v2",
                    supersedes_document_id=first.id,
                    ingest_source="api",
                ),
            )
        assert caught.value is original_error
        assert not session.in_transaction()

    async with factory() as session:
        rows = list(await session.scalars(select(Document).where(Document.series_id == series.id)))
    assert [(row.id, row.is_current) for row in rows] == [(first.id, True)]
