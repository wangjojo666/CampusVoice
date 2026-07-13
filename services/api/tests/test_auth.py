import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app
from app.models.entities import User
from app.security.authentication import (
    AuthenticationError,
    AuthPrincipal,
    JwtAuthenticator,
)
from app.security.websocket_tickets import consume_websocket_ticket
from tests.helpers import confirmed_write

_ISSUER = "https://identity.campus.test"
_AUDIENCE = "campusvoice-api"
_ALLOWED_ORIGIN = "https://campus.test"


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "env": "production",
        "auth_mode": "jwt",
        "jwt_issuer": _ISSUER,
        "jwt_audience": _AUDIENCE,
        "jwt_jwks_url": f"{_ISSUER}/.well-known/jwks.json",
        "jwt_algorithms": ["RS256"],
        "confirmation_secret": SecretStr("production-confirmation-secret-32-bytes"),
        "database_auto_create": False,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _jwt_test_settings(tmp_path: Path) -> Settings:
    return Settings(
        env="test",
        auth_mode="jwt",
        jwt_issuer=_ISSUER,
        jwt_audience=_AUDIENCE,
        jwt_jwks_url=f"{_ISSUER}/.well-known/jwks.json",
        jwt_algorithms=["RS256"],
        cors_origins=[_ALLOWED_ORIGIN],
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'auth-test.db'}",
        database_auto_create=True,
    )


def _claims(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    values: dict[str, object] = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": "student-001",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "name": "测试学生",
    }
    values.update(overrides)
    return values


class _StaticJwks:
    def __init__(self, public_key: object) -> None:
        self._public_key = public_key

    def get_signing_key_from_jwt(self, _token: str) -> SimpleNamespace:
        return SimpleNamespace(key=self._public_key)


class _PrincipalAuthenticator:
    def __init__(self) -> None:
        self._principals = {
            "alice-token": AuthPrincipal(
                user_id="user_alice",
                subject="alice",
                issuer=_ISSUER,
                display_name="Alice",
            ),
            "bob-token": AuthPrincipal(
                user_id="user_bob",
                subject="bob",
                issuer=_ISSUER,
                display_name="Bob",
            ),
        }

    async def authenticate(self, token: str | None) -> AuthPrincipal:
        principal = self._principals.get(token or "")
        if principal is None:
            raise AuthenticationError("invalid_access_token", "The access token is invalid")
        return principal


@pytest.fixture
def principal_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(_jwt_test_settings(tmp_path))
    app.state.authenticator = _PrincipalAuthenticator()
    with TestClient(app) as client:
        yield app, client


def _auth_headers(token: str, **extra: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **extra}


def test_production_rejects_demo_authentication() -> None:
    with pytest.raises(ValueError, match="demo authentication"):
        Settings(env="production", auth_mode="demo")


def test_production_rejects_missing_jwt_configuration() -> None:
    with pytest.raises(ValueError, match="JWT authentication requires"):
        Settings(
            env="production",
            auth_mode="jwt",
            confirmation_secret=SecretStr("production-confirmation-secret-32-bytes"),
        )


@pytest.mark.parametrize("algorithm", ["HS256", "none", "foobar"])
def test_production_rejects_non_asymmetric_jwt_algorithms(algorithm: str) -> None:
    with pytest.raises(ValueError, match="asymmetric signing algorithm"):
        _production_settings(jwt_algorithms=[algorithm])


def test_production_rejects_database_auto_create() -> None:
    with pytest.raises(ValueError, match="auto-create is forbidden"):
        _production_settings(database_auto_create=True)


def test_production_rejects_empty_confirmation_secret() -> None:
    with pytest.raises(ValueError, match="CAMPUSVOICE_CONFIRMATION_SECRET"):
        _production_settings(confirmation_secret=SecretStr(""))


def test_test_environment_replaces_empty_confirmation_secret(tmp_path: Path) -> None:
    settings = Settings(
        env="test",
        auth_mode="demo",
        confirmation_secret=SecretStr(""),
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'empty-secret.db'}",
        database_auto_create=True,
    )

    with TestClient(create_app(settings)) as client:
        generated = client.app.state.settings.confirmation_secret

    assert generated is not None
    assert len(generated.get_secret_value()) >= 32


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("missing", "authentication_required"),
        ("malformed", "invalid_access_token"),
        ("wrong_signature", "invalid_access_token"),
        ("wrong_issuer", "invalid_access_token"),
        ("wrong_audience", "invalid_access_token"),
        ("missing_subject", "invalid_access_token"),
        ("blank_subject", "invalid_access_token"),
    ],
)
def test_jwt_failures_return_bearer_challenge(
    tmp_path: Path,
    case: str,
    expected_code: str,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    authenticator = JwtAuthenticator(
        issuer=_ISSUER,
        audience=_AUDIENCE,
        jwks_url=f"{_ISSUER}/.well-known/jwks.json",
        algorithms=["RS256"],
    )
    authenticator._jwks = _StaticJwks(private_key.public_key())  # type: ignore[assignment]
    app = create_app(_jwt_test_settings(tmp_path))
    app.state.authenticator = authenticator

    headers: dict[str, str] = {}
    if case != "missing":
        claims = _claims()
        signing_key = private_key
        if case == "malformed":
            token = "not-a-jwt"
        else:
            if case == "wrong_signature":
                signing_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
            elif case == "wrong_issuer":
                claims["iss"] = "https://attacker.invalid"
            elif case == "wrong_audience":
                claims["aud"] = "another-api"
            elif case == "missing_subject":
                claims.pop("sub")
            elif case == "blank_subject":
                claims["sub"] = "  "
            token = jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "test"})
        headers = _auth_headers(token)

    with TestClient(app) as client:
        response = client.get("/api/tasks", headers=headers)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == expected_code


def test_forged_user_header_does_not_cross_task_or_action_boundaries(
    principal_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = principal_client
    created = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {"title": "Alice 的私有任务"},
        headers=_auth_headers("alice-token"),
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["record_id"]

    bob_spoof = _auth_headers("bob-token", **{"X-User-ID": "user_alice"})
    bob_tasks = client.get("/api/tasks", headers=bob_spoof)
    assert bob_tasks.status_code == 200
    assert bob_tasks.json()["total"] == 0
    assert client.get("/api/tasks", headers=_auth_headers("alice-token")).json()["total"] == 1

    assert (
        confirmed_write(
            client,
            "PATCH",
            f"/api/tasks/{task_id}",
            {"title": "Bob 不可修改", "expected_version": 1},
            headers=bob_spoof,
        ).status_code
        == 404
    )
    assert client.delete(f"/api/tasks/{task_id}", headers=bob_spoof).status_code == 404

    prepared = client.post(
        "/api/actions/prepare",
        headers=_auth_headers("alice-token"),
        json={"action": "create_task", "payload": {"title": "Alice 的待确认任务"}},
    )
    assert prepared.status_code == 201, prepared.text
    action_id = prepared.json()["id"]
    challenge = client.post(
        f"/api/actions/{action_id}/challenge", headers=_auth_headers("alice-token")
    ).json()["challenge"]

    cross_user_requests = [
        client.get(f"/api/actions/{action_id}", headers=bob_spoof),
        client.post(f"/api/actions/{action_id}/challenge", headers=bob_spoof),
        client.post(
            f"/api/actions/{action_id}/confirm",
            headers=bob_spoof,
            json={"confirmed": True, "challenge": challenge},
        ),
        client.post(
            f"/api/actions/{action_id}/cancel",
            headers=bob_spoof,
            json={"reason": "cross-user"},
        ),
        client.post(f"/api/actions/{action_id}/execute", headers=bob_spoof),
        client.post(f"/api/actions/{action_id}/undo", headers=bob_spoof),
    ]
    assert {response.status_code for response in cross_user_requests} == {404}
    assert {response.json()["error"]["code"] for response in cross_user_requests} == {"not_found"}


def test_inactive_principal_is_rejected(
    principal_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = principal_client
    headers = _auth_headers("alice-token")
    assert client.get("/api/tasks", headers=headers).status_code == 200

    async def deactivate() -> None:
        async with app.state.session_factory() as session, session.begin():
            user = await session.get(User, "user_alice")
            assert user is not None
            user.is_active = False

    asyncio.run(deactivate())
    response = client.get("/api/tasks", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "user_inactive"


def test_websocket_ticket_is_origin_bound_and_single_use(
    principal_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = principal_client
    auth = _auth_headers("alice-token")
    missing_origin = client.post("/api/auth/ws-ticket", headers=auth)
    assert missing_origin.status_code == 403
    assert missing_origin.headers["Cache-Control"] == "no-store"
    assert missing_origin.headers["Pragma"] == "no-cache"
    assert (
        client.post(
            "/api/auth/ws-ticket",
            headers=auth | {"Origin": "https://attacker.invalid"},
        ).status_code
        == 403
    )

    issued = client.post(
        "/api/auth/ws-ticket",
        headers=auth | {"Origin": _ALLOWED_ORIGIN},
    )
    assert issued.status_code == 200, issued.text
    assert issued.headers["Cache-Control"] == "no-store"
    assert issued.headers["Pragma"] == "no-cache"
    ticket = issued.json()["ticket"]

    async def consume(origin: str) -> str | None:
        async with app.state.session_factory() as session:
            return await consume_websocket_ticket(session, ticket=ticket, origin=origin)

    assert asyncio.run(consume("https://attacker.invalid")) is None
    assert asyncio.run(consume(_ALLOWED_ORIGIN)) == "user_alice"
    assert asyncio.run(consume(_ALLOWED_ORIGIN)) is None
