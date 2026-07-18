import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.types import utc_now
from app.models.entities import OidcLoginTransaction, User
from app.services.privacy.service import PrivacyService

logger = logging.getLogger("campusvoice.retention")


class RetentionExecutor:
    """Run the idempotent user-scoped retention service with bounded retries."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._sleep = sleep

    async def run_once(self) -> dict[str, dict[str, int]]:
        async with self._session_factory() as session:
            user_ids = list(await session.scalars(select(User.id).order_by(User.id)))
            await session.execute(
                delete(OidcLoginTransaction).where(OidcLoginTransaction.expires_at <= utc_now())
            )
            await session.commit()
        service = PrivacyService(self._session_factory, self._settings)
        results: dict[str, dict[str, int]] = {}
        for user_id in user_ids:
            outcome = await service.run_retention(user_id)
            results[user_id] = dict(outcome.deleted_counts)
        return results

    async def run_with_retries(self) -> dict[str, dict[str, int]]:
        attempts = self._settings.retention_scheduler_max_retries + 1
        for attempt in range(attempts):
            try:
                return await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                exception_type = type(exc).__name__
                if attempt + 1 >= attempts:
                    logger.error(
                        "retention_run_failed",
                        extra={
                            "attempt": attempt + 1,
                            "attempts": attempts,
                            "exception_type": exception_type,
                        },
                    )
                    raise
                delay = self._settings.retention_scheduler_retry_base_seconds * (2**attempt)
                logger.warning(
                    "retention_run_retry",
                    extra={
                        "attempt": attempt + 1,
                        "attempts": attempts,
                        "delay_seconds": delay,
                        "exception_type": exception_type,
                    },
                )
                await self._sleep(delay)
        raise RuntimeError("unreachable retention retry state")


def retention_summary(results: dict[str, dict[str, int]]) -> dict[str, Any]:
    """Return non-identifying aggregate output for the one-shot executor."""

    totals: dict[str, int] = {}
    for counts in results.values():
        for table, count in counts.items():
            totals[table] = totals.get(table, 0) + count
    return {"users_processed": len(results), "deleted_counts": totals}
