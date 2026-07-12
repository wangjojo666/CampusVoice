from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.routes.intent import get_intent_parser
from app.core.config import Settings
from app.main import create_app
from app.services.intent import IntentParser


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Run every acceptance scenario against its own real SQLite-backed app."""

    database_path = tmp_path / "campusvoice-e2e.db"
    settings = Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        database_auto_create=True,
        action_ttl_minutes=30,
        undo_ttl_minutes=1_440,
        asr_provider="disabled",
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
    )
    app = create_app(settings)

    # TEST-ONLY DETERMINISM: exercise the production IntentParser's built-in
    # rule adapter and prevent CI from making an external LLM network request.
    deterministic_parser = IntentParser(timezone_name=settings.timezone)
    app.dependency_overrides[get_intent_parser] = lambda: deterministic_parser

    with TestClient(app) as test_client:
        yield test_client
