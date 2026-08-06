from fastapi import HTTPException, Request, status
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.observability import request_id_from

DOCUMENT_UPLOAD_BODY_LIMIT = 21 * 1024 * 1024
WECHAT_LOGIN_BODY_LIMIT = 4 * 1024
_DOCUMENT_TOO_LARGE_MESSAGE = "The document upload body cannot exceed 21 MB"
_WECHAT_LOGIN_TOO_LARGE_MESSAGE = "The WeChat login body cannot exceed 4 KiB"


class RequestBodyTooLarge(HTTPException, OSError):
    """A bounded 413 that also closes partially spooled multipart files."""

    def __init__(
        self,
        *,
        code: str = "document_too_large",
        message: str = _DOCUMENT_TOO_LARGE_MESSAGE,
        response_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=message,
        )
        self.code = code
        self.message = message
        self.response_headers = response_headers or {}


def request_body_too_large_response(
    request: Request,
    error: RequestBodyTooLarge,
) -> JSONResponse:
    request_id = request_id_from(request)
    return JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        headers={**error.response_headers, "X-Request-ID": request_id},
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "details": {},
            },
            "request_id": request_id,
        },
    )


def document_too_large_response(request: Request) -> JSONResponse:
    return request_body_too_large_response(request, RequestBodyTooLarge())


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


class RequestBodyLimitMiddleware:
    """Bound one exact request path before FastAPI parses its body."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        path: str,
        max_body_bytes: int,
        error_code: str,
        error_message: str,
        response_headers: dict[str, str] | None = None,
    ) -> None:
        self._app = app
        self._path = path.rstrip("/")
        self._max_body_bytes = max_body_bytes
        self._error_code = error_code
        self._error_message = error_message
        self._response_headers = response_headers or {}

    def _error(self) -> RequestBodyTooLarge:
        return RequestBodyTooLarge(
            code=self._error_code,
            message=self._error_message,
            response_headers=self._response_headers,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method", "").upper() != "POST"
            or str(scope.get("path", "")).rstrip("/") != self._path
        ):
            await self._app(scope, receive, send)
            return

        if _declared_body_exceeds_limit(scope, self._max_body_bytes):
            response = request_body_too_large_response(
                Request(scope, receive=receive),
                self._error(),
            )
            await response(scope, receive, send)
            return

        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self._max_body_bytes:
                    raise self._error()
            return message

        await self._app(scope, limited_receive, send)


class DocumentUploadBodyLimitMiddleware(RequestBodyLimitMiddleware):
    """Keep the existing document-upload boundary and response contract."""

    def __init__(self, app: ASGIApp, *, path: str, max_body_bytes: int) -> None:
        super().__init__(
            app,
            path=path,
            max_body_bytes=max_body_bytes,
            error_code="document_too_large",
            error_message=_DOCUMENT_TOO_LARGE_MESSAGE,
        )


class WeChatLoginBodyLimitMiddleware(RequestBodyLimitMiddleware):
    """Reject oversized unauthenticated login bodies before JSON parsing."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        path: str,
        max_body_bytes: int = WECHAT_LOGIN_BODY_LIMIT,
    ) -> None:
        super().__init__(
            app,
            path=path,
            max_body_bytes=max_body_bytes,
            error_code="wechat_login_body_too_large",
            error_message=_WECHAT_LOGIN_TOO_LARGE_MESSAGE,
            response_headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
            },
        )
