from typing import Any

from pydantic import ValidationError

from app.models.enums import ActionType, EntityType
from app.schemas.domain import EventDraft, TaskDraft
from app.services.errors import DomainError

PayloadModel = TaskDraft | EventDraft


def entity_for_action(action: ActionType) -> EntityType:
    if action in {ActionType.CREATE_TASK, ActionType.UPDATE_TASK, ActionType.DELETE_TASK}:
        return EntityType.TASK
    return EntityType.EVENT


def parse_payload(action: ActionType, payload: dict[str, Any]) -> PayloadModel:
    try:
        if action in {ActionType.CREATE_TASK, ActionType.UPDATE_TASK}:
            return TaskDraft.model_validate(payload)
        if action in {ActionType.CREATE_EVENT, ActionType.UPDATE_EVENT}:
            return EventDraft.model_validate(payload)
        if payload:
            raise DomainError(
                "invalid_action_payload",
                "Delete actions do not accept mutable fields",
                status_code=422,
                details={"unexpected_fields": sorted(payload)},
            )
        return TaskDraft() if action == ActionType.DELETE_TASK else EventDraft()
    except ValidationError as exc:
        raise DomainError(
            "invalid_action_payload",
            "The action payload does not match the required schema",
            status_code=422,
            details={"errors": exc.errors(include_url=False)},
        ) from exc


def missing_required_fields(
    action: ActionType,
    target_id: str | None,
    payload: PayloadModel,
    declared_missing: list[str],
) -> list[str]:
    missing = set(declared_missing)
    values = payload.model_dump(exclude_unset=True)

    if action == ActionType.CREATE_TASK and not values.get("title"):
        missing.add("title")
    elif action == ActionType.CREATE_EVENT:
        if not values.get("title"):
            missing.add("title")
        if not values.get("start_at"):
            missing.add("start_at")
    elif (
        action
        in {
            ActionType.UPDATE_TASK,
            ActionType.DELETE_TASK,
            ActionType.UPDATE_EVENT,
            ActionType.DELETE_EVENT,
        }
        and not target_id
    ):
        missing.add("target_id")

    if action in {ActionType.UPDATE_TASK, ActionType.UPDATE_EVENT} and not (
        payload.model_fields_set - {"expected_version"}
    ):
        missing.add("fields_to_update")

    return sorted(missing)
