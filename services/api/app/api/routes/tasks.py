from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status

from app.api.dependencies import (
    MetricsDependency,
    SessionDependency,
    UserIdDependency,
    WriteChallengeDependency,
)
from app.models.enums import TaskStatus
from app.schemas.domain import (
    TaskCreate,
    TaskList,
    TaskMutationResponse,
    TaskUpdate,
    TaskView,
)
from app.services.actions.service import ActionService
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _mutation_service(metrics: MetricsDependency) -> TaskService:
    return TaskService(ActionService(metrics=metrics))


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
    _write_challenge: WriteChallengeDependency,
    metrics: MetricsDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskMutationResponse:
    return await _mutation_service(metrics).create(
        session,
        user_id,
        body,
        confirmed=True,
        idempotency_key=idempotency_key,
    )


@router.patch("/{task_id}", response_model=TaskMutationResponse)
async def update_task(
    task_id: str,
    body: TaskUpdate,
    session: SessionDependency,
    user_id: UserIdDependency,
    _write_challenge: WriteChallengeDependency,
    metrics: MetricsDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskMutationResponse:
    return await _mutation_service(metrics).update(
        session,
        user_id,
        task_id,
        body,
        confirmed=True,
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
    metrics: MetricsDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    await _mutation_service(metrics).prepare_delete(
        session, user_id, task_id, idempotency_key=idempotency_key
    )
    raise AssertionError("prepare_delete always raises a confirmation requirement")
