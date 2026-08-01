from fastapi import HTTPException, Request, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.observability import request_id_from

DOCUMENT_UPLOAD_BODY_LIMIT = 21 * 1024 * 1024
_DOCUMENT_TOO_LARGE_MESSAGE = "The document upload body cannot exceed 21 MB"


class RequestBodyTooLarge(HTTPException, OSError):
    """A 413 that also makes Starlette close partially spooled multipart files."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=_DOCUMENT_TOO_LARGE_MESSAGE,
        )


def document_too_large_response(request: Request) -> JSONResponse:
    request_id = request_id_from(request)
    return JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        headers={"X-Request-ID": request_id},
        content={
            "error": {
                "code": "document_too_large",
                "message": _DOCUMENT_TOO_LARGE_MESSAGE,
                "details": {},
            },
            "request_id": request_id,
        },
    )


def _declared_body_exceeds_limit(scope: Scope, max_body_bytes: int) -> bool:
    for name, raw_value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        try:
            declared_bytes = int(raw_value.decode("ascii").strip())
        except (UnicodeDecodeError, ValueError):
            continue
        if declared_bytes > max_body_bytes:
            return True
    return False


class DocumentUploadBodyLimitMiddleware:
    """Bound document request bodies before FastAPI parses multipart form data."""

    def __init__(self, app: ASGIApp, *, path: str, max_body_bytes: int) -> None:
        self._app = app
        self._path = path.rstrip("/")
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method", "").upper() != "POST"
            or str(scope.get("path", "")).rstrip("/") != self._path
        ):
            await self._app(scope, receive, send)
            return

        if _declared_body_exceeds_limit(scope, self._max_body_bytes):
            response = document_too_large_response(Request(scope, receive=receive))
            await response(scope, receive, send)
            return

        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self._max_body_bytes:
                    raise RequestBodyTooLarge
            return message

        await self._app(scope, limited_receive, send)
