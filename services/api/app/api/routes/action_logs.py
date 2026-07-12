from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import SessionDependency, UserIdDependency
from app.repositories.actions import ActionRepository
from app.schemas.domain import ActionLogList, ActionLogView

router = APIRouter(prefix="/action-logs", tags=["action-logs"])


@router.get("", response_model=ActionLogList)
async def list_action_logs(
    session: SessionDependency,
    user_id: UserIdDependency,
    success: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ActionLogList:
    items, total = await ActionRepository().list_logs(
        session, user_id, success=success, limit=limit, offset=offset
    )
    return ActionLogList(items=[ActionLogView.model_validate(item) for item in items], total=total)
