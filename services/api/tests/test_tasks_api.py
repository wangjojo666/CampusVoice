from fastapi.testclient import TestClient


def test_task_crud_requires_confirmation_and_verifies_database(client: TestClient) -> None:
    unconfirmed = client.post("/api/tasks", json={"title": "机器学习作业"})
    assert unconfirmed.status_code == 428
    assert unconfirmed.json()["error"]["code"] == "confirmation_required"
    assert client.get("/api/tasks").json()["total"] == 0

    created = client.post(
        "/api/tasks",
        json={"title": "机器学习作业", "due_at": "2026-07-18T18:00:00+08:00"},
        headers={"X-User-Confirmed": "true"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["success"] is True
    assert all(body["verified_fields"].values())
    assert body["record"]["due_at"] == "2026-07-18T10:00:00Z"
    task_id = body["record_id"]

    updated = client.patch(
        f"/api/tasks/{task_id}",
        json={"priority": "high", "expected_version": 1},
        headers={"X-User-Confirmed": "true"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["record"]["priority"] == "high"
    assert updated.json()["record"]["version"] == 2

    tasks = client.get("/api/tasks", params={"status": "pending"}).json()
    assert tasks["total"] == 1
    assert tasks["items"][0]["id"] == task_id


def test_duplicate_task_is_blocked_before_execution(client: TestClient) -> None:
    first = client.post(
        "/api/tasks",
        json={"title": "数据库复习", "course": "数据库"},
        headers={"X-User-Confirmed": "true"},
    )
    assert first.status_code == 201

    duplicate = client.post(
        "/api/tasks",
        json={"title": "数据库复习", "course": "数据库"},
        headers={"X-User-Confirmed": "true"},
    )
    assert duplicate.status_code == 428
    pending = duplicate.json()["error"]["details"]["pending_action"]
    assert pending["state"] == "needs_input"
    assert pending["blocking_reasons"] == ["duplicate_record"]
    assert client.get("/api/tasks").json()["total"] == 1


def test_delete_needs_two_unique_confirmations_and_can_be_undone(client: TestClient) -> None:
    task_id = client.post(
        "/api/tasks",
        json={"title": "待删除任务"},
        headers={"X-User-Confirmed": "true"},
    ).json()["record_id"]

    requested = client.delete(f"/api/tasks/{task_id}")
    assert requested.status_code == 428
    action = requested.json()["error"]["details"]["pending_action"]
    assert action["risk_level"] == "high"
    assert action["required_confirmations"] == 2
    action_id = action["id"]

    first = client.post(
        f"/api/actions/{action_id}/confirm",
        json={"confirmed": True, "confirmation_token": "delete-token-one"},
    )
    assert first.json()["state"] == "awaiting_second_confirmation"
    duplicate_click = client.post(
        f"/api/actions/{action_id}/confirm",
        json={"confirmed": True, "confirmation_token": "delete-token-one"},
    )
    assert duplicate_click.json()["confirmations_received"] == 1
    assert client.post(f"/api/actions/{action_id}/execute").status_code == 409

    second = client.post(
        f"/api/actions/{action_id}/confirm",
        json={"confirmed": True, "confirmation_token": "delete-token-two"},
    )
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
    task_id = client.post(
        "/api/tasks",
        json={"title": "版本检查"},
        headers={"X-User-Confirmed": "true"},
    ).json()["record_id"]
    response = client.patch(
        f"/api/tasks/{task_id}",
        json={"title": "不应保存", "expected_version": 99},
        headers={"X-User-Confirmed": "true"},
    )
    assert response.status_code == 409
    task = client.get("/api/tasks").json()["items"][0]
    assert task["title"] == "版本检查"
    assert task["version"] == 1
