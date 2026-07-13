from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.common import StrictModel

PrivacyScope = Literal["business_data"]


class RetentionPolicy(StrictModel):
    transcription_days: int = Field(ge=1)
    correction_days: int = Field(ge=1)
    conversation_days: int = Field(ge=1)
    pending_action_days: int = Field(ge=1)
    audit_log_days: int = Field(ge=1)
    raw_audio_persisted: Literal[False] = False


class PrivacyExportResponse(StrictModel):
    generated_at: datetime
    scope: PrivacyScope = "business_data"
    user: dict[str, Any]
    retention_policy: RetentionPolicy
    data: dict[str, list[dict[str, Any]]]


class RetentionRunResponse(StrictModel):
    success: Literal[True] = True
    ran_at: datetime
    deleted_counts: dict[str, int]


class PrivacyDeletionChallengeResponse(StrictModel):
    id: str
    challenge: str
    scope: PrivacyScope
    expires_at: datetime


class PrivacyDeletionConfirmRequest(StrictModel):
    challenge: str = Field(min_length=32, max_length=512)
    scope: PrivacyScope = "business_data"
    confirmation: Literal["DELETE_MY_DATA"]


class PrivacyDeletionResult(StrictModel):
    success: Literal[True] = True
    scope: PrivacyScope
    deleted_counts: dict[str, int]
    verified: Literal[True] = True
