from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoticeSeriesCreate(StrictModel):
    canonical_key: str = Field(min_length=2, max_length=240, pattern=r"^[\w.:-]+$")
    title: str = Field(min_length=2, max_length=240)
    department: str | None = Field(default=None, max_length=160)
    source_key: str | None = Field(default=None, max_length=240)


class NoticeSeriesView(StrictModel):
    id: str
    canonical_key: str
    normalized_title: str
    department: str | None
    source_key: str | None
    version_count: int = 0
    current_document_id: str | None = None
    created_at: datetime
    updated_at: datetime


class NoticeVersionCreate(StrictModel):
    title: str = Field(min_length=2, max_length=240)
    content: str = Field(min_length=10, max_length=200_000)
    revision_number: int = Field(ge=1, le=10_000)
    version_label: str = Field(min_length=1, max_length=80)
    supersedes_document_id: str | None = None
    department: str | None = Field(default=None, max_length=160)
    publish_date: date | None = None
    effective_at: datetime | None = None
    applicable_group: str | None = Field(default=None, max_length=240)
    source_url: str | None = Field(default=None, max_length=2_000)
    ingest_source: Literal["manual", "seed", "upload", "api"] = "api"


class NoticeClaimView(StrictModel):
    id: str
    document_id: str
    chunk_id: str
    claim_key: str
    claim_type: str
    value: dict[str, Any]
    normalized_value: dict[str, Any]
    audience_rule: dict[str, Any]
    confidence: float
    evidence_text: str
    evidence_start: int
    evidence_end: int
    extractor_version: str
    review_state: str


class NoticeVersionView(StrictModel):
    id: str
    series_id: str
    supersedes_document_id: str | None
    revision_number: int
    title: str
    version_label: str
    effective_at: datetime | None
    publish_date: date | None
    is_current: bool
    ingest_source: str
    claims: list[NoticeClaimView] = Field(default_factory=list)
    created_at: datetime


class NoticeTimelineView(StrictModel):
    series: NoticeSeriesView
    versions: list[NoticeVersionView]


class ChangeEvidenceView(StrictModel):
    claim_id: str
    document_id: str
    chunk_id: str
    value: dict[str, Any]
    normalized_value: dict[str, Any]
    evidence_text: str
    evidence_start: int
    evidence_end: int


class NoticeChangeItemView(StrictModel):
    id: str
    claim_key: str
    change_type: Literal["added", "removed", "changed"]
    severity: Literal["low", "medium", "high"]
    confidence: float
    review_state: str
    before: ChangeEvidenceView | None
    after: ChangeEvidenceView | None


class NoticeChangeSetView(StrictModel):
    id: str
    series_id: str
    from_document_id: str
    to_document_id: str
    algorithm_version: str
    status: str
    items: list[NoticeChangeItemView]
    created_at: datetime


class ChangeReviewRequest(StrictModel):
    decision: Literal["approved", "rejected"]


class ImpactCaseView(StrictModel):
    id: str
    change_item_id: str
    entity_type: Literal["task", "event"]
    entity_id: str
    entity_version: int
    reason: str
    severity: str
    current_snapshot: dict[str, Any]
    proposed_patch: dict[str, Any]
    recommended_action: Literal["apply", "keep", "cancel", "manual_review"]
    requires_manual_review: bool
    status: str
    migration_plan_id: str | None
    detected_at: datetime
    resolved_at: datetime | None


class ImpactListView(StrictModel):
    items: list[ImpactCaseView]
    total: int


class MigrationItemView(StrictModel):
    id: str
    entity_type: Literal["task", "event"]
    entity_id: str
    expected_version: int
    before: dict[str, Any]
    after: dict[str, Any]
    source_claim_ids: list[str]
    verification: dict[str, Any]
    execute_verification: dict[str, Any] = Field(default_factory=dict)
    undo_verification: dict[str, Any] = Field(default_factory=dict)


class MigrationPlanView(StrictModel):
    id: str
    change_set_id: str
    status: str
    risk_level: Literal["low", "medium", "high"]
    required_confirmations: int
    conflicts: list[dict[str, Any]]
    items: list[MigrationItemView]
    verification: dict[str, Any]
    execute_receipt: dict[str, Any] = Field(default_factory=dict)
    undo_receipt: dict[str, Any] = Field(default_factory=dict)
    generation: int
    version: int
    executed_at: datetime | None
    undone_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MigrationExecuteRequest(StrictModel):
    plan_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=120)
    allow_conflicts: bool = False
    confirmation_stages: Literal[1, 2]

    @model_validator(mode="after")
    def conflict_override_requires_two_stages(self) -> "MigrationExecuteRequest":
        if self.allow_conflicts and self.confirmation_stages != 2:
            raise ValueError("conflict override requires two confirmation stages")
        return self


class MigrationUndoRequest(StrictModel):
    plan_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=120)
    confirmation_stages: Literal[2]


class VerificationReceiptView(StrictModel):
    plan_id: str
    status: str
    operation: Literal["execute", "undo"]
    verified_count: int
    total_count: int
    all_verified: bool
    items: list[MigrationItemView]
    verified_at: datetime


class RadarCardView(StrictModel):
    card_type: Literal["new_notice", "version_change", "upcoming_deadline", "needs_review"]
    change_set_id: str | None
    document_id: str | None = None
    series_id: str
    title: str
    from_revision: int
    to_revision: int
    change_count: int
    affected_tasks: int
    affected_events: int
    needs_review: bool
    applicability: Literal["applicable", "not_applicable", "needs_review"]
    applicability_reason: str | None = None
    deadline_at: datetime | None = None
    message: str
    created_at: datetime


class RadarView(StrictModel):
    items: list[RadarCardView]
    total: int
