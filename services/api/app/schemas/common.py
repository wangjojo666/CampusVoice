from typing import Any

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class ErrorDetail(StrictModel):
    code: str
    message: str
    details: dict[str, Any] = {}


class ErrorResponse(StrictModel):
    error: ErrorDetail
