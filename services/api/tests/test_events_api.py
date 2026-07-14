import pytest
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


def test_event_create_rejects_explicit_null_end_without_writing(client: TestClient) -> None:
    omitted = confirmed_write(
        client,
        "POST",
        "/api/events",
        {
            "title": "省略结束时间",
            "start_at": "2026-07-18T09:00:00+08:00",
        },
    )
    assert omitted.status_code == 201, omitted.text
    assert omitted.json()["record"]["end_at"] == "2026-07-18T02:00:00Z"
    before = client.get("/api/events").json()

    response = confirmed_write(
        client,
        "POST",
        "/api/events",
        {
            "title": "显式空结束时间",
            "start_at": "2026-07-18T09:00:00+08:00",
            "end_at": None,
        },
    )

    assert response.status_code == 422
    assert client.get("/api/events").json() == before


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


def test_event_patch_requires_expected_version_without_modifying_record(
    client: TestClient,
) -> None:
    event_id = _create_event(client, "版本必填日程")
    before = client.get("/api/events").json()["items"][0]

    response = confirmed_write(
        client,
        "PATCH",
        f"/api/events/{event_id}",
        {"location": "不应保存"},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "expected_version"]
    assert response.json()["detail"][0]["type"] == "missing"
    assert client.get("/api/events").json()["items"][0] == before


@pytest.mark.parametrize(
    "field",
    ["title", "start_at", "end_at", "reminder_minutes", "source_type"],
)
def test_event_patch_rejects_null_required_fields_without_changing_version(
    client: TestClient,
    field: str,
) -> None:
    event_id = _create_event(client, "空值保护日程")
    before = client.get("/api/events").json()["items"][0]

    response = confirmed_write(
        client,
        "PATCH",
        f"/api/events/{event_id}",
        {field: None, "expected_version": 1},
    )

    assert response.status_code == 422
    assert client.get("/api/events").json()["items"][0] == before


def test_event_patch_allows_nullable_fields_to_be_cleared(client: TestClient) -> None:
    event_id = _create_event(client, "可空日程字段")

    response = confirmed_write(
        client,
        "PATCH",
        f"/api/events/{event_id}",
        {
            "description": None,
            "course": None,
            "location": None,
            "expected_version": 1,
        },
    )

    assert response.status_code == 200, response.text
    record = response.json()["record"]
    assert record["version"] == 2
    assert all(record[field] is None for field in ("description", "course", "location"))


def test_event_merge_validation_error_is_domain_422_and_preserves_record(
    client: TestClient,
) -> None:
    event_id = _create_event(client, "合并校验日程")
    before = client.get("/api/events").json()["items"][0]

    response = confirmed_write(
        client,
        "PATCH",
        f"/api/events/{event_id}",
        {"start_at": "2026-07-18T11:00:00+08:00", "expected_version": 1},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_action_payload"
    assert client.get("/api/events").json()["items"][0] == before


def test_stale_confirmed_event_update_cannot_overwrite_newer_version(
    client: TestClient,
) -> None:
    event_id = _create_event(client, "并发日程")
    prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_event",
            "target_id": event_id,
            "payload": {"location": "陈旧地点"},
        },
    )
    assert prepared.status_code == 201, prepared.text
    pending = prepared.json()
    assert pending["payload"]["expected_version"] == 1
    confirm_action(client, pending["id"])

    concurrent = confirmed_write(
        client,
        "PATCH",
        f"/api/events/{event_id}",
        {"location": "最新地点", "expected_version": 1},
    )
    assert concurrent.status_code == 200, concurrent.text

    stale = client.post(f"/api/actions/{pending['id']}/execute")
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"
    record = client.get("/api/events").json()["items"][0]
    assert (record["location"], record["version"]) == ("最新地点", 2)


def test_default_reminder_applies_to_new_page_and_voice_events_only(
    client: TestClient,
) -> None:
    existing_id = _create_event(client, "旧默认提醒")
    updated_settings = confirmed_write(
        client,
        "PATCH",
        "/api/settings",
        {"default_reminder_minutes": 45},
    )
    assert updated_settings.status_code == 200, updated_settings.text

    page_created = confirmed_write(
        client,
        "POST",
        "/api/events",
        {
            "title": "页面默认提醒",
            "start_at": "2026-07-19T09:00:00+08:00",
            "end_at": "2026-07-19T10:00:00+08:00",
        },
    )
    assert page_created.status_code == 201, page_created.text
    assert page_created.json()["record"]["reminder_minutes"] == 45

    voice_prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "create_event",
            "payload": {
                "title": "语音默认提醒",
                "start_at": "2026-07-20T09:00:00+08:00",
                "end_at": "2026-07-20T10:00:00+08:00",
            },
            "source_text": "后天上午九点创建语音默认提醒",
        },
    )
    assert voice_prepared.status_code == 201, voice_prepared.text
    pending = voice_prepared.json()
    assert pending["payload"]["reminder_minutes"] == 45
    confirm_action(client, pending["id"])
    voice_created = client.post(f"/api/actions/{pending['id']}/execute")
    assert voice_created.status_code == 200, voice_created.text
    assert voice_created.json()["record"]["reminder_minutes"] == 45

    records = {item["id"]: item for item in client.get("/api/events").json()["items"]}
    assert records[existing_id]["reminder_minutes"] == 30
