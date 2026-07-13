import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.models.entities import NoticeClaim
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
