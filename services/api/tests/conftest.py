from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.helpers import confirm_action


@pytest.fixture
def client(tmp_path: object) -> Iterator[TestClient]:
    database_path = tmp_path / "campusvoice-test.db"  # type: ignore[operator]
    settings = Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{database_path}",
        database_auto_create=True,
        action_ttl_minutes=30,
        undo_ttl_minutes=1_440,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def prepare_and_confirm_once(
    client: TestClient, action: str, payload: dict[str, object], target_id: str | None = None
) -> str:
    request: dict[str, object] = {"action": action, "payload": payload}
    if target_id is not None:
        request["target_id"] = target_id
    prepared = client.post("/api/actions/prepare", json=request)
    assert prepared.status_code == 201, prepared.text
    action_id = prepared.json()["id"]
    confirm_action(client, action_id)
    return action_id
