import hashlib
import hmac
import json
import logging
import re
from time import perf_counter
from uuid import uuid4

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.metrics import InMemoryMetrics

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_LOGGER = logging.getLogger("campusvoice.request")
_SHORT_LIVED_CREDENTIAL_ROUTES = (
    "/auth/ws-ticket",
    "/auth/write-challenges",
    "/auth/write-challenges/advance",
    "/actions/{action_id}/challenge",
)


def request_id_from(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else uuid4().hex


def _safe_request_id(raw_value: str | None) -> str:
    if raw_value and _REQUEST_ID_PATTERN.fullmatch(raw_value):
        return raw_value
    return uuid4().hex


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path.startswith("/"):
        return route_path
    return "__unmatched__"


def _disable_short_lived_credential_caching(request: Request, response: Response) -> None:
    route = _route_template(request)
    if any(route.endswith(suffix) for suffix in _SHORT_LIVED_CREDENTIAL_ROUTES):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"


def _user_reference(request: Request, salt: bytes) -> str | None:
    principal = getattr(request.state, "principal", None)
    user_id = getattr(principal, "user_id", None)
    if not isinstance(user_id, str) or not user_id:
        return None
    return hmac.new(salt, user_id.encode(), hashlib.sha256).hexdigest()[:16]


def _write_request_log(
    *,
    request: Request,
    request_id: str,
    route: str,
    status_code: int,
    duration_seconds: float,
    salt: bytes,
    exception_type: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "event": "http_request",
        "request_id": request_id,
        "method": request.method,
        "route": route,
        "status_code": status_code,
        "duration_ms": round(max(0.0, duration_seconds * 1_000), 3),
    }
    user_reference = _user_reference(request, salt)
    if user_reference is not None:
        payload["user_ref"] = user_reference
    if exception_type is not None:
        payload["exception_type"] = exception_type
    level = logging.ERROR if status_code >= 500 else logging.INFO
    _LOGGER.log(level, json.dumps(payload, ensure_ascii=True, separators=(",", ":")))


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    """Attach request IDs and emit content-free logs and HTTP aggregates."""

    def __init__(
        self,
        app: object,
        *,
        metrics: InMemoryMetrics,
        pseudonym_salt: bytes,
        log_level: str,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._metrics = metrics
        self._pseudonym_salt = pseudonym_salt
        _LOGGER.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        # Uvicorn's default access logger includes the raw path and query string.
        # This middleware replaces it with a route-template-only JSON event.
        logging.getLogger("uvicorn.access").disabled = True

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = _safe_request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration = perf_counter() - started
            route = _route_template(request)
            self._metrics.record_http(
                method=request.method,
                route=route,
                status_code=500,
                duration_seconds=duration,
            )
            _write_request_log(
                request=request,
                request_id=request_id,
                route=route,
                status_code=500,
                duration_seconds=duration,
                salt=self._pseudonym_salt,
                exception_type=type(exc).__name__,
            )
            response = JSONResponse(
                status_code=500,
                headers={"X-Request-ID": request_id},
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "The service could not complete the request",
                        "details": {},
                    },
                    "request_id": request_id,
                },
            )
            _disable_short_lived_credential_caching(request, response)
            return response

        duration = perf_counter() - started
        route = _route_template(request)
        response.headers["X-Request-ID"] = request_id
        _disable_short_lived_credential_caching(request, response)
        self._metrics.record_http(
            method=request.method,
            route=route,
            status_code=response.status_code,
            duration_seconds=duration,
        )
        _write_request_log(
            request=request,
            request_id=request_id,
            route=route,
            status_code=response.status_code,
            duration_seconds=duration,
            salt=self._pseudonym_salt,
        )
        return response
