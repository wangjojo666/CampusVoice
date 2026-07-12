from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentFileType(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    MARKDOWN = "md"


class DocumentMetadata(_StrictModel):
    title: str = Field(min_length=1, max_length=500)
    department: str | None = Field(default=None, max_length=300)
    publish_date: date | None = None
    applicable_group: str | None = Field(default=None, max_length=500)
    source_url: HttpUrl | None = None
    version: str | None = Field(default=None, max_length=100)
    file_type: DocumentFileType


class DocumentRecord(_StrictModel):
    id: str
    user_id: str
    metadata: DocumentMetadata
    content_sha256: str
    status: str
    chunk_count: int = Field(ge=0)
    created_at: datetime


class DocumentChunk(_StrictModel):
    id: str
    document_id: str
    ordinal: int = Field(ge=0)
    content: str = Field(min_length=1)
    page_number: int | None = Field(default=None, ge=1)
    embedding: list[float] | None = Field(default=None, exclude=True, repr=False)


class KnowledgeSearchRequest(_StrictModel):
    query: str = Field(min_length=1, max_length=2_000)
    top_k: int = Field(default=5, ge=1, le=20)
    min_similarity: float = Field(default=0.08, ge=0, le=1)
    version: str | None = Field(default=None, max_length=100)
    applicable_group: str | None = Field(default=None, max_length=500)


class KnowledgeCitation(_StrictModel):
    document_id: str
    chunk_id: str
    original_text: str
    page_number: int | None
    similarity: float = Field(ge=0, le=1)
    file_title: str
    publish_date: date | None
    version: str | None
    applicable_group: str | None


class VersionConflict(_StrictModel):
    title: str
    document_ids: list[str]
    versions: list[str]
    message: str


class ApplicabilityConflict(_StrictModel):
    title: str
    document_ids: list[str]
    applicable_groups: list[str]
    message: str


class KnowledgeSearchResponse(_StrictModel):
    query: str
    results: list[KnowledgeCitation]
    version_conflicts: list[VersionConflict] = Field(default_factory=list)
    applicability_conflicts: list[ApplicabilityConflict] = Field(default_factory=list)


class KnowledgeAskRequest(_StrictModel):
    question: str = Field(min_length=1, max_length=2_000)
    top_k: int = Field(default=5, ge=1, le=10)
    min_similarity: float = Field(default=0.12, ge=0, le=1)
    version: str | None = Field(default=None, max_length=100)
    applicable_group: str | None = Field(default=None, max_length=500)


class KnowledgeAskResponse(_StrictModel):
    question: str
    answer: str
    sufficient_evidence: bool
    insufficiency_reason: str | None = None
    citations: list[KnowledgeCitation]
    version_conflicts: list[VersionConflict] = Field(default_factory=list)
    applicability_conflicts: list[ApplicabilityConflict] = Field(default_factory=list)
