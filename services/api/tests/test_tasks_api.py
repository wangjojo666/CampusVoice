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
