from fastapi.testclient import TestClient


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
        "asr_provider": "funasr",
        "asr_model": "paraformer-zh-streaming",
        "asr_device": "cuda",
    }
    unconfirmed = client.patch("/api/settings", json=payload)
    assert unconfirmed.status_code == 428
    assert client.get("/api/settings").json()["major"] is None

    updated = client.patch(
        "/api/settings",
        json=payload,
        headers={"X-User-Confirmed": "true"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["success"] is True
    assert all(body["verified_fields"].values())
    settings = body["settings"]
    assert settings["major"] == "人工智能"
    assert settings["current_courses"][0]["code"] == "AI201"
    assert settings["teacher_names"] == ["张老师", "李老师"]
    assert settings["asr_provider"] == "funasr"
    assert client.get("/api/settings").json() == settings


def test_settings_schema_rejects_unknown_invalid_and_empty_updates(client: TestClient) -> None:
    assert (
        client.patch(
            "/api/settings",
            json={},
            headers={"X-User-Confirmed": "true"},
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/settings",
            json={"timezone": "Mars/Olympus"},
            headers={"X-User-Confirmed": "true"},
        ).status_code
        == 422
    )
    unknown = client.patch(
        "/api/settings",
        json={"api_key": "must-not-be-accepted"},
        headers={"X-User-Confirmed": "true"},
    )
    assert unknown.status_code == 422
