from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import SessionDependency, UserIdDependency, WriteChallengeDependency
from app.schemas.notice_radar import (
    ChangeReviewRequest,
    ImpactListView,
    MigrationExecuteRequest,
    MigrationPlanView,
    MigrationUndoRequest,
    NoticeChangeItemView,
    NoticeChangeSetView,
    NoticeClaimView,
    NoticeSeriesCreate,
    NoticeSeriesView,
    NoticeTimelineView,
    NoticeVersionCreate,
    NoticeVersionView,
    RadarView,
    VerificationReceiptView,
)
from app.services.notices import NoticeRadarService

router = APIRouter(prefix="/notice-radar", tags=["notice-radar"])


def get_notice_radar_service(request: Request) -> NoticeRadarService:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return NoticeRadarService(factory)


ServiceDependency = Annotated[NoticeRadarService, Depends(get_notice_radar_service)]


@router.get("", response_model=RadarView)
async def radar(
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> RadarView:
    return await service.radar(session, user_id, limit=limit)


@router.post("/series", response_model=NoticeSeriesView, status_code=status.HTTP_201_CREATED)
async def create_series(
    body: NoticeSeriesCreate,
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
    _challenge: WriteChallengeDependency,
) -> NoticeSeriesView:
    return await service.create_series(session, user_id, body)


@router.get("/series", response_model=list[NoticeSeriesView])
async def list_series(
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> list[NoticeSeriesView]:
    return await service.list_series(session, user_id, limit=limit, offset=offset)


@router.post(
    "/series/{series_id}/versions",
    response_model=NoticeVersionView,
    status_code=status.HTTP_201_CREATED,
)
async def add_version(
    series_id: str,
    body: NoticeVersionCreate,
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
    _challenge: WriteChallengeDependency,
) -> NoticeVersionView:
    return await service.add_version(session, user_id, series_id, body)


@router.get("/series/{series_id}/timeline", response_model=NoticeTimelineView)
async def timeline(
    series_id: str,
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
) -> NoticeTimelineView:
    return await service.timeline(session, user_id, series_id)


@router.get("/documents/{document_id}/claims", response_model=list[NoticeClaimView])
async def document_claims(
    document_id: str,
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
) -> list[NoticeClaimView]:
    return await service.claims(session, user_id, document_id)


@router.post("/documents/{document_id}/reanalyze", response_model=list[NoticeClaimView])
async def reanalyze_document(
    document_id: str,
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
    _challenge: WriteChallengeDependency,
) -> list[NoticeClaimView]:
    return await service.reanalyze(session, user_id, document_id)


@router.get("/changes/{change_set_id}", response_model=NoticeChangeSetView)
async def get_change_set(
    change_set_id: str,
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
) -> NoticeChangeSetView:
    return await service.change_set(session, user_id, change_set_id)


@router.patch("/changes/items/{change_item_id}/review", response_model=NoticeChangeItemView)
async def review_change(
    change_item_id: str,
    body: ChangeReviewRequest,
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
    _challenge: WriteChallengeDependency,
) -> NoticeChangeItemView:
    return await service.review_change(session, user_id, change_item_id, body.decision)


@router.post("/changes/{change_set_id}/impacts/detect", response_model=ImpactListView)
async def detect_impacts(
    change_set_id: str,
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
    _challenge: WriteChallengeDependency,
) -> ImpactListView:
    return await service.detect_impacts(session, user_id, change_set_id)


@router.get("/impacts", response_model=ImpactListView)
async def list_impacts(
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
    change_set_id: str | None = None,
    impact_status: Annotated[
        Literal["open", "resolved", "dismissed"] | None, Query(alias="status")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> ImpactListView:
    return await service.list_impacts(
        session,
        user_id,
        change_set_id=change_set_id,
        status=impact_status,
        limit=limit,
        offset=offset,
    )


@router.post("/changes/{change_set_id}/migration-preview", response_model=MigrationPlanView)
async def migration_preview(
    change_set_id: str,
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
    _challenge: WriteChallengeDependency,
) -> MigrationPlanView:
    return await service.build_plan(session, user_id, change_set_id)


@router.get("/migrations/{plan_id}", response_model=MigrationPlanView)
async def get_migration_plan(
    plan_id: str,
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
) -> MigrationPlanView:
    return await service.plan(session, user_id, plan_id)


@router.post("/migrations/{plan_id}/execute", response_model=VerificationReceiptView)
async def execute_migration(
    plan_id: str,
    body: MigrationExecuteRequest,
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
    _challenge: WriteChallengeDependency,
) -> VerificationReceiptView:
    return await service.execute(session, user_id, plan_id, body)


@router.get("/migrations/{plan_id}/receipt", response_model=VerificationReceiptView)
async def migration_receipt(
    plan_id: str,
    operation: Literal["execute", "undo"],
    service: ServiceDependency,
    user_id: UserIdDependency,
) -> VerificationReceiptView:
    return await service.receipt(user_id, plan_id, operation=operation)


@router.post("/migrations/{plan_id}/undo", response_model=VerificationReceiptView)
async def undo_migration(
    plan_id: str,
    body: MigrationUndoRequest,
    service: ServiceDependency,
    session: SessionDependency,
    user_id: UserIdDependency,
    _challenge: WriteChallengeDependency,
) -> VerificationReceiptView:
    return await service.undo(session, user_id, plan_id, body)
