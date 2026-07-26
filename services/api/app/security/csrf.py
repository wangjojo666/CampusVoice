from collections.abc import Sequence
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.observability import request_id_from

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _normalized_origin(value: str) -> tuple[str, str, int] | None:
    if value != value.strip() or "\r" in value or "\n" in value:
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return scheme, parsed.hostname.lower(), port or (443 if scheme == "https" else 80)


def _origin_header_values(scope: Scope) -> list[str]:
    values: list[str] = []
    for name, value in scope.get("headers", []):
        if name.lower() == b"origin":
            values.append(value.decode("latin-1"))
    return values


class OidcCsrfOriginMiddleware:
    """Reject unsafe OIDC-cookie requests that do not prove a trusted browser origin."""

    def __init__(self, app: ASGIApp, *, allowed_origins: Sequence[str]) -> None:
        self._app = app
        self._allowed_origins = frozenset(
            origin for value in allowed_origins if (origin := _normalized_origin(value)) is not None
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method", "").upper() in _SAFE_METHODS:
            await self._app(scope, receive, send)
            return

        origins = _origin_header_values(scope)
        origin = _normalized_origin(origins[0]) if len(origins) == 1 else None
        if origin is not None and origin in self._allowed_origins:
            await self._app(scope, receive, send)
            return

        request_id = request_id_from(Request(scope, receive=receive))
        response = JSONResponse(
            status_code=403,
            headers={"X-Request-ID": request_id},
            content={
                "error": {
                    "code": "origin_not_allowed",
                    "message": "A trusted browser Origin is required for OIDC writes",
                    "details": {},
                },
                "request_id": request_id,
            },
        )
        await response(scope, receive, send)
