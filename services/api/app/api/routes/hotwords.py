from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.dependencies import (
    MetricsDependency,
    SessionDependency,
    UserIdDependency,
    WriteChallengeDependency,
)
from app.models.enums import HotwordCategory
from app.repositories.hotwords import HotwordRepository
from app.schemas.domain import (
    HotwordCreate,
    HotwordList,
    HotwordMutationResponse,
    HotwordView,
)
from app.services.hotword_service import HotwordService

router = APIRouter(prefix="/hotwords", tags=["hotwords"])


@router.get("", response_model=HotwordList)
async def list_hotwords(
    session: SessionDependency,
    user_id: UserIdDependency,
    category: HotwordCategory | None = None,
    active_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> HotwordList:
    items, total = await HotwordRepository().list(
        session,
        user_id,
        category=category,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    return HotwordList(items=[HotwordView.model_validate(item) for item in items], total=total)


@router.post("", response_model=HotwordMutationResponse, status_code=status.HTTP_201_CREATED)
async def create_hotword(
    body: HotwordCreate,
    session: SessionDependency,
    user_id: UserIdDependency,
    _write_challenge: WriteChallengeDependency,
    metrics: MetricsDependency,
) -> HotwordMutationResponse:
    return await HotwordService(metrics).create(session, user_id, body, confirmed=True)


@router.delete("/{hotword_id}", response_model=HotwordMutationResponse)
async def delete_hotword(
    hotword_id: str,
    session: SessionDependency,
    user_id: UserIdDependency,
    _write_challenge: WriteChallengeDependency,
    metrics: MetricsDependency,
) -> HotwordMutationResponse:
    return await HotwordService(metrics).delete(
        session,
        user_id,
        hotword_id,
        confirmed=True,
        second_confirmation=True,
    )
