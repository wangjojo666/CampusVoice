from fastapi.testclient import TestClient

from tests.helpers import confirm_action, confirmed_write


def _create_event(client: TestClient, title: str = "高等数学") -> str:
    payload = {
        "title": title,
        "start_at": "2026-07-18T09:00:00+08:00",
        "end_at": "2026-07-18T10:00:00+08:00",
        "location": "A302",
    }
    response = confirmed_write(client, "POST", "/api/events", payload)
    assert response.status_code == 201, response.text
    return response.json()["record_id"]


def test_conflict_and_duplicate_detection(client: TestClient) -> None:
    event_id = _create_event(client)
    conflict = client.post(
        "/api/events/check-conflict",
        json={
            "start_at": "2026-07-18T09:30:00+08:00",
            "end_at": "2026-07-18T10:30:00+08:00",
        },
    )
    assert conflict.status_code == 200
    assert conflict.json()["has_conflict"] is True
    assert conflict.json()["conflicts"][0]["id"] == event_id

    duplicate = client.post(
        "/api/actions/prepare",
        json={
            "action": "create_event",
            "payload": {
                "title": "高等数学",
                "start_at": "2026-07-18T09:00:00+08:00",
                "end_at": "2026-07-18T10:00:00+08:00",
                "location": "A302",
            },
        },
    )
    assert duplicate.status_code == 201
    body = duplicate.json()
    assert body["state"] == "needs_input"
    assert "duplicate_record" in body["blocking_reasons"]


def test_conflict_requires_explicit_override_and_two_confirmations(client: TestClient) -> None:
    _create_event(client, "已有课程")
    payload = {
        "title": "实验课",
        "start_at": "2026-07-18T09:30:00+08:00",
        "end_at": "2026-07-18T10:30:00+08:00",
    }
    blocked = client.post(
        "/api/actions/prepare", json={"action": "create_event", "payload": payload}
    ).json()
    assert blocked["state"] == "needs_input"
    assert "time_conflict_requires_override" in blocked["blocking_reasons"]

    overridden = client.post(
        "/api/actions/prepare",
        json={
            "action": "create_event",
            "payload": payload,
            "overwrite_existing": True,
        },
    ).json()
    assert overridden["risk_level"] == "high"
    assert overridden["required_confirmations"] == 2
    action_id = overridden["id"]
    for _ in range(2):
        confirm_action(client, action_id)
    executed = client.post(f"/api/actions/{action_id}/execute")
    assert executed.status_code == 200, executed.text
    assert executed.json()["success"] is True
    assert "time_conflict" in executed.json()["side_effects"]


def test_direct_conflict_override_returns_a_continuable_two_stage_action(
    client: TestClient,
) -> None:
    _create_event(client, "已有直写课程")
    response = confirmed_write(
        client,
        "POST",
        "/api/events",
        {
            "title": "直写冲突实验课",
            "start_at": "2026-07-18T09:30:00+08:00",
            "end_at": "2026-07-18T10:30:00+08:00",
            "allow_conflict": True,
        },
    )

    assert response.status_code == 428, response.text
    pending = response.json()["error"]["details"]["pending_action"]
    assert pending["state"] == "awaiting_confirmation"
    assert pending["risk_level"] == "high"
    assert pending["required_confirmations"] == 2
    for _ in range(2):
        confirm_action(client, pending["id"])
    executed = client.post(f"/api/actions/{pending['id']}/execute")
    assert executed.status_code == 200, executed.text
    assert executed.json()["success"] is True
    assert "time_conflict" in executed.json()["side_effects"]


def test_event_requires_timezone_and_valid_interval(client: TestClient) -> None:
    naive = confirmed_write(
        client,
        "POST",
        "/api/events",
        {"title": "无时区", "start_at": "2026-07-18T09:00:00"},
    )
    assert naive.status_code == 422
    backwards = confirmed_write(
        client,
        "POST",
        "/api/events",
        {
            "title": "错误区间",
            "start_at": "2026-07-18T10:00:00+08:00",
            "end_at": "2026-07-18T09:00:00+08:00",
        },
    )
    assert backwards.status_code == 422


def test_event_update_can_be_undone(client: TestClient) -> None:
    event_id = _create_event(client, "原日程")
    prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_event",
            "target_id": event_id,
            "payload": {"title": "新日程", "location": "B201"},
        },
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)
    executed = client.post(f"/api/actions/{action_id}/execute")
    assert executed.json()["success"] is True
    assert executed.json()["record"]["title"] == "新日程"

    undone = client.post(f"/api/actions/{action_id}/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["success"] is True
    restored = client.get("/api/events").json()["items"][0]
    assert (restored["title"], restored["location"]) == ("原日程", "A302")
