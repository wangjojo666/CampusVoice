import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.services.health import expected_alembic_heads


def _database_path(client: TestClient) -> Path:
    database_url = client.app.state.settings.database_url
    raw_path = make_url(database_url).database
    assert raw_path is not None
    return Path(raw_path)


def _stamp_revisions(client: TestClient, revisions: tuple[str, ...]) -> None:
    with sqlite3.connect(_database_path(client)) as connection:
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
        "version": "0.2.0",
    }


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
