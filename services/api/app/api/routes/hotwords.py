from typing import Annotated

from fastapi import APIRouter, Header, Query, status

from app.api.dependencies import SessionDependency, UserIdDependency
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
    confirmed: Annotated[bool, Header(alias="X-User-Confirmed")] = False,
) -> HotwordMutationResponse:
    return await HotwordService().create(session, user_id, body, confirmed=confirmed)


@router.delete("/{hotword_id}", response_model=HotwordMutationResponse)
async def delete_hotword(
    hotword_id: str,
    session: SessionDependency,
    user_id: UserIdDependency,
    confirmed: Annotated[bool, Header(alias="X-User-Confirmed")] = False,
    second_confirmation: Annotated[bool, Header(alias="X-Second-Confirmation")] = False,
) -> HotwordMutationResponse:
    return await HotwordService().delete(
        session,
        user_id,
        hotword_id,
        confirmed=confirmed,
        second_confirmation=second_confirmation,
    )
