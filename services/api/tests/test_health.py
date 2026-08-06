import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from app import __version__ as package_version
from app.services import health as health_module
from app.services.health import expected_alembic_heads


def _database_path(client: TestClient) -> Path:
    database_url = client.app.state.settings.database_url
    raw_path = make_url(database_url).database
    assert raw_path is not None
    return Path(raw_path)


def _stamp_revisions(client: TestClient, revisions: tuple[str, ...]) -> None:
    with closing(sqlite3.connect(_database_path(client))) as connection, connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS alembic_version "
            "(version_num VARCHAR(255) NOT NULL PRIMARY KEY)"
        )
        connection.execute("DELETE FROM alembic_version")
        connection.executemany(
            "INSERT INTO alembic_version (version_num) VALUES (?)",
            ((revision,) for revision in revisions),
        )


def test_liveness_is_process_only_and_returns_request_id(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "probe-request-123"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "probe-request-123"
    assert response.json() == {
        "status": "ok",
        "service": "CampusVoice API",
        "version": "0.3.0",
    }
    assert package_version == response.json()["version"]


def test_readiness_rejects_database_without_alembic_revision(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["migrations"]["status"] == "error"
    assert body["checks"]["asr"]["status"] == "disabled"
    assert body["checks"]["retriever"]["status"] == "ok"
    assert body["checks"]["llm"]["status"] == "disabled"
    assert "ffmpeg" not in body["checks"]


def test_readiness_accepts_database_at_current_alembic_head(client: TestClient) -> None:
    heads = expected_alembic_heads()
    assert heads
    _stamp_revisions(client, heads)

    root_response = client.get("/health/ready")
    api_response = client.get("/api/health")

    assert root_response.status_code == 200
    assert api_response.status_code == 200
    assert root_response.json()["status"] == "ok"
    assert root_response.json()["checks"]["migrations"]["status"] == "ok"
    assert api_response.json() == root_response.json()


def test_readiness_rejects_stale_alembic_revision(client: TestClient) -> None:
    _stamp_revisions(client, ("stale_revision",))

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["migrations"] == {
        "status": "error",
        "message": "Database migration revision does not match the application head",
    }


def test_readiness_rejects_unreachable_shared_asr_quota(client: TestClient) -> None:
    class _UnavailableQuota:
        async def health_check(self) -> bool:
            return False

        async def close(self) -> None:
            return None

    _stamp_revisions(client, expected_alembic_heads())
    client.app.state.settings = client.app.state.settings.model_copy(
        update={
            "asr_quota_backend": "redis",
            "asr_redis_url": SecretStr("redis://redis:6379/0"),
        }
    )
    client.app.state.asr_connections = _UnavailableQuota()

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["asr_quota"] == {
        "status": "error",
        "message": "Redis ASR quota backend is unreachable",
    }


@pytest.mark.parametrize(
    ("ffmpeg_path", "expected_http_status", "expected_check_status"),
    [
        (None, 503, "error"),
        ("/usr/bin/ffmpeg", 200, "ok"),
    ],
)
@pytest.mark.parametrize("auth_mode", ["demo", "jwt", "oidc", "wechat", "oidc_wechat"])
def test_asr_readiness_requires_ffmpeg_for_every_auth_mode(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    ffmpeg_path: str | None,
    expected_http_status: int,
    expected_check_status: str,
    auth_mode: str,
) -> None:
    _stamp_revisions(client, expected_alembic_heads())
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"auth_mode": auth_mode, "asr_provider": "funasr"}
    )
    monkeypatch.setattr(health_module, "_module_available", lambda _name: True)
    monkeypatch.setattr(health_module, "which", lambda _name: ffmpeg_path)

    response = client.get("/health/ready")

    assert response.status_code == expected_http_status
    assert response.json()["checks"]["asr"]["status"] == "ok"
    assert response.json()["checks"]["ffmpeg"] == {
        "status": expected_check_status,
        "message": (
            "FFmpeg is available for MP3 decoding"
            if ffmpeg_path
            else "FFmpeg is unavailable for MP3 decoding"
        ),
    }


@pytest.mark.parametrize("auth_mode", ["demo", "jwt", "oidc", "wechat", "oidc_wechat"])
def test_disabled_asr_omits_ffmpeg_for_every_auth_mode(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    auth_mode: str,
) -> None:
    _stamp_revisions(client, expected_alembic_heads())
    client.app.state.settings = client.app.state.settings.model_copy(
        update={"auth_mode": auth_mode, "asr_provider": "disabled"}
    )

    def unexpected_ffmpeg_probe(_name: str) -> None:
        raise AssertionError("disabled ASR must not probe FFmpeg")

    monkeypatch.setattr(health_module, "which", unexpected_ffmpeg_probe)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["asr"]["status"] == "disabled"
    assert "ffmpeg" not in response.json()["checks"]
