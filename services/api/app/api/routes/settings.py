from fastapi import APIRouter

from app.api.dependencies import (
    SessionDependency,
    SettingsDependency,
    UserIdDependency,
    WriteChallengeDependency,
)
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
    settings: SettingsDependency,
) -> UserSettingsView:
    return await UserSettingsService().get(session, user_id, settings)


@router.patch("", response_model=UserSettingsMutationResponse)
async def update_user_settings(
    body: UserSettingsUpdate,
    session: SessionDependency,
    user_id: UserIdDependency,
    settings: SettingsDependency,
    _write_challenge: WriteChallengeDependency,
) -> UserSettingsMutationResponse:
    return await UserSettingsService().update(
        session,
        user_id,
        body,
        settings,
        confirmed=True,
    )
