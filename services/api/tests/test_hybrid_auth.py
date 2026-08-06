import asyncio
from collections.abc import Iterator, Sequence
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from starlette.websockets import WebSocketDisconnect

from app.api.dependencies import provision_principal
from app.core.config import Settings
from app.db.types import utc_now
from app.main import create_app
from app.models.entities import OidcSession
from app.security.authentication import (
    WECHAT_BEARER_PREFIX,
    AuthPrincipal,
    internal_user_id,
    wechat_session_issuer,
)
from app.security.oidc import OidcClient, token_hash
from app.security.wechat import WeChatIdentity
from app.services.asr import AsrSessionConfig, TranscriptResult

_APP_ID = "wx3648488d39d15ff4"
_APP_SECRET = "c" * 32
_OIDC_ISSUER = "https://identity.campus.test"
_ALLOWED_ORIGIN = "https://campus.test"
_LOGIN_CODE = "hybrid-login-code"


class FakeWeChatClient:
    async def exchange_code(self, code: str) -> WeChatIdentity:
        assert code == _LOGIN_CODE
        return WeChatIdentity(
            openid="hybrid-private-openid",
            unionid="hybrid-private-unionid",
        )


class FakeOidcClient:
    async def authorization_url(self, *, state: str, nonce: str, verifier: str) -> str:
        assert state and nonce and verifier
        return f"{_OIDC_ISSUER}/authorize?state={state}"

    async def logout_url(self) -> None:
        return None


class NoopAsrAdapter:
    provider_name = "hybrid-auth-test"

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


@pytest.fixture
def hybrid_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient, Settings]]:
    settings = Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'hybrid.db'}",
        database_auto_create=True,
        auth_mode="oidc_wechat",
        cors_origins=[_ALLOWED_ORIGIN],
        oidc_issuer=_OIDC_ISSUER,
        oidc_client_id="campusvoice-web",
        oidc_redirect_uri=f"{_ALLOWED_ORIGIN}/api/auth/callback",
        oidc_post_login_redirect_uri=f"{_ALLOWED_ORIGIN}/",
        oidc_post_logout_redirect_uri=f"{_ALLOWED_ORIGIN}/",
        wechat_app_id=_APP_ID,
        wechat_app_secret=SecretStr(_APP_SECRET),
        confirmation_secret=SecretStr("d" * 32),
        asr_provider="disabled",
    )
    app = create_app(settings)
    assert isinstance(app.state.oidc_client, OidcClient)
    assert app.state.wechat_client is not None
    app.state.oidc_client = FakeOidcClient()
    app.state.wechat_client = FakeWeChatClient()
    app.state.asr_adapter_factory = NoopAsrAdapter
    with TestClient(app) as client:
        yield app, client, settings


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_session(
    app: FastAPI,
    settings: Settings,
    *,
    token: str,
    issuer: str,
    label: str,
) -> str:
    user_id = internal_user_id(issuer, label)

    async def seed() -> None:
        principal = AuthPrincipal(
            user_id=user_id,
            subject=f"subject-{label}",
            issuer=issuer,
            display_name=label,
            authentication_method="oidc_session",
        )
        async with app.state.session_factory() as session:
            await provision_principal(session, principal, settings)
            now = utc_now()
            session.add(
                OidcSession(
                    session_hash=token_hash(token),
                    user_id=user_id,
                    subject=principal.subject,
                    issuer=issuer,
                    display_name=principal.display_name,
                    roles=[],
                    expires_at=now + timedelta(hours=1),
                    last_seen_at=now,
                    created_at=now,
                )
            )
            await session.commit()

    asyncio.run(seed())
    return user_id


def _session_record(app: FastAPI, token: str) -> OidcSession:
    async def load() -> OidcSession:
        async with app.state.session_factory() as session:
            record = await session.get(OidcSession, token_hash(token))
            assert record is not None
            session.expunge(record)
            return record

    return asyncio.run(load())


def _finish_websocket(client: TestClient, ticket: str, *, origin: str | None = None) -> None:
    headers = {"Origin": origin} if origin else {}
    protocols = ["campusvoice", f"campusvoice.ticket.{ticket}"]
    with client.websocket_connect(
        "/ws/asr",
        headers=headers,
        subprotocols=protocols,
    ) as websocket:
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
        assert websocket.receive_json()["type"] == "ready"
        websocket.send_json({"type": "stop"})
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_json()
        assert closed.value.code == 1000


def test_hybrid_routes_cookie_and_prefixed_bearer_without_type_confusion(
    hybrid_client: tuple[FastAPI, TestClient, Settings],
) -> None:
    app, client, settings = hybrid_client
    oidc_token = "oidc-cookie-" + ("o" * 48)
    oidc_user_id = _seed_session(
        app,
        settings,
        token=oidc_token,
        issuer=_OIDC_ISSUER,
        label="oidc-user",
    )
    client.cookies.set(settings.oidc_session_cookie_name, oidc_token)

    oidc_status = client.get("/api/auth/session")
    assert oidc_status.status_code == 200, oidc_status.text
    assert oidc_status.json()["user_id"] == oidc_user_id

    oidc_login = client.get("/api/auth/login", follow_redirects=False)
    assert oidc_login.status_code == 302
    assert oidc_login.headers["location"].startswith(f"{_OIDC_ISSUER}/authorize?")

    wechat_login = client.post("/api/auth/wechat/login", json={"code": _LOGIN_CODE})
    assert wechat_login.status_code == 403
    assert wechat_login.json()["error"]["code"] == "origin_not_allowed"

    client.cookies.clear()
    wechat_login = client.post("/api/auth/wechat/login", json={"code": _LOGIN_CODE})
    assert wechat_login.status_code == 200, wechat_login.text
    wechat_token = wechat_login.json()["session_token"]
    assert wechat_token.startswith(WECHAT_BEARER_PREFIX)

    wechat_status = client.get("/api/auth/session", headers=_bearer(wechat_token))
    assert wechat_status.status_code == 200, wechat_status.text
    assert wechat_status.json()["user_id"] != oidc_user_id

    wrong_bearer_type = client.get("/api/auth/session", headers=_bearer(oidc_token))
    assert wrong_bearer_type.status_code == 401
    assert wrong_bearer_type.json()["error"]["code"] == "invalid_session_type"

    client.cookies.set(settings.oidc_session_cookie_name, wechat_token)
    wrong_cookie_type = client.get("/api/auth/session")
    assert wrong_cookie_type.status_code == 401
    assert wrong_cookie_type.json()["error"]["code"] == "invalid_session"

    wechat_record = _session_record(app, wechat_token)
    assert wechat_record.subject == wechat_record.user_id
    assert "hybrid-private-openid" not in wechat_record.subject
    assert "hybrid-private-unionid" not in wechat_record.subject


def test_hybrid_rejects_foreign_issuers_and_logout_does_not_revoke_them(
    hybrid_client: tuple[FastAPI, TestClient, Settings],
) -> None:
    app, client, settings = hybrid_client
    foreign_wechat = WECHAT_BEARER_PREFIX + ("f" * 48)
    _seed_session(
        app,
        settings,
        token=foreign_wechat,
        issuer=wechat_session_issuer("wx0000000000000000"),
        label="foreign-wechat",
    )

    rejected_wechat = client.get("/api/auth/session", headers=_bearer(foreign_wechat))
    assert rejected_wechat.status_code == 401
    assert rejected_wechat.json()["error"]["code"] == "invalid_session"

    logged_out = client.post("/api/auth/wechat/logout", headers=_bearer(foreign_wechat))
    assert logged_out.status_code == 200
    assert _session_record(app, foreign_wechat).revoked_at is None

    foreign_oidc = "foreign-oidc-" + ("x" * 48)
    _seed_session(
        app,
        settings,
        token=foreign_oidc,
        issuer="https://other-issuer.test",
        label="foreign-oidc",
    )
    client.cookies.set(settings.oidc_session_cookie_name, foreign_oidc)

    rejected_oidc = client.get("/api/auth/session")
    assert rejected_oidc.status_code == 401
    assert rejected_oidc.json()["error"]["code"] == "invalid_session"

    oidc_logout = client.post("/api/auth/logout", headers={"Origin": _ALLOWED_ORIGIN})
    assert oidc_logout.status_code == 200
    assert _session_record(app, foreign_oidc).revoked_at is None


def test_hybrid_ws_tickets_follow_authenticated_session_type(
    hybrid_client: tuple[FastAPI, TestClient, Settings],
) -> None:
    app, client, settings = hybrid_client
    oidc_token = "websocket-oidc-" + ("w" * 48)
    _seed_session(
        app,
        settings,
        token=oidc_token,
        issuer=_OIDC_ISSUER,
        label="websocket-oidc-user",
    )
    client.cookies.set(settings.oidc_session_cookie_name, oidc_token)

    missing_origin = client.post("/api/auth/ws-ticket")
    assert missing_origin.status_code == 403
    assert missing_origin.json()["error"]["code"] == "origin_not_allowed"

    oidc_ticket_response = client.post(
        "/api/auth/ws-ticket",
        headers={"Origin": _ALLOWED_ORIGIN},
    )
    assert oidc_ticket_response.status_code == 200, oidc_ticket_response.text
    _finish_websocket(
        client,
        oidc_ticket_response.json()["ticket"],
        origin=_ALLOWED_ORIGIN,
    )

    client.cookies.clear()
    login = client.post("/api/auth/wechat/login", json={"code": _LOGIN_CODE})
    assert login.status_code == 200, login.text
    wechat_token = login.json()["session_token"]
    wechat_ticket_response = client.post(
        "/api/auth/ws-ticket",
        headers=_bearer(wechat_token),
    )
    assert wechat_ticket_response.status_code == 200, wechat_ticket_response.text
    _finish_websocket(client, wechat_ticket_response.json()["ticket"])


def test_hybrid_configuration_requires_both_identity_providers() -> None:
    with pytest.raises(ValidationError, match="OIDC authentication requires"):
        Settings(
            env="test",
            auth_mode="oidc_wechat",
            wechat_app_id=_APP_ID,
            wechat_app_secret=SecretStr(_APP_SECRET),
        )

    with pytest.raises(ValidationError, match="WeChat authentication requires"):
        Settings(
            env="test",
            auth_mode="oidc_wechat",
            oidc_issuer=_OIDC_ISSUER,
            oidc_client_id="campusvoice-web",
            oidc_redirect_uri=f"{_ALLOWED_ORIGIN}/api/auth/callback",
            oidc_post_login_redirect_uri=f"{_ALLOWED_ORIGIN}/",
            oidc_post_logout_redirect_uri=f"{_ALLOWED_ORIGIN}/",
        )
