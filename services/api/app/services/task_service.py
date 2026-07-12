from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import PendingAction, Task
from app.models.enums import ActionType, TaskStatus
from app.repositories.tasks import TaskRepository
from app.schemas.actions import ActionPrepareRequest, ConfirmActionRequest, PendingActionView
from app.schemas.domain import TaskCreate, TaskMutationResponse, TaskUpdate, TaskView
from app.services.actions.service import ActionService
from app.services.errors import ConfirmationRequiredError, VerificationFailedError


class TaskService:
    def __init__(self, action_service: ActionService | None = None) -> None:
        self.repository = TaskRepository()
        self.actions = action_service or ActionService()

    async def list(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        status: TaskStatus | None,
        course: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Task], int]:
        return await self.repository.list(
            session,
            user_id,
            status=status,
            course=course,
            limit=limit,
            offset=offset,
        )

    async def create(
        self,
        session: AsyncSession,
        user_id: str,
        data: TaskCreate,
        *,
        confirmed: bool,
        idempotency_key: str | None,
    ) -> TaskMutationResponse:
        action = await self.actions.prepare(
            session,
            user_id,
            ActionPrepareRequest(
                action=ActionType.CREATE_TASK,
                payload=data.model_dump(mode="json"),
                idempotency_key=idempotency_key,
            ),
        )
        return await self._confirm_and_execute(session, user_id, action, confirmed)

    async def update(
        self,
        session: AsyncSession,
        user_id: str,
        task_id: str,
        data: TaskUpdate,
        *,
        confirmed: bool,
        idempotency_key: str | None,
    ) -> TaskMutationResponse:
        action = await self.actions.prepare(
            session,
            user_id,
            ActionPrepareRequest(
                action=ActionType.UPDATE_TASK,
                target_id=task_id,
                payload=data.model_dump(mode="json", exclude_unset=True),
                idempotency_key=idempotency_key,
            ),
        )
        return await self._confirm_and_execute(session, user_id, action, confirmed)

    async def prepare_delete(
        self,
        session: AsyncSession,
        user_id: str,
        task_id: str,
        *,
        idempotency_key: str | None,
    ) -> None:
        action = await self.actions.prepare(
            session,
            user_id,
            ActionPrepareRequest(
                action=ActionType.DELETE_TASK,
                target_id=task_id,
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
    ) -> TaskMutationResponse:
        if not confirmed or action.state.value == "needs_input":
            raise ConfirmationRequiredError(_action_dict(action))
        action = await self.actions.confirm(
            session,
            user_id,
            action.id,
            ConfirmActionRequest(confirmed=True, confirmation_token=f"direct-{uuid4().hex}"),
        )
        if action.state.value != "ready":
            raise ConfirmationRequiredError(_action_dict(action))
        result = await self.actions.execute(session, user_id, action.id)
        if not result.success:
            raise VerificationFailedError(result.model_dump(mode="json"))
        if result.record is not None and not isinstance(result.record, TaskView):
            raise VerificationFailedError(
                {"reason": "verified action returned the wrong record type"}
            )
        return TaskMutationResponse(
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
