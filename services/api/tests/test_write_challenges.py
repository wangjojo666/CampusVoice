import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.security.authentication import DemoAuthenticator
from app.security.write_challenges import consume_write_challenge
from app.services.errors import ConflictError


def _issue(
    client: TestClient,
    method: str,
    path: str,
    body: object,
) -> dict[str, object]:
    response = client.post(
        "/api/auth/write-challenges",
        json={"method": method, "path": path, "body": body},
    )
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    return response.json()


def test_legacy_boolean_header_cannot_replace_server_challenge_and_replay_fails(
    client: TestClient,
) -> None:
    payload = {"title": "服务端挑战任务", "description": "绑定请求体"}
    legacy = client.post(
        "/api/tasks",
        json=payload,
        headers={"X-User-Confirmed": "true", "X-Second-Confirmation": "true"},
    )
    assert legacy.status_code == 428
    assert legacy.json()["error"]["code"] == "write_challenge_required"

    issued = _issue(
        client,
        "POST",
        "/api/tasks",
        {"description": "绑定请求体", "title": "服务端挑战任务"},
    )
    assert issued["stage"] == 1
    assert issued["required_stages"] == 1
    headers = {"X-Write-Challenge": str(issued["challenge"])}
    created = client.post("/api/tasks", json=payload, headers=headers)
    assert created.status_code == 201, created.text

    replayed = client.post("/api/tasks", json=payload, headers=headers)
    assert replayed.status_code == 409
    assert replayed.json()["error"]["code"] == "invalid_write_challenge"


def test_body_tampering_and_cross_user_use_are_rejected_without_consuming_valid_grant(
    client: TestClient,
) -> None:
    payload = {"title": "不可篡改任务", "priority": "high"}
    issued = _issue(client, "POST", "/api/tasks", payload)
    headers = {"X-Write-Challenge": str(issued["challenge"])}

    tampered = client.post(
        "/api/tasks",
        json=payload | {"priority": "low"},
        headers=headers,
    )
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "invalid_write_challenge"
    assert client.post("/api/tasks", json=payload, headers=headers).status_code == 201

    cross_user_payload = {"title": "跨用户挑战"}
    cross_user = _issue(client, "POST", "/api/tasks", cross_user_payload)
    cross_headers = {"X-Write-Challenge": str(cross_user["challenge"])}
    client.app.state.authenticator = DemoAuthenticator("user_other")
    rejected = client.post("/api/tasks", json=cross_user_payload, headers=cross_headers)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "invalid_write_challenge"

    settings = client.app.state.settings
    client.app.state.authenticator = DemoAuthenticator(settings.demo_user_id)
    accepted = client.post("/api/tasks", json=cross_user_payload, headers=cross_headers)
    assert accepted.status_code == 201, accepted.text


def test_expired_and_wrong_path_challenges_are_rejected(client: TestClient) -> None:
    payload = {"title": "即将过期任务"}
    wrong_path = _issue(client, "POST", "/api/tasks", payload)
    wrong_path_response = client.post(
        "/api/events",
        json=payload | {"start_at": "2026-07-20T01:00:00Z"},
        headers={"X-Write-Challenge": str(wrong_path["challenge"])},
    )
    assert wrong_path_response.status_code == 409

    expiring = _issue(client, "POST", "/api/tasks", payload)
    future = datetime.now(UTC) + timedelta(hours=1)
    with patch("app.security.write_challenges.utc_now", return_value=future):
        expired = client.post(
            "/api/tasks",
            json=payload,
            headers={"X-Write-Challenge": str(expiring["challenge"])},
        )
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "invalid_write_challenge"


def test_challenge_consumption_is_atomic_across_sessions(client: TestClient) -> None:
    payload = {"title": "并发消费"}
    issued = _issue(client, "POST", "/api/tasks", payload)
    challenge = str(issued["challenge"])
    factory = client.app.state.session_factory
    settings = client.app.state.settings

    async def consume_once() -> str:
        async with factory() as session:
            try:
                await consume_write_challenge(
                    session,
                    user_id=settings.demo_user_id,
                    challenge=challenge,
                    method="POST",
                    path="/api/tasks",
                    body=payload,
                    api_prefix=settings.api_prefix,
                )
            except ConflictError:
                return "conflict"
        return "consumed"

    async def race() -> list[str]:
        return list(await asyncio.gather(consume_once(), consume_once()))

    assert sorted(asyncio.run(race())) == ["conflict", "consumed"]


def test_hotword_delete_requires_consumed_stage_one_before_final_challenge(
    client: TestClient,
) -> None:
    create_payload = {"term": "一次性热词", "category": "custom"}
    created_challenge = _issue(client, "POST", "/api/hotwords", create_payload)
    created = client.post(
        "/api/hotwords",
        json=create_payload,
        headers={"X-Write-Challenge": str(created_challenge["challenge"])},
    )
    assert created.status_code == 201, created.text
    path = f"/api/hotwords/{created.json()['record_id']}"

    first = _issue(client, "DELETE", path, None)
    assert (first["stage"], first["required_stages"]) == (1, 2)
    premature = client.delete(
        path,
        headers={"X-Write-Challenge": str(first["challenge"])},
    )
    assert premature.status_code == 409

    second = client.post(
        "/api/auth/write-challenges/advance",
        json={"challenge": first["challenge"]},
    )
    assert second.status_code == 200, second.text
    assert second.headers["Cache-Control"] == "no-store"
    assert second.headers["Pragma"] == "no-cache"
    assert (second.json()["stage"], second.json()["required_stages"]) == (2, 2)
    assert (
        client.post(
            "/api/auth/write-challenges/advance",
            json={"challenge": first["challenge"]},
        ).status_code
        == 409
    )

    deleted = client.delete(
        path,
        headers={"X-Write-Challenge": second.json()["challenge"]},
    )
    assert deleted.status_code == 200, deleted.text
    assert (
        client.delete(
            path,
            headers={"X-Write-Challenge": second.json()["challenge"]},
        ).status_code
        == 409
    )


def test_challenge_issuer_rejects_unprotected_targets(client: TestClient) -> None:
    response = client.post(
        "/api/auth/write-challenges",
        json={"method": "DELETE", "path": "/api/tasks/task-1", "body": None},
    )
    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert response.json()["error"]["code"] == "unsupported_write_challenge_target"
