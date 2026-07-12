from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import UserSettings


class UserSettingsRepository:
    async def get(
        self, session: AsyncSession, user_id: str, *, lock: bool = False
    ) -> UserSettings | None:
        statement = select(UserSettings).where(UserSettings.user_id == user_id)
        if lock:
            statement = statement.with_for_update()
        settings: UserSettings | None = await session.scalar(statement)
        return settings
