import pytest
from fastapi.testclient import TestClient

from tests.helpers import confirm_action, confirmed_write


def test_task_crud_requires_confirmation_and_verifies_database(client: TestClient) -> None:
    unconfirmed = client.post("/api/tasks", json={"title": "机器学习作业"})
    assert unconfirmed.status_code == 428
    assert unconfirmed.json()["error"]["code"] == "write_challenge_required"
    assert client.get("/api/tasks").json()["total"] == 0

    create_payload = {
        "title": "机器学习作业",
        "due_at": "2026-07-18T18:00:00+08:00",
    }
    created = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        create_payload,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["success"] is True
    assert all(body["verified_fields"].values())
    assert body["record"]["due_at"] == "2026-07-18T10:00:00Z"
    task_id = body["record_id"]

    update_payload = {"priority": "high", "expected_version": 1}
    updated = confirmed_write(
        client,
        "PATCH",
        f"/api/tasks/{task_id}",
        update_payload,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["record"]["priority"] == "high"
    assert updated.json()["record"]["version"] == 2

    tasks = client.get("/api/tasks", params={"status": "pending"}).json()
    assert tasks["total"] == 1
    assert tasks["items"][0]["id"] == task_id


def test_duplicate_task_is_blocked_before_execution(client: TestClient) -> None:
    payload = {"title": "数据库复习", "course": "数据库"}
    first = confirmed_write(client, "POST", "/api/tasks", payload)
    assert first.status_code == 201

    duplicate = confirmed_write(client, "POST", "/api/tasks", payload)
    assert duplicate.status_code == 428
    pending = duplicate.json()["error"]["details"]["pending_action"]
    assert pending["state"] == "needs_input"
    assert pending["blocking_reasons"] == ["duplicate_record"]
    assert client.get("/api/tasks").json()["total"] == 1


def test_delete_needs_two_unique_confirmations_and_can_be_undone(client: TestClient) -> None:
    task_id = confirmed_write(client, "POST", "/api/tasks", {"title": "待删除任务"}).json()[
        "record_id"
    ]

    requested = client.delete(f"/api/tasks/{task_id}")
    assert requested.status_code == 428
    action = requested.json()["error"]["details"]["pending_action"]
    assert action["risk_level"] == "high"
    assert action["required_confirmations"] == 2
    action_id = action["id"]

    issued = client.post(f"/api/actions/{action_id}/challenge").json()["challenge"]
    first = client.post(
        f"/api/actions/{action_id}/confirm",
        json={"confirmed": True, "challenge": issued},
    )
    assert first.json()["state"] == "awaiting_second_confirmation"
    duplicate_click = client.post(
        f"/api/actions/{action_id}/confirm",
        json={"confirmed": True, "challenge": issued},
    )
    assert duplicate_click.status_code == 409
    assert client.post(f"/api/actions/{action_id}/execute").status_code == 409

    second = confirm_action(client, action_id)
    assert second.json()["state"] == "ready"
    deleted = client.post(f"/api/actions/{action_id}/execute")
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True
    assert deleted.json()["verified_fields"] == {"absent": True}
    assert client.get("/api/tasks").json()["total"] == 0

    undone = client.post(f"/api/actions/{action_id}/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["success"] is True
    assert undone.json()["original_action"] == "delete_task"
    assert client.get("/api/tasks").json()["total"] == 1


def test_optimistic_version_conflict_does_not_modify_task(client: TestClient) -> None:
    task_id = confirmed_write(client, "POST", "/api/tasks", {"title": "版本检查"}).json()[
        "record_id"
    ]
    response = confirmed_write(
        client,
        "PATCH",
        f"/api/tasks/{task_id}",
        {"title": "不应保存", "expected_version": 99},
    )
    assert response.status_code == 409
    task = client.get("/api/tasks").json()["items"][0]
    assert task["title"] == "版本检查"
    assert task["version"] == 1


def test_task_patch_requires_expected_version_without_modifying_record(
    client: TestClient,
) -> None:
    task_id = confirmed_write(client, "POST", "/api/tasks", {"title": "版本必填"}).json()[
        "record_id"
    ]
    before = client.get("/api/tasks").json()["items"][0]

    response = confirmed_write(
        client,
        "PATCH",
        f"/api/tasks/{task_id}",
        {"title": "不应保存"},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "expected_version"]
    assert response.json()["detail"][0]["type"] == "missing"
    after = client.get("/api/tasks").json()["items"][0]
    assert after == before


@pytest.mark.parametrize("field", ["title", "priority", "status", "source_type"])
def test_task_patch_rejects_null_required_fields_without_changing_version(
    client: TestClient,
    field: str,
) -> None:
    task_id = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {
            "title": "空值保护",
            "description": "可清空说明",
            "course": "数据库",
            "due_at": "2026-07-18T10:00:00Z",
            "reminder_at": "2026-07-18T09:00:00Z",
        },
    ).json()["record_id"]
    before = client.get("/api/tasks").json()["items"][0]

    response = confirmed_write(
        client,
        "PATCH",
        f"/api/tasks/{task_id}",
        {field: None, "expected_version": 1},
    )

    assert response.status_code == 422
    assert client.get("/api/tasks").json()["items"][0] == before


def test_task_patch_allows_nullable_fields_to_be_cleared(client: TestClient) -> None:
    task_id = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {
            "title": "可空字段",
            "description": "说明",
            "course": "数据库",
            "due_at": "2026-07-18T10:00:00Z",
            "reminder_at": "2026-07-18T09:00:00Z",
        },
    ).json()["record_id"]

    response = confirmed_write(
        client,
        "PATCH",
        f"/api/tasks/{task_id}",
        {
            "description": None,
            "course": None,
            "due_at": None,
            "reminder_at": None,
            "expected_version": 1,
        },
    )

    assert response.status_code == 200, response.text
    record = response.json()["record"]
    assert record["version"] == 2
    assert all(
        record[field] is None for field in ("description", "course", "due_at", "reminder_at")
    )


def test_task_merge_validation_error_is_domain_422_and_preserves_record(
    client: TestClient,
) -> None:
    task_id = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {
            "title": "合并校验",
            "due_at": "2026-07-18T10:00:00Z",
            "reminder_at": "2026-07-18T09:00:00Z",
        },
    ).json()["record_id"]
    before = client.get("/api/tasks").json()["items"][0]

    response = confirmed_write(
        client,
        "PATCH",
        f"/api/tasks/{task_id}",
        {"due_at": "2026-07-18T08:00:00Z", "expected_version": 1},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_action_payload"
    assert client.get("/api/tasks").json()["items"][0] == before


def test_stale_confirmed_task_update_cannot_overwrite_newer_version(
    client: TestClient,
) -> None:
    task_id = confirmed_write(client, "POST", "/api/tasks", {"title": "并发待办"}).json()[
        "record_id"
    ]
    prepare_request = {
        "action": "update_task",
        "target_title": "并发待办",
        "payload": {"title": "陈旧标题"},
        "idempotency_key": "stale-task-update-key",
    }
    prepared = client.post("/api/actions/prepare", json=prepare_request)
    assert prepared.status_code == 201, prepared.text
    pending = prepared.json()
    assert pending["target_id"] == task_id
    assert pending["payload"]["expected_version"] == 1
    confirm_action(client, pending["id"])

    concurrent = confirmed_write(
        client,
        "PATCH",
        f"/api/tasks/{task_id}",
        {"priority": "high", "expected_version": 1},
    )
    assert concurrent.status_code == 200, concurrent.text

    replay = client.post("/api/actions/prepare", json=prepare_request)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == pending["id"]
    assert replay.json()["payload"]["expected_version"] == 1

    stale = client.post(f"/api/actions/{pending['id']}/execute")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"
    record = client.get("/api/tasks").json()["items"][0]
    assert (record["title"], record["priority"], record["version"]) == (
        "并发待办",
        "high",
        2,
    )
