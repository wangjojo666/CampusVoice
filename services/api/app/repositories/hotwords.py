from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Hotword
from app.models.enums import HotwordCategory
from app.schemas.domain import HotwordCreate


class HotwordRepository:
    async def list(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        category: HotwordCategory | None = None,
        active_only: bool = True,
        limit: int = 500,
        offset: int = 0,
    ) -> tuple[list[Hotword], int]:
        filters = [Hotword.user_id == user_id]
        if category is not None:
            filters.append(Hotword.category == category)
        if active_only:
            filters.append(Hotword.is_active.is_(True))
        count = await session.scalar(select(func.count(Hotword.id)).where(*filters))
        result = await session.scalars(
            select(Hotword)
            .where(*filters)
            .order_by(Hotword.category, Hotword.term)
            .limit(limit)
            .offset(offset)
        )
        return list(result), int(count or 0)

    async def get(self, session: AsyncSession, user_id: str, hotword_id: str) -> Hotword | None:
        hotword: Hotword | None = await session.scalar(
            select(Hotword).where(Hotword.id == hotword_id, Hotword.user_id == user_id)
        )
        return hotword

    async def find_same(
        self,
        session: AsyncSession,
        user_id: str,
        term: str,
        category: HotwordCategory,
    ) -> Hotword | None:
        hotword: Hotword | None = await session.scalar(
            select(Hotword).where(
                Hotword.user_id == user_id,
                func.lower(Hotword.term) == term.strip().lower(),
                Hotword.category == category,
            )
        )
        return hotword

    async def create(self, session: AsyncSession, user_id: str, data: HotwordCreate) -> Hotword:
        hotword = Hotword(user_id=user_id, **data.model_dump())
        session.add(hotword)
        await session.flush()
        return hotword

    async def delete(self, session: AsyncSession, hotword: Hotword) -> None:
        await session.delete(hotword)
        await session.flush()
