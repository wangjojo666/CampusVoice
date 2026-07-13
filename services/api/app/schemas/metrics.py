from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HttpMetric(_StrictModel):
    method: str
    route: str
    status_family: str
    count: int = Field(ge=1)
    error_count: int = Field(ge=0)
    total_duration_ms: float = Field(ge=0)
    max_duration_ms: float = Field(ge=0)


class ComponentMetric(_StrictModel):
    component: Literal["asr", "intent", "retrieval", "llm", "action", "verification"]
    operation: str
    outcome: Literal["ok", "error"]
    count: int = Field(ge=1)
    error_count: int = Field(ge=0)
    total_duration_ms: float = Field(ge=0)
    max_duration_ms: float = Field(ge=0)


class MetricsResponse(_StrictModel):
    http: list[HttpMetric]
    components: list[ComponentMetric]
