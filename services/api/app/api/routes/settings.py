from typing import Annotated

from fastapi import APIRouter, Header

from app.api.dependencies import SessionDependency, UserIdDependency
from app.schemas.settings import (
    UserSettingsMutationResponse,
    UserSettingsUpdate,
    UserSettingsView,
)
from app.services.settings_service import UserSettingsService

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=UserSettingsView)
async def get_user_settings(
    session: SessionDependency,
    user_id: UserIdDependency,
) -> UserSettingsView:
    return await UserSettingsService().get(session, user_id)


@router.patch("", response_model=UserSettingsMutationResponse)
async def update_user_settings(
    body: UserSettingsUpdate,
    session: SessionDependency,
    user_id: UserIdDependency,
    confirmed: Annotated[bool, Header(alias="X-User-Confirmed")] = False,
) -> UserSettingsMutationResponse:
    return await UserSettingsService().update(
        session,
        user_id,
        body,
        confirmed=confirmed,
    )
