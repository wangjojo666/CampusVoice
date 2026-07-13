from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status
from pydantic import AwareDatetime

from app.api.dependencies import (
    MetricsDependency,
    SessionDependency,
    UserIdDependency,
    WriteChallengeDependency,
)
from app.repositories.events import EventRepository
from app.schemas.domain import (
    ConflictCheckRequest,
    ConflictCheckResponse,
    EventCreate,
    EventList,
    EventMutationResponse,
    EventUpdate,
    EventView,
)
from app.services.actions.service import ActionService
from app.services.event_service import EventService

router = APIRouter(prefix="/events", tags=["events"])


def _mutation_service(metrics: MetricsDependency) -> EventService:
    return EventService(ActionService(metrics=metrics))


@router.get("", response_model=EventList)
async def list_events(
    session: SessionDependency,
    user_id: UserIdDependency,
    starts_after: Annotated[AwareDatetime | None, Query()] = None,
    starts_before: Annotated[AwareDatetime | None, Query()] = None,
    course: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EventList:
    items, total = await EventRepository().list(
        session,
        user_id,
        starts_after=starts_after,
        starts_before=starts_before,
        course=course,
        limit=limit,
        offset=offset,
    )
    return EventList(items=[EventView.model_validate(item) for item in items], total=total)


@router.post("", response_model=EventMutationResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    body: EventCreate,
    session: SessionDependency,
    user_id: UserIdDependency,
    _write_challenge: WriteChallengeDependency,
    metrics: MetricsDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> EventMutationResponse:
    return await _mutation_service(metrics).create(
        session,
        user_id,
        body,
        confirmed=True,
        idempotency_key=idempotency_key,
    )


@router.patch("/{event_id}", response_model=EventMutationResponse)
async def update_event(
    event_id: str,
    body: EventUpdate,
    session: SessionDependency,
    user_id: UserIdDependency,
    _write_challenge: WriteChallengeDependency,
    metrics: MetricsDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> EventMutationResponse:
    return await _mutation_service(metrics).update(
        session,
        user_id,
        event_id,
        body,
        confirmed=True,
        idempotency_key=idempotency_key,
    )


@router.delete(
    "/{event_id}",
    status_code=status.HTTP_428_PRECONDITION_REQUIRED,
    responses={428: {"description": "Two-step confirmation required"}},
)
async def delete_event(
    event_id: str,
    session: SessionDependency,
    user_id: UserIdDependency,
    metrics: MetricsDependency,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    await _mutation_service(metrics).prepare_delete(
        session, user_id, event_id, idempotency_key=idempotency_key
    )
    raise AssertionError("prepare_delete always raises a confirmation requirement")


@router.post("/check-conflict", response_model=ConflictCheckResponse)
async def check_event_conflict(
    body: ConflictCheckRequest,
    session: SessionDependency,
    user_id: UserIdDependency,
) -> ConflictCheckResponse:
    conflicts = await EventRepository().conflicts(
        session,
        user_id,
        start_at=body.start_at,
        end_at=body.end_at,
        exclude_id=body.exclude_event_id,
    )
    return ConflictCheckResponse(
        has_conflict=bool(conflicts),
        conflicts=[EventView.model_validate(item) for item in conflicts],
    )
