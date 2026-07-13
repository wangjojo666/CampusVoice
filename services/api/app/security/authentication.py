import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

import jwt
from jwt import PyJWKClient

from app.core.config import Settings
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


def _internal_user_id(issuer: str, subject: str) -> str:
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
        self._jwks = PyJWKClient(jwks_url, cache_keys=True)

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
            user_id=_internal_user_id(self._issuer, subject),
            subject=subject,
            issuer=self._issuer,
            display_name=display_name[:120],
            roles=roles,
        )


def build_authenticator(settings: Settings) -> Authenticator:
    if settings.auth_mode == "demo":
        return DemoAuthenticator(settings.demo_user_id)
    assert settings.jwt_issuer and settings.jwt_audience and settings.jwt_jwks_url
    return JwtAuthenticator(
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        jwks_url=settings.jwt_jwks_url,
        algorithms=settings.jwt_algorithms,
        leeway_seconds=settings.jwt_leeway_seconds,
    )


def websocket_ticket(subprotocol_header: str | None) -> str | None:
    if not subprotocol_header:
        return None
    for raw_protocol in subprotocol_header.split(","):
        protocol = raw_protocol.strip()
        if protocol.startswith("campusvoice.ticket."):
            return protocol.removeprefix("campusvoice.ticket.")
    return None
