import asyncio
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.db.base import Base
from app.db.session import create_database_engine, create_session_factory
from app.db.types import utc_now
from app.main import create_app
from app.models.entities import OidcLoginTransaction
from app.security.oidc import OidcClient, OidcError, consume_oidc_transaction

ISSUER = "https://id.campus.test"
CLIENT_ID = "campusvoice"


def _settings(tmp_path: object) -> Settings:
    return Settings(
        env="test",
        auth_mode="oidc",
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret=SecretStr("server-only-secret"),
        oidc_redirect_uri="https://api.campus.test/api/auth/callback",
        oidc_post_login_redirect_uri="https://app.campus.test/",
        oidc_post_logout_redirect_uri="https://app.campus.test/signed-out",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'oidc.db'}",  # type: ignore[operator]
        database_auto_create=True,
    )


def test_oidc_pkce_session_callback_logout_and_replay_protection(tmp_path: object) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk.update({"kid": "campus-key", "use": "sig", "alg": "RS256"})
    expected_nonce = ""
    token_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_request
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                    "end_session_endpoint": f"{ISSUER}/logout",
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [public_jwk]})
        if request.url.path == "/token":
            token_request = request
            now = datetime.now(UTC)
            encoded = jwt.encode(
                {
                    "iss": ISSUER,
                    "aud": CLIENT_ID,
                    "sub": "student-42",
                    "iat": now,
                    "exp": now + timedelta(minutes=15),
                    "nonce": expected_nonce,
                    "name": "校园学生",
                },
                private_key,
                algorithm="RS256",
                headers={"kid": "campus-key"},
            )
            return httpx.Response(
                200, json={"id_token": encoded, "access_token": "not-for-browser"}
            )
        raise AssertionError(f"unexpected OIDC request: {request.url}")

    settings = _settings(tmp_path)
    app = create_app(settings)
    app.state.oidc_client = OidcClient(settings, transport=httpx.MockTransport(handler))
    with TestClient(app, follow_redirects=False) as client:
        login = client.get("/api/auth/login")
        assert login.status_code == 302
        assert login.headers["referrer-policy"] == "no-referrer"
        authorization = urlparse(login.headers["location"])
        query = parse_qs(authorization.query)
        expected_nonce = query["nonce"][0]
        assert query["code_challenge_method"] == ["S256"]
        assert query["state"] and query["code_challenge"]
        assert "server-only-secret" not in login.headers["location"]

        wrong_state = client.get("/api/auth/callback?code=ok&state=wrong")
        assert wrong_state.status_code == 400

        login = client.get("/api/auth/login")
        query = parse_qs(urlparse(login.headers["location"]).query)
        expected_nonce = query["nonce"][0]
        callback = client.get(f"/api/auth/callback?code=ok&state={query['state'][0]}")
        assert callback.status_code == 302
        assert callback.headers["location"] == "https://app.campus.test/"
        assert "HttpOnly" in callback.headers["set-cookie"]
        assert "Path=/api" in callback.headers["set-cookie"]
        assert callback.headers["referrer-policy"] == "no-referrer"
        assert "access_token" not in callback.headers["set-cookie"]
        assert token_request is not None
        assert b"code_verifier=" in token_request.content
        assert token_request.headers["authorization"].startswith("Basic ")

        session = client.get("/api/auth/session")
        assert session.status_code == 200
        assert session.json()["display_name"] == "校园学生"
        assert session.headers["cache-control"] == "no-store"

        replay = client.get(f"/api/auth/callback?code=ok&state={query['state'][0]}")
        assert replay.status_code == 400

        rejected_logout = client.post("/api/auth/logout", headers={"Origin": "https://evil.test"})
        assert rejected_logout.status_code == 403
        assert client.get("/api/auth/session").status_code == 200

        logout = client.post("/api/auth/logout", headers={"Origin": "http://localhost:3000"})
        assert logout.status_code == 200
        assert logout.json()["logout_url"].startswith(f"{ISSUER}/logout?")
        assert client.get("/api/auth/session").status_code == 401

        second_login = client.get("/api/auth/login")
        second_query = parse_qs(urlparse(second_login.headers["location"]).query)
        expected_nonce = second_query["nonce"][0]
        second_callback = client.get(f"/api/auth/callback?code=ok&state={second_query['state'][0]}")
        assert second_callback.status_code == 302

        raw_session = client.cookies.get(settings.oidc_session_cookie_name)
        assert raw_session
        with sqlite3.connect(Path(tmp_path) / "oidc.db") as database:
            database.execute(
                "UPDATE oidc_sessions SET expires_at = ? WHERE session_hash = ?",
                ("2000-01-01 00:00:00.000000", hashlib.sha256(raw_session.encode()).hexdigest()),
            )
        expired = client.get("/api/auth/session")
        assert expired.status_code == 401
        assert expired.json()["error"]["code"] == "invalid_session"


def test_oidc_provider_callback_error_is_bounded_and_clears_flow(tmp_path: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
                "code_challenge_methods_supported": ["S256"],
            },
        )

    settings = _settings(tmp_path)
    app = create_app(settings)
    app.state.oidc_client = OidcClient(settings, transport=httpx.MockTransport(handler))
    with TestClient(app, follow_redirects=False) as client:
        login = client.get("/api/auth/login")
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        callback = client.get(
            f"/api/auth/callback?error=access_denied&error_description=secret&state={state}"
        )
        assert callback.status_code == 302
        assert callback.headers["location"].endswith("?auth_error=identity_provider_rejected")
        assert "secret" not in callback.headers["location"]


def test_production_oidc_configuration_fails_closed() -> None:
    common = {
        "env": "production",
        "auth_mode": "oidc",
        "oidc_issuer": ISSUER,
        "oidc_client_id": CLIENT_ID,
        "oidc_redirect_uri": "https://api.campus.test/api/auth/callback",
        "oidc_post_login_redirect_uri": "https://app.campus.test/",
        "oidc_post_logout_redirect_uri": "https://app.campus.test/signed-out",
        "confirmation_secret": SecretStr("production-confirmation-secret-32-bytes"),
        "database_auto_create": False,
    }
    configured = Settings(**common)  # type: ignore[arg-type]
    assert configured.auth_mode == "oidc"

    with pytest.raises(ValueError, match="OIDC authentication requires"):
        Settings(
            env="production",
            auth_mode="oidc",
            confirmation_secret=common["confirmation_secret"],
            database_auto_create=False,
        )
    with pytest.raises(ValueError, match="valid HTTPS URL"):
        Settings(**{**common, "oidc_redirect_uri": "http://api.campus.test/api/auth/callback"})
    with pytest.raises(ValueError, match="valid HTTPS URL"):
        Settings(**{**common, "oidc_issuer": "https://"})
    with pytest.raises(ValueError, match="valid HTTPS URL"):
        Settings(**{**common, "oidc_issuer": f"{ISSUER}?tenant=other"})
    with pytest.raises(ValueError, match="wildcard CORS"):
        Settings(**{**common, "cors_origins": ["*"]})


@pytest.mark.asyncio
async def test_oidc_login_transaction_has_exactly_one_concurrent_consumer(
    tmp_path: Path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'atomic-oidc.db'}"
    engine = create_database_engine(database_url)
    factory = create_session_factory(engine)
    now = utc_now()
    flow = "flow-secret"
    state = "state-secret"
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session, session.begin():
        session.add(
            OidcLoginTransaction(
                flow_hash=hashlib.sha256(flow.encode()).hexdigest(),
                state_hash=hashlib.sha256(state.encode()).hexdigest(),
                nonce="nonce",
                code_verifier="v" * 64,
                expires_at=now + timedelta(minutes=5),
                created_at=now,
            )
        )

    async def consume() -> object:
        async with factory() as session:
            return await consume_oidc_transaction(
                session,
                flow=flow,
                state=state,
                now=now,
            )

    try:
        results = await asyncio.gather(consume(), consume())
        assert sum(result is not None for result in results) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("nonce", "nonce_mismatch"),
        ("issuer", "id_token_invalid"),
        ("audience", "id_token_invalid"),
        ("expired", "id_token_invalid"),
        ("authorized_party", "id_token_authorized_party_invalid"),
    ],
)
async def test_oidc_rejects_invalid_id_token_boundaries(
    tmp_path: Path,
    case: str,
    expected_code: str,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk.update({"kid": "negative-key", "use": "sig", "alg": "RS256"})
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "student-negative",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "nonce": "expected-nonce",
    }
    if case == "nonce":
        claims["nonce"] = "wrong-nonce"
    elif case == "issuer":
        claims["iss"] = "https://other-idp.test"
    elif case == "audience":
        claims["aud"] = "other-client"
    elif case == "expired":
        claims["exp"] = now - timedelta(minutes=1)
    else:
        claims["aud"] = [CLIENT_ID, "other-client"]
        claims["azp"] = "other-client"
    encoded = jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "negative-key"},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if request.url.path == "/token":
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"id_token": encoded})
        if request.url.path == "/jwks":
            return httpx.Response(200, json={"keys": [public_jwk]})
        raise AssertionError(f"unexpected request: {request.url}")

    settings = _settings(tmp_path).model_copy(update={"oidc_client_secret": None})
    client = OidcClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(OidcError) as captured:
        await client.exchange_code(
            code="authorization-code",
            verifier="v" * 64,
            expected_nonce="expected-nonce",
        )
    assert captured.value.code == expected_code
