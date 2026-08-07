import asyncio
import re
from collections.abc import AsyncIterator, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

import app.main as main_module
from app.core.config import Settings
from app.main import create_app
from app.models.entities import OidcSession
from app.schemas.auth import WeChatLoginResponse
from app.security.authentication import wechat_session_issuer
from app.security.wechat import WeChatError, WeChatIdentity
from app.services.asr import AsrSessionConfig, TranscriptResult

_APP_ID = "wx3648488d39d15ff4"
_APP_SECRET = "a" * 32
_LOGIN_CODE = "temporary-code-123"


class NoopAsrAdapter:
    provider_name = "wechat-auth-test"

    async def start(self, config: AsrSessionConfig) -> None:
        del config

    async def feed(self, pcm_s16le: bytes) -> Sequence[TranscriptResult]:
        del pcm_s16le
        return ()

    async def flush(self) -> Sequence[TranscriptResult]:
        return ()

    async def finish(self) -> Sequence[TranscriptResult]:
        return ()

    async def close(self) -> None:
        return None


class FakeWeChatClient:
    async def exchange_code(self, code: str) -> WeChatIdentity:
        if code == "rejected-code":
            raise WeChatError("wechat_code_rejected")
        if code == "unavailable-code":
            raise WeChatError("wechat_service_unavailable")
        if code == "capacity-code":
            raise WeChatError("wechat_exchange_capacity_exceeded", retry_after_seconds=1)
        if code == "rate-code":
            raise WeChatError("wechat_exchange_rate_exceeded", retry_after_seconds=3)
        assert code == _LOGIN_CODE
        return WeChatIdentity(openid="openid-private-value", unionid="unionid-private-value")


@pytest.fixture
def wechat_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'wechat.db'}",
        database_auto_create=True,
        auth_mode="wechat",
        wechat_app_id=_APP_ID,
        wechat_app_secret=SecretStr(_APP_SECRET),
        confirmation_secret=SecretStr("test-confirmation-secret-with-32-characters"),
        asr_provider="disabled",
    )
    app = create_app(settings)
    app.state.wechat_client = FakeWeChatClient()
    app.state.asr_adapter_factory = NoopAsrAdapter
    with TestClient(app) as client:
        yield client


def login(client: TestClient) -> str:
    response = client.post("/api/auth/wechat/login", json={"code": _LOGIN_CODE})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["display_name"] == "微信用户"
    assert payload["session_token"].startswith("cvwx1.")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    serialized = response.text
    assert "openid-private-value" not in serialized
    assert "unionid-private-value" not in serialized
    assert _APP_SECRET not in serialized
    return payload["session_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_wechat_login_provisions_bounded_session_and_authenticates(
    wechat_client: TestClient,
) -> None:
    token = login(wechat_client)

    session = wechat_client.get("/api/auth/session", headers=bearer(token))
    assert session.status_code == 200, session.text
    assert session.json()["authenticated"] is True
    assert session.json()["display_name"] == "微信用户"
    assert session.json()["expires_at"] is not None

    tasks = wechat_client.get("/api/tasks", headers=bearer(token))
    assert tasks.status_code == 200, tasks.text
    assert tasks.json() == {"items": [], "total": 0}


def test_wechat_login_returns_stable_non_raw_account_id(
    wechat_client: TestClient,
) -> None:
    first = wechat_client.post("/api/auth/wechat/login", json={"code": _LOGIN_CODE})
    second = wechat_client.post("/api/auth/wechat/login", json={"code": _LOGIN_CODE})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_account_id = first.json()["account_id"]
    second_account_id = second.json()["account_id"]
    assert re.fullmatch(r"usr_[0-9a-f]{48}", first_account_id)
    assert second_account_id == first_account_id
    assert "openid-private-value" not in first.text
    assert "openid-private-value" not in second.text


def test_wechat_authentication_failures_are_bounded(wechat_client: TestClient) -> None:
    missing = wechat_client.get("/api/auth/session")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"

    malformed = wechat_client.get(
        "/api/auth/session",
        headers={"Authorization": "Basic not-a-wechat-session"},
    )
    assert malformed.status_code == 401

    unprefixed = wechat_client.get(
        "/api/auth/session",
        headers={"Authorization": "Bearer " + ("u" * 64)},
    )
    assert unprefixed.status_code == 401
    assert unprefixed.json()["error"]["code"] == "invalid_session_type"

    rejected = wechat_client.post(
        "/api/auth/wechat/login",
        json={"code": "rejected-code"},
    )
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "wechat_code_rejected"
    assert "openid" not in rejected.text.lower()
    assert _APP_SECRET not in rejected.text

    unavailable = wechat_client.post(
        "/api/auth/wechat/login",
        json={"code": "unavailable-code"},
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "wechat_service_unavailable"

    capacity = wechat_client.post(
        "/api/auth/wechat/login",
        json={"code": "capacity-code"},
    )
    assert capacity.status_code == 429
    assert capacity.headers["retry-after"] == "1"
    assert capacity.headers["cache-control"] == "no-store"
    assert capacity.json()["error"]["code"] == "wechat_exchange_capacity_exceeded"

    rate_limited = wechat_client.post(
        "/api/auth/wechat/login",
        json={"code": "rate-code"},
    )
    assert rate_limited.status_code == 429
    assert rate_limited.headers["retry-after"] == "3"
    assert rate_limited.json()["error"]["details"] == {"retry_after_seconds": 3}

    invalid_code = wechat_client.post(
        "/api/auth/wechat/login",
        json={"code": "bad code with spaces"},
    )
    assert invalid_code.status_code == 422


def test_wechat_login_rotates_the_previous_session_and_keeps_one_row(
    wechat_client: TestClient,
) -> None:
    first_token = login(wechat_client)
    second_token = login(wechat_client)

    assert first_token != second_token
    assert wechat_client.get("/api/auth/session", headers=bearer(first_token)).status_code == 401
    assert wechat_client.get("/api/auth/session", headers=bearer(second_token)).status_code == 200

    async def load_sessions() -> list[OidcSession]:
        async with wechat_client.app.state.session_factory() as session:
            return list(
                await session.scalars(
                    select(OidcSession).where(OidcSession.issuer == wechat_session_issuer(_APP_ID))
                )
            )

    records = asyncio.run(load_sessions())
    assert len(records) == 1
    assert records[0].revoked_at is None


def test_wechat_logout_revokes_the_server_session(wechat_client: TestClient) -> None:
    token = login(wechat_client)

    logged_out = wechat_client.post(
        "/api/auth/wechat/logout",
        headers=bearer(token),
    )
    assert logged_out.status_code == 200
    assert logged_out.json() == {"success": True}
    assert logged_out.headers["cache-control"] == "no-store"

    rejected = wechat_client.get("/api/auth/session", headers=bearer(token))
    assert rejected.status_code == 401

    repeated = wechat_client.post(
        "/api/auth/wechat/logout",
        headers=bearer(token),
    )
    assert repeated.status_code == 200


def test_wechat_websocket_ticket_needs_no_browser_origin_and_is_single_use(
    wechat_client: TestClient,
) -> None:
    token = login(wechat_client)
    issued = wechat_client.post("/api/auth/ws-ticket", headers=bearer(token))
    assert issued.status_code == 200, issued.text
    ticket = issued.json()["ticket"]
    protocols = ["campusvoice", f"campusvoice.ticket.{ticket}"]

    with wechat_client.websocket_connect("/ws/asr", subprotocols=protocols) as websocket:
        websocket.send_json(
            {
                "type": "start",
                "sample_rate_hz": 16000,
                "channels": 1,
                "sample_width_bytes": 2,
                "language": "zh",
                "hotwords": [],
            }
        )
        ready = websocket.receive_json()
        assert ready["type"] == "ready"
        websocket.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()
        assert closed.value.code == 1000

    with (
        pytest.raises(WebSocketDisconnect) as replay,
        wechat_client.websocket_connect("/ws/asr", subprotocols=protocols),
    ):
        pass
    assert replay.value.code == 1008


def test_wechat_configuration_fails_closed() -> None:
    with pytest.raises(ValidationError, match="wechat_app_secret"):
        Settings(
            env="production",
            auth_mode="wechat",
            wechat_app_id=_APP_ID,
            confirmation_secret=SecretStr("production-confirmation-secret-32-chars"),
        )

    with pytest.raises(ValidationError, match="wechat_app_id"):
        Settings(
            env="test",
            auth_mode="wechat",
            wechat_app_id="invalid-app-id",
            wechat_app_secret=SecretStr(_APP_SECRET),
        )

    valid = Settings(
        env="production",
        auth_mode="wechat",
        wechat_app_id=_APP_ID,
        wechat_app_secret=SecretStr(_APP_SECRET),
        confirmation_secret=SecretStr("production-confirmation-secret-32-chars"),
    )
    assert valid.wechat_app_id == _APP_ID
    assert valid.wechat_app_secret is not None
    assert valid.wechat_app_secret.get_secret_value() == _APP_SECRET


@pytest.mark.parametrize("display_name", ["", " \t\r\n", "名" * 121])
def test_wechat_login_response_rejects_invalid_display_names(display_name: str) -> None:
    with pytest.raises(ValidationError):
        WeChatLoginResponse(
            account_id="usr_" + ("a" * 48),
            session_token="cvwx1." + ("a" * 32),
            expires_at=datetime.now(UTC),
            display_name=display_name,
        )


@pytest.mark.parametrize(
    "app_secret",
    [
        " " * 32,
        "a" * 31,
        "a" * 33,
        ("a" * 31) + "-",
        "密" * 32,
    ],
)
def test_wechat_configuration_rejects_malformed_app_secrets(app_secret: str) -> None:
    with pytest.raises(ValidationError, match="exactly 32 ASCII letters or digits"):
        Settings(
            env="test",
            auth_mode="wechat",
            wechat_app_id=_APP_ID,
            wechat_app_secret=SecretStr(app_secret),
        )


@pytest.mark.parametrize("limit", [0, 65])
def test_wechat_configuration_bounds_code_exchange_concurrency(limit: int) -> None:
    with pytest.raises(ValidationError):
        Settings(
            env="test",
            auth_mode="wechat",
            wechat_app_id=_APP_ID,
            wechat_app_secret=SecretStr(_APP_SECRET),
            wechat_code_exchange_max_concurrency=limit,
        )


@pytest.mark.parametrize("rate", [0.0, 100.1])
def test_wechat_configuration_bounds_code_exchange_rate(rate: float) -> None:
    with pytest.raises(ValidationError):
        Settings(
            env="test",
            auth_mode="wechat",
            wechat_app_id=_APP_ID,
            wechat_app_secret=SecretStr(_APP_SECRET),
            wechat_code_exchange_rate_per_second=rate,
        )


@pytest.mark.parametrize("burst", [0, 257])
def test_wechat_configuration_bounds_code_exchange_burst(burst: int) -> None:
    with pytest.raises(ValidationError):
        Settings(
            env="test",
            auth_mode="wechat",
            wechat_app_id=_APP_ID,
            wechat_app_secret=SecretStr(_APP_SECRET),
            wechat_code_exchange_burst=burst,
        )


def _body_limit_settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'wechat-body-limit.db'}",
        database_auto_create=True,
        auth_mode="wechat",
        wechat_app_id=_APP_ID,
        wechat_app_secret=SecretStr(_APP_SECRET),
        confirmation_secret=SecretStr("e" * 32),
        asr_provider="disabled",
    )


@pytest.mark.asyncio
async def test_concurrent_wechat_logins_converge_to_one_active_session(tmp_path: Path) -> None:
    app = create_app(_body_limit_settings(tmp_path))
    app.state.wechat_client = FakeWeChatClient()

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            responses = await asyncio.gather(
                client.post("/api/auth/wechat/login", json={"code": _LOGIN_CODE}),
                client.post("/api/auth/wechat/login", json={"code": _LOGIN_CODE}),
            )
            assert [response.status_code for response in responses] == [200, 200]
            tokens = [response.json()["session_token"] for response in responses]
            statuses = await asyncio.gather(
                *(client.get("/api/auth/session", headers=bearer(token)) for token in tokens)
            )

        async with app.state.session_factory() as session:
            records = list(
                await session.scalars(
                    select(OidcSession).where(OidcSession.issuer == wechat_session_issuer(_APP_ID))
                )
            )

    assert sorted(response.status_code for response in statuses) == [200, 401]
    assert len(records) == 1
    assert records[0].revoked_at is None


@pytest.mark.asyncio
async def test_declared_oversized_wechat_login_is_rejected_before_json_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "WECHAT_LOGIN_BODY_LIMIT", 64)
    app = create_app(_body_limit_settings(tmp_path))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/auth/wechat/login",
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": "65",
                },
                content=b"{}",
            )
            unrelated = await client.post(
                "/api/auth/wechat/logout",
                headers={"Content-Length": "65"},
                content=b"{}",
            )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "wechat_login_body_too_large"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert unrelated.status_code == 401


@pytest.mark.asyncio
async def test_chunked_oversized_wechat_login_is_rejected_while_streaming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module, "WECHAT_LOGIN_BODY_LIMIT", 64)
    app = create_app(_body_limit_settings(tmp_path))

    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"code":"'
        yield b"a" * 64
        yield b'"}'

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/auth/wechat/login",
                headers={"Content-Type": "application/json"},
                content=chunks(),
            )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "wechat_login_body_too_large"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
