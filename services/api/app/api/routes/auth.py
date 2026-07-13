from datetime import timedelta
from secrets import token_urlsafe
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, or_, update

from app.api.dependencies import (
    AuthPrincipalDependency,
    SessionDependency,
    SettingsDependency,
    UserIdDependency,
    provision_principal,
)
from app.core.config import Settings
from app.db.types import utc_now
from app.models.entities import OidcLoginTransaction, OidcSession
from app.schemas.auth import (
    OidcLogoutResponse,
    OidcSessionResponse,
    WebSocketTicketResponse,
    WriteChallengeAdvanceRequest,
    WriteChallengeIssueRequest,
    WriteChallengeResponse,
)
from app.security.oidc import (
    OidcClient,
    OidcError,
    consume_oidc_transaction,
    token_hash,
)
from app.security.websocket_tickets import issue_websocket_ticket
from app.security.write_challenges import (
    IssuedWriteChallenge,
    advance_write_challenge,
    issue_write_challenge,
)
from app.services.errors import DomainError

router = APIRouter(prefix="/auth", tags=["authentication"])


def _oidc_client(request: Request) -> OidcClient:
    client: OidcClient | None = getattr(request.app.state, "oidc_client", None)
    if client is None:
        raise DomainError(
            "oidc_not_configured", "OIDC authentication is not enabled", status_code=404
        )
    return client


@router.get("/login", include_in_schema=False)
async def oidc_login(
    request: Request,
    session: SessionDependency,
    settings: SettingsDependency,
) -> RedirectResponse:
    client = _oidc_client(request)
    flow = token_urlsafe(32)
    state = token_urlsafe(32)
    nonce = token_urlsafe(32)
    verifier = token_urlsafe(64)
    now = utc_now()
    session.add(
        OidcLoginTransaction(
            flow_hash=token_hash(flow),
            state_hash=token_hash(state),
            nonce=nonce,
            code_verifier=verifier,
            expires_at=now + timedelta(seconds=settings.oidc_login_ttl_seconds),
            created_at=now,
        )
    )
    previous_flow = request.cookies.get(settings.oidc_flow_cookie_name)
    cleanup_conditions = [OidcLoginTransaction.expires_at <= now]
    if previous_flow:
        cleanup_conditions.append(OidcLoginTransaction.flow_hash == token_hash(previous_flow))
    await session.execute(delete(OidcLoginTransaction).where(or_(*cleanup_conditions)))
    await session.commit()
    try:
        authorization_url = await client.authorization_url(
            state=state, nonce=nonce, verifier=verifier
        )
    except OidcError as exc:
        raise DomainError(
            exc.code, "The identity provider is unavailable", status_code=503
        ) from exc
    response = RedirectResponse(authorization_url, status_code=302)
    _disable_credential_caching(response)
    response.set_cookie(
        settings.oidc_flow_cookie_name,
        flow,
        max_age=settings.oidc_login_ttl_seconds,
        httponly=True,
        secure=settings.env == "production",
        samesite="lax",
        path=settings.api_prefix + "/auth",
    )
    return response


@router.get("/callback", include_in_schema=False)
async def oidc_callback(
    request: Request,
    session: SessionDependency,
    settings: SettingsDependency,
    code: Annotated[str | None, Query(max_length=4096)] = None,
    state: Annotated[str | None, Query(max_length=256)] = None,
    error: Annotated[str | None, Query(max_length=128)] = None,
) -> RedirectResponse:
    client = _oidc_client(request)
    flow = request.cookies.get(settings.oidc_flow_cookie_name)
    now = utc_now()
    transaction = (
        await consume_oidc_transaction(session, flow=flow, state=state, now=now)
        if flow and state
        else None
    )
    if transaction is None:
        raise DomainError(
            "invalid_oidc_state", "The OIDC callback state is invalid", status_code=400
        )
    if error is not None:
        return _callback_redirect(settings, "identity_provider_rejected", clear_flow=True)
    if not code:
        return _callback_redirect(settings, "authorization_code_missing", clear_flow=True)
    try:
        identity = await client.exchange_code(
            code=code,
            verifier=transaction.code_verifier,
            expected_nonce=transaction.nonce,
        )
    except OidcError as exc:
        return _callback_redirect(settings, exc.code, clear_flow=True)
    expires_at = min(
        identity.id_token_expires_at,
        now + timedelta(seconds=settings.oidc_session_ttl_seconds),
    )
    if expires_at <= now:
        return _callback_redirect(settings, "id_token_expired", clear_flow=True)
    await provision_principal(session, identity.principal, settings)
    raw_session = token_urlsafe(48)
    session.add(
        OidcSession(
            session_hash=token_hash(raw_session),
            user_id=identity.principal.user_id,
            subject=identity.principal.subject,
            issuer=identity.principal.issuer,
            display_name=identity.principal.display_name,
            roles=list(identity.principal.roles),
            expires_at=expires_at,
            last_seen_at=now,
            created_at=now,
        )
    )
    await session.commit()
    assert settings.oidc_post_login_redirect_uri
    response = RedirectResponse(settings.oidc_post_login_redirect_uri, status_code=302)
    _disable_credential_caching(response)
    response.delete_cookie(settings.oidc_flow_cookie_name, path=settings.api_prefix + "/auth")
    response.set_cookie(
        settings.oidc_session_cookie_name,
        raw_session,
        max_age=max(1, int((expires_at - now).total_seconds())),
        httponly=True,
        secure=settings.env == "production",
        samesite="lax",
        path=settings.api_prefix,
    )
    return response


@router.get("/session", response_model=OidcSessionResponse)
async def oidc_session_status(
    principal: AuthPrincipalDependency,
    request: Request,
    response: Response,
    session: SessionDependency,
    settings: SettingsDependency,
) -> OidcSessionResponse:
    _disable_credential_caching(response)
    expires_at = None
    if settings.auth_mode == "oidc":
        raw = request.cookies.get(settings.oidc_session_cookie_name)
        record = await session.get(OidcSession, token_hash(raw or "")) if raw else None
        expires_at = record.expires_at if record else None
    return OidcSessionResponse(
        authenticated=True,
        user_id=principal.user_id,
        display_name=principal.display_name,
        roles=list(principal.roles),
        expires_at=expires_at,
    )


@router.post("/logout", response_model=OidcLogoutResponse)
async def oidc_logout(
    request: Request,
    response: Response,
    session: SessionDependency,
    settings: SettingsDependency,
    origin: Annotated[str | None, Header(alias="Origin")] = None,
) -> OidcLogoutResponse:
    if settings.auth_mode != "oidc":
        raise DomainError(
            "oidc_not_configured", "OIDC authentication is not enabled", status_code=404
        )
    if origin is None or origin not in settings.cors_origins:
        raise DomainError(
            "origin_not_allowed",
            "A configured browser Origin is required for logout",
            status_code=403,
        )
    raw = request.cookies.get(settings.oidc_session_cookie_name)
    if raw:
        await session.execute(
            update(OidcSession)
            .where(OidcSession.session_hash == token_hash(raw), OidcSession.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
        await session.commit()
    response.delete_cookie(settings.oidc_session_cookie_name, path=settings.api_prefix)
    _disable_credential_caching(response)
    try:
        provider_logout = await _oidc_client(request).logout_url()
    except OidcError:
        provider_logout = None
    assert settings.oidc_post_logout_redirect_uri
    return OidcLogoutResponse(logout_url=provider_logout or settings.oidc_post_logout_redirect_uri)


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
    response.headers["Referrer-Policy"] = "no-referrer"


def _callback_redirect(
    settings: Settings,
    error_code: str,
    *,
    clear_flow: bool,
) -> RedirectResponse:
    redirect_uri = settings.oidc_post_login_redirect_uri
    assert isinstance(redirect_uri, str)
    parts = urlsplit(redirect_uri)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["auth_error"] = error_code
    url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    response = RedirectResponse(url, status_code=302)
    _disable_credential_caching(response)
    if clear_flow:
        response.delete_cookie(
            settings.oidc_flow_cookie_name,
            path=settings.api_prefix + "/auth",
        )
    return response
