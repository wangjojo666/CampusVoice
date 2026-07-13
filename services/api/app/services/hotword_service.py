from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import InMemoryMetrics, observe_component
from app.repositories.hotwords import HotwordRepository
from app.schemas.domain import HotwordCreate, HotwordMutationResponse, HotwordView
from app.services.errors import (
    ConfirmationRequiredError,
    ConflictError,
    NotFoundError,
    VerificationFailedError,
)
from app.services.verification.service import VerificationService


class HotwordService:
    def __init__(self, metrics: InMemoryMetrics | None = None) -> None:
        self.repository = HotwordRepository()
        self.verifier = VerificationService()
        self.metrics = metrics

    async def create(
        self,
        session: AsyncSession,
        user_id: str,
        data: HotwordCreate,
        *,
        confirmed: bool,
    ) -> HotwordMutationResponse:
        if not confirmed:
            raise ConfirmationRequiredError(
                {"operation": "create_hotword", "required_confirmations": 1}
            )
        existing = await self.repository.find_same(session, user_id, data.term, data.category)
        if existing is not None:
            raise ConflictError(
                "duplicate_hotword",
                "The same hotword already exists",
                {"id": existing.id},
            )
        await session.rollback()
        async with session.begin():
            hotword = await self.repository.create(session, user_id, data)
            hotword_id = hotword.id
        with observe_component(self.metrics, "verification", "verify") as observation:
            report = await self.verifier.verify_hotword(
                session, user_id, hotword_id, data.model_dump(mode="json")
            )
            observation.error = not report.success
        record = HotwordView.model_validate(report.record) if report.record is not None else None
        await session.rollback()
        if not report.success:
            raise VerificationFailedError(report.as_dict())
        return HotwordMutationResponse(
            success=True,
            action="create_hotword",
            record_id=hotword_id,
            verified_fields=report.verified_fields,
            side_effects=list(report.side_effects),
            message="热词已创建并通过数据库验证",
            record=record,
        )

    async def delete(
        self,
        session: AsyncSession,
        user_id: str,
        hotword_id: str,
        *,
        confirmed: bool,
        second_confirmation: bool,
    ) -> HotwordMutationResponse:
        if not confirmed or not second_confirmation:
            raise ConfirmationRequiredError(
                {"operation": "delete_hotword", "required_confirmations": 2}
            )
        async with session.begin():
            hotword = await self.repository.get(session, user_id, hotword_id)
            if hotword is None:
                raise NotFoundError("hotword", hotword_id)
            await self.repository.delete(session, hotword)
        with observe_component(self.metrics, "verification", "verify") as observation:
            report = await self.verifier.verify_hotword(
                session, user_id, hotword_id, {}, should_exist=False
            )
            observation.error = not report.success
        await session.rollback()
        if not report.success:
            raise VerificationFailedError(report.as_dict())
        return HotwordMutationResponse(
            success=True,
            action="delete_hotword",
            record_id=hotword_id,
            verified_fields=report.verified_fields,
            side_effects=list(report.side_effects),
            message="热词已删除并通过数据库验证",
            record=None,
        )
