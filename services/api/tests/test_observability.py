import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.metrics import InMemoryMetrics, observe_component
from app.main import create_app
from tests.helpers import confirmed_write


def test_request_metrics_use_route_templates_and_status_families(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200
    assert client.get("/does-not-exist/private-id?token=private-value").status_code == 404

    response = client.get("/api/metrics")

    assert response.status_code == 200
    metrics = response.json()["http"]
    live = next(item for item in metrics if item["route"] == "/health/live")
    missing = next(item for item in metrics if item["route"] == "__unmatched__")
    assert live["method"] == "GET"
    assert live["status_family"] == "2xx"
    assert live["count"] >= 1
    assert live["error_count"] == 0
    assert missing["status_family"] == "4xx"
    assert missing["error_count"] == 1
    assert "private-id" not in json.dumps(metrics)
    assert "private-value" not in json.dumps(metrics)


def test_request_log_omits_headers_query_values_and_untemplated_paths(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="campusvoice.request"):
        response = client.get(
            "/health/live?token=query-secret",
            headers={
                "Authorization": "Bearer header-secret",
                "X-Request-ID": "observable-request-123",
            },
        )

    assert response.status_code == 200
    record = next(item for item in reversed(caplog.records) if item.name == "campusvoice.request")
    payload = json.loads(record.getMessage())
    assert payload["request_id"] == "observable-request-123"
    assert payload["route"] == "/health/live"
    serialized = record.getMessage()
    assert "query-secret" not in serialized
    assert "header-secret" not in serialized


def test_invalid_incoming_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "short"})

    replacement = response.headers["x-request-id"]
    assert replacement != "short"
    assert len(replacement) == 32


def test_unauthenticated_domain_error_has_bearer_challenge_and_request_id(tmp_path: object) -> None:
    database_path = tmp_path / "jwt-auth.db"  # type: ignore[operator]
    settings = Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{database_path}",
        database_auto_create=True,
        auth_mode="jwt",
        jwt_issuer="https://issuer.example",
        jwt_audience="campusvoice",
        jwt_jwks_url="https://issuer.example/.well-known/jwks.json",
    )
    with TestClient(create_app(settings)) as jwt_client:
        response = jwt_client.get("/api/tasks", headers={"X-Request-ID": "auth-request-123"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["x-request-id"] == "auth-request-123"
    assert response.json()["request_id"] == "auth-request-123"


def test_component_metrics_are_bounded_and_count_errors() -> None:
    metrics = InMemoryMetrics()
    with observe_component(metrics, "intent", "parse"):
        pass
    with observe_component(metrics, "verification", "verify") as observation:
        observation.error = True
    with (
        pytest.raises(RuntimeError, match="provider failed"),
        observe_component(metrics, "llm", "complete"),
    ):
        raise RuntimeError("provider failed")

    components = metrics.snapshot()["components"]
    assert {(item["component"], item["outcome"]) for item in components} == {
        ("intent", "ok"),
        ("llm", "error"),
        ("verification", "error"),
    }
    assert all(item["error_count"] == 1 for item in components if item["outcome"] == "error")
    with pytest.raises(ValueError, match="unsupported metric operation"):
        metrics.record_component(
            component="action",
            operation="entity-id-would-be-unbounded",
            outcome="ok",
            duration_seconds=0,
        )


def test_intent_and_retrieval_paths_record_component_metrics_without_content(
    client: TestClient,
) -> None:
    intent_text = "创建一个不应出现在指标里的私密待办"
    parsed = client.post("/api/intent/parse", json={"text": intent_text})
    assert parsed.status_code == 200, parsed.text

    uploaded = client.post(
        "/api/documents",
        files={"file": ("synthetic.txt", "合成校历通知：七月二十日放假。", "text/plain")},
        data={"title": "合成校历"},
    )
    assert uploaded.status_code == 201, uploaded.text
    searched = client.post(
        "/api/knowledge/search",
        json={"query": "放假日期", "top_k": 3, "min_similarity": 0},
    )
    assert searched.status_code == 200, searched.text
    assert searched.json()["results"]

    components = client.get("/api/metrics").json()["components"]
    labels = {(item["component"], item["operation"], item["outcome"]) for item in components}
    assert ("intent", "parse", "ok") in labels
    assert ("retrieval", "search", "ok") in labels
    serialized = json.dumps(components, ensure_ascii=False)
    assert intent_text not in serialized
    assert "放假日期" not in serialized


def test_direct_mutation_records_action_and_verification_outcomes(client: TestClient) -> None:
    created = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {"title": "指标只记录标签，不记录这个标题"},
    )
    assert created.status_code == 201, created.text

    components = client.get("/api/metrics").json()["components"]
    labels = {(item["component"], item["operation"], item["outcome"]) for item in components}
    assert ("action", "execute", "ok") in labels
    assert ("verification", "verify", "ok") in labels
    assert "指标只记录标签" not in json.dumps(components, ensure_ascii=False)


def test_unhandled_exception_response_and_log_do_not_expose_exception_text(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'exception.db'}",
        database_auto_create=True,
    )
    application = create_app(settings)

    @application.get("/explode/{secret_id}")
    async def explode(secret_id: str) -> None:
        raise RuntimeError(f"student transcript {secret_id}")

    with (
        TestClient(application) as exception_client,
        caplog.at_level(logging.ERROR, logger="campusvoice.request"),
    ):
        response = exception_client.get(
            "/explode/private-student-text",
            headers={"X-Request-ID": "exception-request-123"},
        )

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "exception-request-123"
    assert response.json()["error"]["code"] == "internal_error"
    record = next(item for item in reversed(caplog.records) if item.name == "campusvoice.request")
    assert json.loads(record.getMessage())["route"] == "/explode/{secret_id}"
    assert "private-student-text" not in record.getMessage()
    assert "student transcript" not in record.getMessage()
