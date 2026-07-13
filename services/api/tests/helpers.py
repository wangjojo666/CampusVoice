from fastapi.testclient import TestClient
from httpx import Response


def confirm_action(client: TestClient, action_id: str) -> object:
    issued = client.post(f"/api/actions/{action_id}/challenge")
    assert issued.status_code == 200, issued.text
    assert issued.headers["Cache-Control"] == "no-store"
    assert issued.headers["Pragma"] == "no-cache"
    response = client.post(
        f"/api/actions/{action_id}/confirm",
        json={"confirmed": True, "challenge": issued.json()["challenge"]},
    )
    assert response.status_code == 200, response.text
    return response


def write_challenge_headers(
    client: TestClient,
    method: str,
    path: str,
    body: object,
    *,
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    request_headers = dict(headers or {})
    issued = client.post(
        "/api/auth/write-challenges",
        headers=request_headers,
        json={"method": method.upper(), "path": path, "body": body},
    )
    assert issued.status_code == 200, issued.text
    return request_headers | {"X-Write-Challenge": issued.json()["challenge"]}


def confirmed_write(
    client: TestClient,
    method: str,
    path: str,
    body: object,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    request_headers = write_challenge_headers(
        client,
        method,
        path,
        body,
        headers=headers,
    )
    return client.request(
        method,
        path,
        headers=request_headers,
        json=body,
    )
