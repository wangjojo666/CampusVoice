from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.enums import ActionType, EntityType, PendingActionState, RiskLevel
from app.schemas.common import StrictModel
from app.schemas.domain import EventView, TaskView


class ActionPrepareRequest(StrictModel):
    action: ActionType
    target_id: str | None = None
    target_title: str | None = Field(default=None, min_length=1, max_length=240)
    payload: dict[str, Any] = Field(default_factory=dict)
    asr_confidence: float = Field(default=1.0, ge=0, le=1)
    missing_fields: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    batch_size: int = Field(default=1, ge=1, le=100)
    overwrite_existing: bool = False
    hard_to_undo: bool = False
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=120)
    source_text: str | None = None
    corrected_text: str | None = None
    voice_session_id: str | None = None
    transcription_id: str | None = None


class PendingActionView(StrictModel):
    id: str
    action_type: ActionType
    entity_type: EntityType
    target_id: str | None
    payload: dict[str, Any]
    state: PendingActionState
    risk_level: RiskLevel
    risk_factors: list[str]
    missing_fields: list[str]
    ambiguities: list[str]
    blocking_reasons: list[str]
    diagnostics: dict[str, Any]
    required_confirmations: int
    confirmations_received: int
    expires_at: datetime
    attempt_count: int
    max_attempts: int
    last_error: str | None
    result: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class ConfirmActionRequest(StrictModel):
    confirmed: bool = True
    confirmation_token: str = Field(min_length=8, max_length=160)


class CancelActionRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=500)


class ExecutionResult(StrictModel):
    success: bool
    action: str
    record_id: str | None
    verified_fields: dict[str, bool]
    side_effects: list[str]
    message: str
    error: str | None = None
    retryable: bool = False
    action_id: str
    record: TaskView | EventView | None = None


class UndoResult(ExecutionResult):
    original_action: ActionType
