from dataclasses import dataclass

from app.models.enums import ActionType, RiskLevel


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    level: RiskLevel
    factors: tuple[str, ...]
    required_confirmations: int


def assess_risk(
    action: ActionType,
    *,
    asr_confidence: float,
    missing_fields: list[str],
    has_conflict: bool,
    has_duplicate: bool,
    batch_size: int,
    overwrite_existing: bool,
    hard_to_undo: bool,
) -> RiskAssessment:
    """Calculate risk using deterministic, auditable rules only."""

    factors: list[str] = ["modifies_data"]
    high = False

    if action in {ActionType.DELETE_TASK, ActionType.DELETE_EVENT}:
        factors.append("deletes_data")
        high = True
    if batch_size > 1:
        factors.append("batch_operation")
        high = True
    if asr_confidence < 0.70:
        factors.append("low_asr_confidence")
        high = True
    if missing_fields:
        factors.append("missing_required_fields")
        high = True
    if has_conflict:
        factors.append("time_conflict")
        high = True
    if has_duplicate:
        factors.append("duplicate_record")
        high = True
    if overwrite_existing:
        factors.append("overwrites_existing_data")
        high = True
    if hard_to_undo:
        factors.append("hard_to_undo")
        high = True

    if high:
        return RiskAssessment(RiskLevel.HIGH, tuple(factors), 2)
    return RiskAssessment(RiskLevel.MEDIUM, tuple(factors), 1)
