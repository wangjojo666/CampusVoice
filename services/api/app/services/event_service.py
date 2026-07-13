from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import PendingAction
from app.models.enums import ActionType
from app.repositories.events import EventRepository
from app.schemas.actions import ActionPrepareRequest, PendingActionView
from app.schemas.domain import EventCreate, EventMutationResponse, EventUpdate, EventView
from app.services.actions.service import ActionService
from app.services.errors import ConfirmationRequiredError, VerificationFailedError
from app.services.lineage import validate_notice_lineage


class EventService:
    def __init__(self, action_service: ActionService | None = None) -> None:
        self.repository = EventRepository()
        self.actions = action_service or ActionService()

    async def create(
        self,
        session: AsyncSession,
        user_id: str,
        data: EventCreate,
        *,
        confirmed: bool,
        idempotency_key: str | None,
    ) -> EventMutationResponse:
        await validate_notice_lineage(
            session,
            user_id,
            document_id=data.source_document_id,
            chunk_id=data.source_chunk_id,
            claim_id=data.source_claim_id,
        )
        action = await self.actions.prepare(
            session,
            user_id,
            ActionPrepareRequest(
                action=ActionType.CREATE_EVENT,
                payload=data.model_dump(mode="json", exclude={"allow_conflict"}),
                overwrite_existing=data.allow_conflict,
                idempotency_key=idempotency_key,
            ),
        )
        return await self._confirm_and_execute(session, user_id, action, confirmed)

    async def update(
        self,
        session: AsyncSession,
        user_id: str,
        event_id: str,
        data: EventUpdate,
        *,
        confirmed: bool,
        idempotency_key: str | None,
    ) -> EventMutationResponse:
        await validate_notice_lineage(
            session,
            user_id,
            document_id=data.source_document_id,
            chunk_id=data.source_chunk_id,
            claim_id=data.source_claim_id,
        )
        action = await self.actions.prepare(
            session,
            user_id,
            ActionPrepareRequest(
                action=ActionType.UPDATE_EVENT,
                target_id=event_id,
                payload=data.model_dump(
                    mode="json", exclude_unset=True, exclude={"allow_conflict"}
                ),
                overwrite_existing=data.allow_conflict,
                idempotency_key=idempotency_key,
            ),
        )
        return await self._confirm_and_execute(session, user_id, action, confirmed)

    async def prepare_delete(
        self,
        session: AsyncSession,
        user_id: str,
        event_id: str,
        *,
        idempotency_key: str | None,
    ) -> None:
        action = await self.actions.prepare(
            session,
            user_id,
            ActionPrepareRequest(
                action=ActionType.DELETE_EVENT,
                target_id=event_id,
                idempotency_key=idempotency_key,
            ),
        )
        raise ConfirmationRequiredError(_action_dict(action))

    async def _confirm_and_execute(
        self,
        session: AsyncSession,
        user_id: str,
        action: PendingAction,
        confirmed: bool,
    ) -> EventMutationResponse:
        if (
            not confirmed
            or action.state.value == "needs_input"
            or action.required_confirmations != 1
        ):
            raise ConfirmationRequiredError(_action_dict(action))
        action = await self.actions.confirm_direct(session, user_id, action.id)
        if action.state.value != "ready":
            raise ConfirmationRequiredError(_action_dict(action))
        result = await self.actions.execute(session, user_id, action.id)
        if not result.success:
            raise VerificationFailedError(result.model_dump(mode="json"))
        if result.record is not None and not isinstance(result.record, EventView):
            raise VerificationFailedError(
                {"reason": "verified action returned the wrong record type"}
            )
        return EventMutationResponse(
            success=True,
            action=result.action,
            record_id=result.record_id or "",
            verified_fields=result.verified_fields,
            side_effects=result.side_effects,
            message=result.message,
            record=result.record,
        )


def _action_dict(action: PendingAction) -> dict[str, object]:
    return PendingActionView.model_validate(action).model_dump(mode="json")
