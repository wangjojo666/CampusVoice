from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status

from app.api.dependencies import SessionDependency, UserIdDependency
from app.models.enums import TaskStatus
from app.schemas.domain import (
    TaskCreate,
    TaskList,
    TaskMutationResponse,
    TaskUpdate,
    TaskView,
)
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=TaskList)
async def list_tasks(
    session: SessionDependency,
    user_id: UserIdDependency,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    course: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskList:
    items, total = await TaskService().list(
        session,
        user_id,
        status=task_status,
        course=course,
        limit=limit,
        offset=offset,
    )
    return TaskList(items=[TaskView.model_validate(item) for item in items], total=total)


@router.post("", response_model=TaskMutationResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskCreate,
    session: SessionDependency,
    user_id: UserIdDependency,
    confirmed: Annotated[bool, Header(alias="X-User-Confirmed")] = False,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskMutationResponse:
    return await TaskService().create(
        session,
        user_id,
        body,
        confirmed=confirmed,
        idempotency_key=idempotency_key,
    )


@router.patch("/{task_id}", response_model=TaskMutationResponse)
async def update_task(
    task_id: str,
    body: TaskUpdate,
    session: SessionDependency,
    user_id: UserIdDependency,
    confirmed: Annotated[bool, Header(alias="X-User-Confirmed")] = False,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskMutationResponse:
    return await TaskService().update(
        session,
        user_id,
        task_id,
        body,
        confirmed=confirmed,
        idempotency_key=idempotency_key,
    )


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_428_PRECONDITION_REQUIRED,
    responses={428: {"description": "Two-step confirmation required"}},
)
async def delete_task(
    task_id: str,
    session: SessionDependency,
    user_id: UserIdDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    await TaskService().prepare_delete(session, user_id, task_id, idempotency_key=idempotency_key)
    raise AssertionError("prepare_delete always raises a confirmation requirement")
