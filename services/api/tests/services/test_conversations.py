from fastapi.testclient import TestClient


def test_intent_clarification_is_persisted_and_reused(client: TestClient) -> None:
    first = client.post(
        "/api/intent/parse",
        json={"text": "创建日程：项目答辩"},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["missing_fields"] == ["date", "start_time"]
    assert first_body["conversation_id"].startswith("cnv_")

    second = client.post(
        "/api/intent/parse",
        json={
            "text": "明天下午三点",
            "conversation_id": first_body["conversation_id"],
        },
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["conversation_id"] == first_body["conversation_id"]
    assert second_body["intent"] == "create_event"
    assert second_body["slots"]["title"] == "项目答辩"
    assert second_body["slots"]["start_time"] == "15:00"
    assert second_body["missing_fields"] == []


def test_conversation_ids_are_user_scoped_and_not_client_invented(client: TestClient) -> None:
    response = client.post(
        "/api/intent/parse",
        json={"text": "明天下午三点", "conversation_id": "cnv_not_owned"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
