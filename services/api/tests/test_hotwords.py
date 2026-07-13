from fastapi.testclient import TestClient

from tests.helpers import confirmed_write


def test_hotword_writes_require_confirmation_and_are_verified(client: TestClient) -> None:
    unconfirmed = client.post("/api/hotwords", json={"term": "Transformer", "category": "ai_term"})
    assert unconfirmed.status_code == 428
    assert client.get("/api/hotwords").json()["total"] == 0

    payload = {"term": "Transformer", "category": "ai_term"}
    created = confirmed_write(
        client,
        "POST",
        "/api/hotwords",
        payload,
    )
    assert created.status_code == 201, created.text
    assert created.json()["success"] is True
    hotword_id = created.json()["record_id"]

    path = f"/api/hotwords/{hotword_id}"
    issued = client.post(
        "/api/auth/write-challenges",
        json={"method": "DELETE", "path": path, "body": None},
    )
    assert issued.status_code == 200, issued.text
    assert issued.json()["required_stages"] == 2
    assert issued.json()["stage"] == 1

    first_stage_cannot_delete = client.delete(
        path,
        headers={"X-Write-Challenge": issued.json()["challenge"]},
    )
    assert first_stage_cannot_delete.status_code == 409
    assert client.get("/api/hotwords").json()["total"] == 1

    advanced = client.post(
        "/api/auth/write-challenges/advance",
        json={"challenge": issued.json()["challenge"]},
    )
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["stage"] == 2
    assert (
        client.post(
            "/api/auth/write-challenges/advance",
            json={"challenge": issued.json()["challenge"]},
        ).status_code
        == 409
    )

    deleted = client.delete(
        path,
        headers={"X-Write-Challenge": advanced.json()["challenge"]},
    )
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True
    assert deleted.json()["verified_fields"] == {"absent": True}
    assert client.get("/api/hotwords").json()["total"] == 0
