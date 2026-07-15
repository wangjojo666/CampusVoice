import asyncio
import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.types import utc_now
from app.models.entities import OidcSession
from app.services.errors import DomainError


class AuthenticationError(DomainError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=401)


@dataclass(frozen=True, slots=True)
class AuthPrincipal:
    user_id: str
    subject: str
    issuer: str
    display_name: str
    roles: tuple[str, ...] = ()
    authentication_method: str = "jwt"


class Authenticator(Protocol):
    async def authenticate(self, token: str | None) -> AuthPrincipal: ...


def internal_user_id(issuer: str, subject: str) -> str:
    digest = hashlib.sha256(f"{issuer}\0{subject}".encode()).hexdigest()[:48]
    return f"usr_{digest}"


class DemoAuthenticator:
    def __init__(self, user_id: str) -> None:
        self._user_id = user_id

    async def authenticate(self, token: str | None) -> AuthPrincipal:
        del token
        return AuthPrincipal(
            user_id=self._user_id,
            subject=self._user_id,
            issuer="campusvoice:demo",
            display_name="CampusVoice Demo User",
            authentication_method="demo",
        )


class OidcCookieAuthenticator:
    async def authenticate(self, token: str | None) -> AuthPrincipal:
        del token
        raise AuthenticationError("authentication_required", "An OIDC session is required")


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        if urlsplit(new_url).scheme.lower() != "https":
            raise urllib.error.HTTPError(
                new_url,
                code,
                "JWKS redirects must remain on HTTPS",
                headers,
                file_pointer,
            )
        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            new_url,
        )


class _HttpsOnlyPyJWKClient(PyJWKClient):
    def fetch_data(self) -> Any:
        try:
            request = urllib.request.Request(url=self.uri, headers=self.headers)
            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=self.ssl_context),
                _HttpsOnlyRedirectHandler(),
            )
            with opener.open(request, timeout=self.timeout) as response:
                if urlsplit(response.geturl()).scheme.lower() != "https":
                    raise urllib.error.URLError("JWKS response URL is not HTTPS")
                jwk_set = json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            if isinstance(exc, urllib.error.HTTPError):
                exc.close()
            raise PyJWKClientConnectionError(f'Failed to fetch JWKS over HTTPS: "{exc}"') from exc

        if self.jwk_set_cache is not None:
            self.jwk_set_cache.put(jwk_set)
        return jwk_set


class JwtAuthenticator:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        algorithms: list[str],
        leeway_seconds: int = 30,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._algorithms = tuple(algorithms)
        self._leeway_seconds = leeway_seconds
        jwks_client_type = (
            _HttpsOnlyPyJWKClient if urlsplit(jwks_url).scheme.lower() == "https" else PyJWKClient
        )
        self._jwks = jwks_client_type(jwks_url, cache_keys=True)

    async def authenticate(self, token: str | None) -> AuthPrincipal:
        if not token:
            raise AuthenticationError("authentication_required", "A bearer token is required")
        try:
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(
                "invalid_access_token", "The access token is invalid"
            ) from exc
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise AuthenticationError("invalid_access_token", "The access token subject is invalid")
        raw_roles = claims.get("roles", ())
        roles = (
            tuple(value for value in raw_roles if isinstance(value, str))
            if isinstance(raw_roles, list)
            else ()
        )
        display_name = next(
            (
                value
                for value in (claims.get("name"), claims.get("preferred_username"))
                if isinstance(value, str) and value.strip()
            ),
            "CampusVoice User",
        )
        return AuthPrincipal(
            user_id=internal_user_id(self._issuer, subject),
            subject=subject,
            issuer=self._issuer,
            display_name=display_name[:120],
            roles=roles,
        )


def build_authenticator(settings: Settings) -> Authenticator:
    if settings.auth_mode == "demo":
        return DemoAuthenticator(settings.demo_user_id)
    if settings.auth_mode == "oidc":
        return OidcCookieAuthenticator()
    assert settings.jwt_issuer and settings.jwt_audience and settings.jwt_jwks_url
    return JwtAuthenticator(
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        jwks_url=settings.jwt_jwks_url,
        algorithms=settings.jwt_algorithms,
        leeway_seconds=settings.jwt_leeway_seconds,
    )


async def authenticate_oidc_session(
    session: AsyncSession,
    token: str | None,
) -> AuthPrincipal:
    if not token:
        raise AuthenticationError("authentication_required", "An OIDC session is required")
    session_hash = hashlib.sha256(token.encode()).hexdigest()
    record = await session.scalar(
        select(OidcSession).where(OidcSession.session_hash == session_hash)
    )
    if record is None or record.revoked_at is not None or record.expires_at <= utc_now():
        raise AuthenticationError("invalid_session", "The OIDC session is invalid or expired")
    return AuthPrincipal(
        user_id=record.user_id,
        subject=record.subject,
        issuer=record.issuer,
        display_name=record.display_name,
        roles=tuple(record.roles),
        authentication_method="oidc_session",
    )


def websocket_ticket(subprotocol_header: str | None) -> str | None:
    if not subprotocol_header:
        return None
    for raw_protocol in subprotocol_header.split(","):
        protocol = raw_protocol.strip()
        if protocol.startswith("campusvoice.ticket."):
            return protocol.removeprefix("campusvoice.ticket.")
    return None
