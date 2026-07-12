from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HotwordSource(StrEnum):
    COURSE = "course"
    COURSE_CODE = "course_code"
    TEACHER = "teacher"
    AI_TERM = "ai_term"
    DOCUMENT = "document"
    USER = "user"


class CorrectionPolicy(StrEnum):
    AUTO_APPLY = "auto_apply"
    SUGGEST = "suggest"
    CLARIFY = "clarify"
    UNCHANGED = "unchanged"


class CorrectionTerm(_StrictModel):
    term: str = Field(min_length=1, max_length=200)
    source: HotwordSource
    aliases: list[str] = Field(default_factory=list, max_length=30)
    context_keywords: list[str] = Field(default_factory=list, max_length=30)


class CriticalSpan(_StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    kind: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_bounds(self) -> "CriticalSpan":
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class CandidateScore(_StrictModel):
    edit_similarity: float = Field(ge=0, le=1)
    pronunciation_similarity: float = Field(ge=0, le=1)
    asr_uncertainty: float = Field(ge=0, le=1)
    course_relevance: float = Field(ge=0, le=1)
    document_relevance: float = Field(ge=0, le=1)
    recent_context_relevance: float = Field(ge=0, le=1)
    semantic_relevance: float = Field(ge=0, le=1)
    total: float = Field(ge=0, le=1)


class CorrectionCandidate(_StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    original: str
    replacement: str
    source: HotwordSource
    score: CandidateScore
    critical_field: bool
    critical_kind: str | None = None
    policy: CorrectionPolicy
    reason: str


class CorrectionModification(_StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    original: str
    replacement: str
    policy: CorrectionPolicy
    confidence: float = Field(ge=0, le=1)
    reason: str
    critical_field: bool


class CorrectionRecord(_StrictModel):
    id: str = Field(default_factory=lambda: f"cor_{uuid4().hex}")
    original_text: str
    corrected_text: str
    modifications: list[CorrectionModification]
    candidates: list[CorrectionCandidate]
    reason: str
    confidence: float = Field(ge=0, le=1)
    user_confirmed: bool


class CorrectionRequest(_StrictModel):
    transcription_id: str | None = Field(default=None, max_length=64)
    text: str = Field(min_length=1, max_length=10_000)
    asr_confidence: float = Field(ge=0, le=1)
    terms: list[CorrectionTerm] = Field(default_factory=list, max_length=2_000)
    critical_spans: list[CriticalSpan] = Field(default_factory=list, max_length=100)
    current_courses: list[str] = Field(default_factory=list, max_length=100)
    document_terms: list[str] = Field(default_factory=list, max_length=1_000)
    recent_context: list[str] = Field(default_factory=list, max_length=20)


class CorrectionResponse(_StrictModel):
    record: CorrectionRecord
    requires_user_input: bool


class CorrectionDecisionRequest(_StrictModel):
    corrected_text: str = Field(min_length=1, max_length=10_000)
    confirmed: bool


class CorrectionDecisionResponse(_StrictModel):
    id: str
    corrected_text: str
    user_confirmed: bool
