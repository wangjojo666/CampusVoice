from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from tests.helpers import confirmed_write


def test_settings_get_and_verified_patch(client: TestClient) -> None:
    initial = client.get("/api/settings")
    assert initial.status_code == 200
    assert initial.json()["timezone"] == "Asia/Shanghai"
    assert initial.json()["asr_device"] == "cpu"

    payload = {
        "major": "人工智能",
        "grade": "2024",
        "current_courses": [
            {
                "code": "AI201",
                "name": "机器学习",
                "teacher": "张老师",
            }
        ],
        "teacher_names": ["张老师", "张老师", "李老师"],
        "default_reminder_minutes": 1_440,
        "timezone": "Asia/Shanghai",
    }
    unconfirmed = client.patch("/api/settings", json=payload)
    assert unconfirmed.status_code == 428
    assert client.get("/api/settings").json()["major"] is None

    updated = confirmed_write(client, "PATCH", "/api/settings", payload)
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["success"] is True
    assert all(body["verified_fields"].values())
    settings = body["settings"]
    assert settings["major"] == "人工智能"
    assert settings["current_courses"][0]["code"] == "AI201"
    assert settings["teacher_names"] == ["张老师", "李老师"]
    assert settings["asr_provider"] == "disabled"
    assert client.get("/api/settings").json() == settings


def test_settings_schema_rejects_unknown_invalid_and_empty_updates(client: TestClient) -> None:
    assert confirmed_write(client, "PATCH", "/api/settings", {}).status_code == 422
    assert (
        confirmed_write(
            client,
            "PATCH",
            "/api/settings",
            {"timezone": "Mars/Olympus"},
        ).status_code
        == 422
    )
    unknown = confirmed_write(
        client,
        "PATCH",
        "/api/settings",
        {"api_key": "must-not-be-accepted"},
    )
    assert unknown.status_code == 422


def test_settings_exposes_server_asr_status_and_rejects_user_overrides(
    client: TestClient,
) -> None:
    before = client.get("/api/settings").json()

    for field, value in (
        ("asr_provider", "funasr"),
        ("asr_model", "user-selected-model"),
        ("asr_device", "cuda"),
    ):
        response = confirmed_write(client, "PATCH", "/api/settings", {field: value})
        assert response.status_code == 422
        assert client.get("/api/settings").json() == before

    client.app.state.settings = client.app.state.settings.model_copy(  # type: ignore[attr-defined]
        update={"asr_model": "server-model", "asr_device": "server-device"}
    )
    runtime_view = client.get("/api/settings")
    assert runtime_view.status_code == 200
    assert runtime_view.json()["asr_model"] == "server-model"
    assert runtime_view.json()["asr_device"] == "server-device"


def test_intent_parse_uses_saved_user_timezone(client: TestClient) -> None:
    updated = confirmed_write(
        client,
        "PATCH",
        "/api/settings",
        {"timezone": "America/Los_Angeles"},
    )
    assert updated.status_code == 200, updated.text

    instant = datetime(2026, 7, 13, 1, 0, tzinfo=ZoneInfo("UTC"))
    with patch("app.services.intent.parser.datetime", wraps=datetime) as mocked_datetime:
        mocked_datetime.now.side_effect = lambda timezone=None: (
            instant.astimezone(timezone) if timezone is not None else instant
        )
        parsed = client.post(
            "/api/intent/parse",
            json={"text": "创建日程：项目组会，明天下午三点"},
        )

    assert parsed.status_code == 200, parsed.text
    assert parsed.json()["slots"]["date"] == "2026-07-13"
    assert parsed.json()["slots"]["start_time"] == "15:00"
