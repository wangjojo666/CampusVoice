from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

import httpx
import pytest
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette import formparsers

from app import main as main_module
from app.core.config import Settings
from app.main import create_app

_ALLOWED_ORIGIN = "https://app.campus.test"


def _oidc_settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        auth_mode="oidc",
        oidc_issuer="https://id.campus.test",
        oidc_client_id="campusvoice",
        oidc_client_secret=SecretStr("server-only-secret"),
        oidc_redirect_uri="https://api.campus.test/api/auth/callback",
        oidc_post_login_redirect_uri=f"{_ALLOWED_ORIGIN}/",
        oidc_post_logout_redirect_uri=f"{_ALLOWED_ORIGIN}/signed-out",
        cors_origins=[_ALLOWED_ORIGIN],
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'csrf.db'}",
        database_auto_create=True,
    )


def _add_boundary_probes(app: FastAPI) -> None:
    @app.get("/boundary-probe")
    async def safe_probe() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/boundary-probe/json")
    async def json_probe(request: Request) -> dict[str, object]:
        return {"payload": await request.json()}

    @app.post("/boundary-probe/multipart")
    async def multipart_probe(
        file: Annotated[UploadFile, File()],
        title: Annotated[str, Form()],
    ) -> dict[str, object]:
        return {"title": title, "size": len(await file.read())}


def _assert_origin_rejected(response: httpx.Response) -> None:
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"] == {
        "code": "origin_not_allowed",
        "message": "A trusted browser Origin is required for OIDC writes",
        "details": {},
    }
    assert payload["request_id"] == response.headers["x-request-id"]


def _assert_invalid_session(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_session"


def test_oidc_unsafe_requests_require_one_exact_allowlisted_origin(tmp_path: Path) -> None:
    settings = _oidc_settings(tmp_path)
    app = create_app(settings)
    _add_boundary_probes(app)

    with TestClient(app) as client:
        assert client.get("/boundary-probe").status_code == 200

        _assert_origin_rejected(client.post("/boundary-probe/json", json={"value": 1}))
        _assert_origin_rejected(
            client.post(
                "/boundary-probe/json",
                json={"value": 1},
                headers={"Origin": "https://attacker.campus.test"},
            )
        )
        _assert_origin_rejected(
            client.post(
                "/boundary-probe/json",
                json={"value": 1},
                headers={"Origin": "http://testserver"},
            )
        )
        _assert_origin_rejected(
            client.post(
                "/boundary-probe/json",
                json={"value": 1},
                headers={"Origin": f"{_ALLOWED_ORIGIN}:444"},
            )
        )
        _assert_origin_rejected(
            client.post(
                "/boundary-probe/json",
                json={"value": 1},
                headers={"Origin": f"{_ALLOWED_ORIGIN}:0"},
            )
        )
        _assert_origin_rejected(
            client.post(
                "/boundary-probe/json",
                json={"value": 1},
                headers=[("Origin", _ALLOWED_ORIGIN), ("Origin", _ALLOWED_ORIGIN)],
            )
        )
        _assert_origin_rejected(
            client.post(
                "/boundary-probe/multipart",
                files={"file": ("notice.txt", b"content", "text/plain")},
                data={"title": "notice"},
            )
        )
        _assert_origin_rejected(
            client.post(
                "/boundary-probe/multipart",
                files={"file": ("notice.txt", b"content", "text/plain")},
                data={"title": "notice"},
                headers={"Origin": "https://attacker.campus.test"},
            )
        )
        client.cookies.set(
            settings.oidc_session_cookie_name,
            "invalid-session",
            path=settings.api_prefix,
        )
        _assert_origin_rejected(client.post("/api/privacy/retention/run"))
        _assert_origin_rejected(
            client.post(
                "/api/privacy/retention/run",
                headers={"Origin": "https://attacker.campus.test"},
            )
        )
        _assert_origin_rejected(
            client.post(
                "/api/documents",
                files={"file": ("notice.txt", b"content", "text/plain")},
                data={"title": "notice"},
            )
        )
        retention_response = client.post(
            "/api/privacy/retention/run",
            headers={"Origin": _ALLOWED_ORIGIN},
        )
        _assert_invalid_session(retention_response)
        upload_response = client.post(
            "/api/documents",
            files={"file": ("notice.txt", b"content", "text/plain")},
            data={"title": "notice"},
            headers={"Origin": _ALLOWED_ORIGIN},
        )
        _assert_invalid_session(upload_response)

        json_response = client.post(
            "/boundary-probe/json",
            json={"value": 1},
            headers={"Origin": _ALLOWED_ORIGIN},
        )
        assert json_response.status_code == 200
        assert json_response.json() == {"payload": {"value": 1}}
        assert json_response.headers["access-control-allow-origin"] == _ALLOWED_ORIGIN

        multipart_response = client.post(
            "/boundary-probe/multipart",
            files={"file": ("notice.txt", b"content", "text/plain")},
            data={"title": "notice"},
            headers={"Origin": _ALLOWED_ORIGIN},
        )
        assert multipart_response.status_code == 200
        assert multipart_response.json() == {"title": "notice", "size": 7}


@pytest.mark.parametrize("auth_mode", ["demo", "jwt"])
def test_non_oidc_auth_modes_do_not_apply_cookie_origin_boundary(
    tmp_path: Path,
    auth_mode: str,
) -> None:
    values: dict[str, object] = {
        "env": "test",
        "auth_mode": auth_mode,
        "database_url": f"sqlite+aiosqlite:///{tmp_path / f'{auth_mode}.db'}",
        "database_auto_create": True,
    }
    if auth_mode == "jwt":
        values.update(
            jwt_issuer="https://id.campus.test",
            jwt_audience="campusvoice-api",
            jwt_jwks_url="https://id.campus.test/jwks",
        )
    app = create_app(Settings(**values))  # type: ignore[arg-type]
    _add_boundary_probes(app)

    with TestClient(app) as client:
        response = client.post(
            "/boundary-probe/json",
            json={"value": 1},
            headers={"Authorization": "Bearer opaque-test-value"},
        )
    assert response.status_code == 200


def _multipart_chunks() -> AsyncIterator[bytes]:
    boundary = b"campusvoice-boundary"
    prefix = (
        b"--"
        + boundary
        + b'\r\nContent-Disposition: form-data; name="file"; filename="large.txt"\r\n'
        + b"Content-Type: text/plain\r\n\r\n"
    )
    suffix = b"\r\n--" + boundary + b"--\r\n"

    async def chunks() -> AsyncIterator[bytes]:
        yield prefix + (b"x" * 32)
        yield b"x" * 256
        yield suffix

    return chunks()


def _body_limit_settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'body-limit.db'}",
        database_auto_create=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_length", [None, "1"])
async def test_streamed_document_limit_returns_413_and_closes_partial_spool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    declared_length: str | None,
) -> None:
    monkeypatch.setattr(main_module, "DOCUMENT_UPLOAD_BODY_LIMIT", 256)
    created_spools: list[object] = []
    real_spooled_file = formparsers.SpooledTemporaryFile

    def tracking_spooled_file(*args: object, **kwargs: object) -> object:
        spool = real_spooled_file(*args, **kwargs)
        created_spools.append(spool)
        return spool

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracking_spooled_file)
    app = create_app(_body_limit_settings(tmp_path))
    headers = {"Content-Type": "multipart/form-data; boundary=campusvoice-boundary"}
    if declared_length is not None:
        headers["Content-Length"] = declared_length

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/documents",
                headers=headers,
                content=_multipart_chunks(),
            )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "document_too_large"
    assert created_spools
    assert all(bool(getattr(spool, "closed", False)) for spool in created_spools)


@pytest.mark.asyncio
async def test_declared_oversized_document_is_rejected_before_multipart_spooling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "DOCUMENT_UPLOAD_BODY_LIMIT", 256)
    created_spools: list[object] = []
    real_spooled_file = formparsers.SpooledTemporaryFile

    def tracking_spooled_file(*args: object, **kwargs: object) -> object:
        spool = real_spooled_file(*args, **kwargs)
        created_spools.append(spool)
        return spool

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", tracking_spooled_file)
    app = create_app(_body_limit_settings(tmp_path))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/documents",
                headers={
                    "Content-Type": "multipart/form-data; boundary=campusvoice-boundary",
                    "Content-Length": "257",
                },
                content=b"not-read",
            )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "document_too_large"
    assert created_spools == []
