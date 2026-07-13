import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.entities import OidcLoginTransaction
from app.security.authentication import AuthPrincipal, internal_user_id


class OidcError(Exception):
    """A safe, bounded OIDC failure suitable for callback error mapping."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class OidcMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    end_session_endpoint: str | None
    code_challenge_methods_supported: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    principal: AuthPrincipal
    id_token_expires_at: datetime


@dataclass(frozen=True, slots=True)
class OidcTransactionSecrets:
    nonce: str
    code_verifier: str


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def consume_oidc_transaction(
    session: AsyncSession,
    *,
    flow: str,
    state: str,
    now: datetime,
) -> OidcTransactionSecrets | None:
    """Atomically consume one unexpired flow and return its server-side secrets."""

    result = await session.execute(
        update(OidcLoginTransaction)
        .where(
            OidcLoginTransaction.flow_hash == token_hash(flow),
            OidcLoginTransaction.state_hash == token_hash(state),
            OidcLoginTransaction.consumed_at.is_(None),
            OidcLoginTransaction.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(
            OidcLoginTransaction.nonce,
            OidcLoginTransaction.code_verifier,
        )
    )
    row = result.one_or_none()
    if row is None:
        await session.rollback()
        return None
    await session.commit()
    return OidcTransactionSecrets(nonce=row.nonce, code_verifier=row.code_verifier)


class OidcClient:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if settings.auth_mode != "oidc":
            raise ValueError("OIDC client requires oidc auth mode")
        self._settings = settings
        self._transport = transport
        self._metadata: OidcMetadata | None = None

    async def metadata(self) -> OidcMetadata:
        if self._metadata is not None:
            return self._metadata
        assert self._settings.oidc_issuer
        url = self._settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
        payload = await self._get_json(url)
        issuer = _required_url(payload, "issuer")
        if not hmac.compare_digest(issuer.rstrip("/"), self._settings.oidc_issuer.rstrip("/")):
            raise OidcError("provider_issuer_mismatch")
        methods = payload.get("code_challenge_methods_supported", [])
        supported = tuple(value for value in methods if isinstance(value, str))
        if "S256" not in supported:
            raise OidcError("provider_pkce_s256_unsupported")
        self._metadata = OidcMetadata(
            issuer=issuer,
            authorization_endpoint=_required_url(payload, "authorization_endpoint"),
            token_endpoint=_required_url(payload, "token_endpoint"),
            jwks_uri=_required_url(payload, "jwks_uri"),
            end_session_endpoint=_optional_url(payload, "end_session_endpoint"),
            code_challenge_methods_supported=supported,
        )
        return self._metadata

    async def authorization_url(self, *, state: str, nonce: str, verifier: str) -> str:
        metadata = await self.metadata()
        assert self._settings.oidc_client_id and self._settings.oidc_redirect_uri
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._settings.oidc_client_id,
                "redirect_uri": self._settings.oidc_redirect_uri,
                "scope": " ".join(self._settings.oidc_scopes),
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{metadata.authorization_endpoint}?{query}"

    async def exchange_code(self, *, code: str, verifier: str, expected_nonce: str) -> OidcIdentity:
        metadata = await self.metadata()
        assert self._settings.oidc_client_id and self._settings.oidc_redirect_uri
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._settings.oidc_redirect_uri,
            "client_id": self._settings.oidc_client_id,
            "code_verifier": verifier,
        }
        request_options: dict[str, Any] = {}
        client_secret = (
            self._settings.oidc_client_secret.get_secret_value()
            if self._settings.oidc_client_secret
            else ""
        )
        if client_secret:
            request_options["auth"] = httpx.BasicAuth(
                self._settings.oidc_client_id,
                client_secret,
            )
        try:
            async with self._client() as client:
                response = await client.post(
                    metadata.token_endpoint,
                    data=data,
                    headers={"Accept": "application/json"},
                    **request_options,
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcError("token_exchange_failed") from exc
        id_token = payload.get("id_token") if isinstance(payload, dict) else None
        if not isinstance(id_token, str) or not id_token:
            raise OidcError("id_token_missing")
        return await self._validate_id_token(id_token, metadata, expected_nonce)

    async def logout_url(self) -> str | None:
        metadata = await self.metadata()
        if metadata.end_session_endpoint is None:
            return None
        assert self._settings.oidc_client_id and self._settings.oidc_post_logout_redirect_uri
        return (
            metadata.end_session_endpoint
            + "?"
            + urlencode(
                {
                    "client_id": self._settings.oidc_client_id,
                    "post_logout_redirect_uri": self._settings.oidc_post_logout_redirect_uri,
                }
            )
        )

    async def _validate_id_token(
        self, encoded: str, metadata: OidcMetadata, expected_nonce: str
    ) -> OidcIdentity:
        try:
            header = jwt.get_unverified_header(encoded)
            key_id = header.get("kid")
            algorithm = header.get("alg")
            if (
                not isinstance(key_id, str)
                or algorithm not in self._settings.oidc_id_token_algorithms
            ):
                raise OidcError("id_token_key_invalid")
            jwks = await self._get_json(metadata.jwks_uri)
            raw_keys = jwks.get("keys", [])
            raw_key = next(
                (item for item in raw_keys if isinstance(item, dict) and item.get("kid") == key_id),
                None,
            )
            if raw_key is None:
                raise OidcError("id_token_key_invalid")
            signing_key = jwt.PyJWK.from_dict(raw_key, algorithm=algorithm)
            assert self._settings.oidc_client_id
            claims: dict[str, Any] = jwt.decode(
                encoded,
                signing_key.key,
                algorithms=self._settings.oidc_id_token_algorithms,
                audience=self._settings.oidc_client_id,
                issuer=metadata.issuer,
                leeway=self._settings.jwt_leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce"]},
            )
        except OidcError:
            raise
        except (jwt.PyJWTError, ValueError, TypeError, StopIteration) as exc:
            raise OidcError("id_token_invalid") from exc
        nonce = claims.get("nonce")
        if not isinstance(nonce, str) or not hmac.compare_digest(nonce, expected_nonce):
            raise OidcError("nonce_mismatch")
        audience = claims.get("aud")
        if isinstance(audience, list) and len(audience) > 1:
            authorized_party = claims.get("azp")
            if not isinstance(authorized_party, str) or not hmac.compare_digest(
                authorized_party, self._settings.oidc_client_id or ""
            ):
                raise OidcError("id_token_authorized_party_invalid")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise OidcError("id_token_subject_invalid")
        raw_roles = claims.get("roles", [])
        roles = (
            tuple(value for value in raw_roles if isinstance(value, str))
            if isinstance(raw_roles, list)
            else ()
        )
        display_name = next(
            (
                value
                for value in (
                    claims.get("name"),
                    claims.get("preferred_username"),
                    claims.get("email"),
                )
                if isinstance(value, str) and value.strip()
            ),
            "CampusVoice User",
        )
        expires_at = datetime.fromtimestamp(float(claims["exp"]), tz=UTC)
        return OidcIdentity(
            principal=AuthPrincipal(
                user_id=internal_user_id(metadata.issuer, subject),
                subject=subject,
                issuer=metadata.issuer,
                display_name=display_name[:120],
                roles=roles,
                authentication_method="oidc_session",
            ),
            id_token_expires_at=expires_at,
        )

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.get(url, headers={"Accept": "application/json"})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OidcError("provider_metadata_unavailable") from exc
        if not isinstance(payload, dict):
            raise OidcError("provider_response_invalid")
        return payload

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._settings.oidc_http_timeout_seconds,
            follow_redirects=False,
            transport=self._transport,
        )


def _required_url(payload: dict[str, Any], key: str) -> str:
    value = _optional_url(payload, key)
    if value is None:
        raise OidcError("provider_metadata_invalid")
    return value


def _optional_url(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("https://"):
        raise OidcError("provider_metadata_invalid")
    return value
