from typing import Annotated

from fastapi import APIRouter, Header, Response

from app.api.dependencies import SessionDependency, SettingsDependency, UserIdDependency
from app.schemas.auth import (
    WebSocketTicketResponse,
    WriteChallengeAdvanceRequest,
    WriteChallengeIssueRequest,
    WriteChallengeResponse,
)
from app.security.websocket_tickets import issue_websocket_ticket
from app.security.write_challenges import (
    IssuedWriteChallenge,
    advance_write_challenge,
    issue_write_challenge,
)
from app.services.errors import DomainError

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/ws-ticket", response_model=WebSocketTicketResponse)
async def create_websocket_ticket(
    user_id: UserIdDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    response: Response,
    origin: Annotated[str | None, Header(alias="Origin")] = None,
) -> WebSocketTicketResponse:
    _disable_credential_caching(response)
    if origin is None or origin not in settings.cors_origins:
        raise DomainError(
            "origin_not_allowed",
            "A configured browser Origin is required for a WebSocket ticket",
            status_code=403,
        )
    raw, record = issue_websocket_ticket(
        user_id=user_id,
        origin=origin,
        ttl_seconds=settings.websocket_ticket_ttl_seconds,
    )
    session.add(record)
    await session.commit()
    return WebSocketTicketResponse(ticket=raw, expires_at=record.expires_at)


@router.post("/write-challenges", response_model=WriteChallengeResponse)
async def create_write_challenge(
    body: WriteChallengeIssueRequest,
    user_id: UserIdDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    response: Response,
) -> WriteChallengeResponse:
    _disable_credential_caching(response)
    issued = await issue_write_challenge(
        session,
        user_id=user_id,
        method=body.method,
        path=body.path,
        body=body.body,
        api_prefix=settings.api_prefix,
        ttl_seconds=settings.confirmation_challenge_ttl_seconds,
    )
    return _write_challenge_response(issued)


@router.post("/write-challenges/advance", response_model=WriteChallengeResponse)
async def advance_write_challenge_stage(
    body: WriteChallengeAdvanceRequest,
    user_id: UserIdDependency,
    session: SessionDependency,
    response: Response,
) -> WriteChallengeResponse:
    _disable_credential_caching(response)
    issued = await advance_write_challenge(
        session,
        user_id=user_id,
        challenge=body.challenge,
    )
    return _write_challenge_response(issued)


def _write_challenge_response(issued: IssuedWriteChallenge) -> WriteChallengeResponse:
    return WriteChallengeResponse(
        challenge=issued.challenge,
        stage=issued.stage,
        required_stages=issued.required_stages,
        expires_at=issued.expires_at,
    )


def _disable_credential_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
