from fastapi import APIRouter, Response, status

from app.api.dependencies import (
    MetricsDependency,
    SessionDependency,
    SettingsDependency,
    UserIdDependency,
)
from app.repositories.actions import ActionRepository
from app.schemas.actions import (
    ActionPrepareRequest,
    CancelActionRequest,
    ConfirmActionRequest,
    ConfirmationChallenge,
    ExecutionResult,
    PendingActionView,
    UndoResult,
)
from app.security.confirmation import ConfirmationChallengeService
from app.services.actions.service import ActionService
from app.services.errors import NotFoundError

router = APIRouter(prefix="/actions", tags=["reliable-actions"])


def _service(settings: SettingsDependency, metrics: MetricsDependency) -> ActionService:
    assert settings.confirmation_secret is not None
    return ActionService(
        action_ttl_minutes=settings.action_ttl_minutes,
        undo_ttl_minutes=settings.undo_ttl_minutes,
        confirmation_service=ConfirmationChallengeService(
            settings.confirmation_secret.get_secret_value(),
            ttl_seconds=settings.confirmation_challenge_ttl_seconds,
        ),
        metrics=metrics,
    )


@router.post("/prepare", response_model=PendingActionView, status_code=status.HTTP_201_CREATED)
async def prepare_action(
    body: ActionPrepareRequest,
    session: SessionDependency,
    user_id: UserIdDependency,
    settings: SettingsDependency,
    metrics: MetricsDependency,
) -> PendingActionView:
    action = await _service(settings, metrics).prepare(session, user_id, body)
    return PendingActionView.model_validate(action)


@router.get("/{action_id}", response_model=PendingActionView)
async def get_action(
    action_id: str,
    session: SessionDependency,
    user_id: UserIdDependency,
) -> PendingActionView:
    action = await ActionRepository().get_pending(session, user_id, action_id)
    if action is None:
        raise NotFoundError("pending_action", action_id)
    return PendingActionView.model_validate(action)


@router.post("/{action_id}/confirm", response_model=PendingActionView)
async def confirm_action(
    action_id: str,
    body: ConfirmActionRequest,
    session: SessionDependency,
    user_id: UserIdDependency,
    settings: SettingsDependency,
    metrics: MetricsDependency,
) -> PendingActionView:
    action = await _service(settings, metrics).confirm(session, user_id, action_id, body)
    return PendingActionView.model_validate(action)


@router.post("/{action_id}/challenge", response_model=ConfirmationChallenge)
async def issue_confirmation_challenge(
    action_id: str,
    session: SessionDependency,
    user_id: UserIdDependency,
    settings: SettingsDependency,
    response: Response,
    metrics: MetricsDependency,
) -> ConfirmationChallenge:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return await _service(settings, metrics).issue_confirmation_challenge(
        session, user_id, action_id
    )


@router.post("/{action_id}/execute", response_model=ExecutionResult)
async def execute_action(
    action_id: str,
    session: SessionDependency,
    user_id: UserIdDependency,
    settings: SettingsDependency,
    metrics: MetricsDependency,
) -> ExecutionResult:
    return await _service(settings, metrics).execute(session, user_id, action_id)


@router.post("/{action_id}/cancel", response_model=PendingActionView)
async def cancel_action(
    action_id: str,
    body: CancelActionRequest,
    session: SessionDependency,
    user_id: UserIdDependency,
    settings: SettingsDependency,
    metrics: MetricsDependency,
) -> PendingActionView:
    action = await _service(settings, metrics).cancel(session, user_id, action_id, body)
    return PendingActionView.model_validate(action)


@router.post("/{action_id}/undo", response_model=UndoResult)
async def undo_action(
    action_id: str,
    session: SessionDependency,
    user_id: UserIdDependency,
    settings: SettingsDependency,
    metrics: MetricsDependency,
) -> UndoResult:
    return await _service(settings, metrics).undo(session, user_id, action_id)
