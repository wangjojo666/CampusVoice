from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.common import StrictModel


class WebSocketTicketResponse(StrictModel):
    ticket: str
    expires_at: datetime


class WeChatLoginRequest(StrictModel):
    code: str = Field(min_length=8, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


class WeChatLoginResponse(StrictModel):
    session_token: str = Field(
        min_length=38,
        max_length=262,
        pattern=r"^cvwx1\.[A-Za-z0-9_-]{32,256}$",
    )
    expires_at: datetime
    display_name: str


class WeChatLogoutResponse(StrictModel):
    success: Literal[True] = True


class WriteChallengeIssueRequest(StrictModel):
    method: Literal["POST", "PATCH", "DELETE"]
    path: str = Field(min_length=1, max_length=500)
    body: Any = None


class WriteChallengeAdvanceRequest(StrictModel):
    challenge: str = Field(min_length=32, max_length=256)


class WriteChallengeResponse(StrictModel):
    challenge: str
    stage: int = Field(ge=1, le=2)
    required_stages: int = Field(ge=1, le=2)
    expires_at: datetime


class OidcSessionResponse(StrictModel):
    authenticated: bool
    user_id: str
    display_name: str
    roles: list[str]
    expires_at: datetime | None = None


class OidcLogoutResponse(StrictModel):
    logout_url: str
