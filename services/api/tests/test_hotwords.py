from fastapi.testclient import TestClient


def test_hotword_writes_require_confirmation_and_are_verified(client: TestClient) -> None:
    unconfirmed = client.post("/api/hotwords", json={"term": "Transformer", "category": "ai_term"})
    assert unconfirmed.status_code == 428
    assert client.get("/api/hotwords").json()["total"] == 0

    created = client.post(
        "/api/hotwords",
        json={"term": "Transformer", "category": "ai_term"},
        headers={"X-User-Confirmed": "true"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["success"] is True
    hotword_id = created.json()["record_id"]

    one_confirmation = client.delete(
        f"/api/hotwords/{hotword_id}", headers={"X-User-Confirmed": "true"}
    )
    assert one_confirmation.status_code == 428
    assert client.get("/api/hotwords").json()["total"] == 1

    deleted = client.delete(
        f"/api/hotwords/{hotword_id}",
        headers={
            "X-User-Confirmed": "true",
            "X-Second-Confirmation": "true",
        },
    )
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True
    assert deleted.json()["verified_fields"] == {"absent": True}
    assert client.get("/api/hotwords").json()["total"] == 0
