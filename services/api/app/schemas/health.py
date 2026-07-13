from typing import Literal

from pydantic import BaseModel, ConfigDict


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthCheck(_StrictModel):
    status: Literal["ok", "error", "disabled"]
    message: str


class LivenessResponse(_StrictModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


class ReadinessResponse(_StrictModel):
    status: Literal["ok", "error"]
    service: str
    version: str
    checks: dict[str, HealthCheck]


# Backward-compatible name for downstream imports while /api/health transitions
# to the readiness contract.
HealthResponse = ReadinessResponse
