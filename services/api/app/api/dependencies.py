from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


SessionDependency = Annotated[AsyncSession, Depends(get_session)]


def get_runtime_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]


def current_user_id(settings: SettingsDependency) -> str:
    """Return the configured single-user identity.

    Client-controlled user IDs are intentionally not accepted. This is the MVP
    authorization boundary and prevents cross-user access when sample rows exist.
    """

    return settings.single_user_id


UserIdDependency = Annotated[str, Depends(current_user_id)]
