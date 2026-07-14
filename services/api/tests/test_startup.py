import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import main as main_module
from app.core.config import Settings
from app.core.startup import validate_runtime_capabilities
from app.db.base import Base
from app.db.session import create_database_engine, create_session_factory
from app.models.entities import User, UserSettings


async def test_demo_user_bootstrap_handles_two_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'startup-race.db'}"
    settings = Settings(
        env="test",
        database_url=database_url,
        database_auto_create=False,
        demo_user_id="smoke_user",
    )
    first_engine = create_database_engine(database_url)
    second_engine = create_database_engine(database_url)
    first_factory = create_session_factory(first_engine)
    original_get = AsyncSession.get
    first_lookup = asyncio.Event()
    second_lookup = asyncio.Event()
    first_committed = asyncio.Event()
    lookup_count = 0
    first_task: asyncio.Task[None] | None = None
    second_task: asyncio.Task[None] | None = None

    async def coordinated_get(
        session: AsyncSession, entity: type[object], identifier: object, **kwargs: object
    ) -> object | None:
        nonlocal lookup_count
        result = await original_get(session, entity, identifier, **kwargs)
        if entity is User and identifier == settings.demo_user_id and result is None:
            lookup_count += 1
            if lookup_count == 1:
                first_lookup.set()
                await asyncio.wait_for(second_lookup.wait(), timeout=5)
            elif lookup_count == 2:
                second_lookup.set()
                await asyncio.wait_for(first_committed.wait(), timeout=5)
        return result

    try:
        async with first_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        first_app = main_module.create_app(settings, engine=first_engine, initialize_schema=False)
        second_app = main_module.create_app(settings, engine=second_engine, initialize_schema=False)
        monkeypatch.setattr(AsyncSession, "get", coordinated_get)

        async def run_first_worker() -> None:
            async with first_app.router.lifespan_context(first_app):
                first_committed.set()

        async def run_second_worker() -> None:
            async with second_app.router.lifespan_context(second_app):
                pass

        first_task = asyncio.create_task(run_first_worker())
        await asyncio.wait_for(first_lookup.wait(), timeout=5)
        second_task = asyncio.create_task(run_second_worker())
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=10)

        async with first_factory() as session:
            user_count = await session.scalar(
                select(func.count()).select_from(User).where(User.id == settings.demo_user_id)
            )
            settings_count = await session.scalar(
                select(func.count())
                .select_from(UserSettings)
                .where(UserSettings.user_id == settings.demo_user_id)
            )
            user_settings = await session.get(UserSettings, settings.demo_user_id)

        assert user_count == 1
        assert settings_count == 1
        assert lookup_count == 2
        assert user_settings is not None
        assert user_settings.timezone == settings.timezone
        assert user_settings.asr_model_config == {
            "provider": settings.asr_provider,
            "model": settings.asr_model,
            "device": settings.asr_device,
        }

        await main_module._ensure_demo_user(first_factory, settings)
        async with first_factory() as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(User).where(User.id == settings.demo_user_id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(UserSettings)
                    .where(UserSettings.user_id == settings.demo_user_id)
                )
                == 1
            )
    finally:
        tasks = [task for task in (first_task, second_task) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await first_engine.dispose()
        await second_engine.dispose()


async def test_demo_user_bootstrap_reraises_incomplete_integrity_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = IntegrityError("INSERT INTO users", {"id": "smoke_user"}, ValueError("invalid"))
    settings = Settings(env="test", demo_user_id="smoke_user")
    engine = create_database_engine("sqlite+aiosqlite:///:memory:")
    session_factory = create_session_factory(engine)

    async def fail_initialization(*_args: object) -> None:
        raise failure

    async def report_incomplete(*_args: object) -> bool:
        return False

    monkeypatch.setattr(main_module, "_initialize_demo_user", fail_initialization)
    monkeypatch.setattr(main_module, "_demo_user_is_initialized", report_incomplete)
    try:
        with pytest.raises(IntegrityError) as caught:
            await main_module._ensure_demo_user(session_factory, settings)
        assert caught.value is failure
    finally:
        await engine.dispose()


def test_core_defaults_are_dependency_safe_and_partial_llm_config_is_rejected() -> None:
    assert Settings(env="test").knowledge_retriever == "lexical"
    with pytest.raises(ValidationError, match="LLM base URL and model"):
        Settings(env="test", llm_base_url="https://llm.example/v1")


def test_disabled_ai_components_need_no_optional_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.startup.find_spec", lambda _name: None)

    validate_runtime_capabilities(
        Settings(env="test", asr_provider="disabled", knowledge_retriever="lexical")
    )


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("funasr", "FunASR"), ("whisper", "Whisper")],
)
def test_enabled_asr_fails_fast_when_optional_modules_are_missing(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    expected: str,
) -> None:
    monkeypatch.setattr("app.core.startup.find_spec", lambda _name: None)

    with pytest.raises(ValueError, match=expected):
        validate_runtime_capabilities(
            Settings(env="test", asr_provider=provider, asr_model="small")  # type: ignore[arg-type]
        )


def test_whisper_rejects_a_paraformer_model_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.startup.find_spec", lambda _name: object())

    with pytest.raises(ValueError, match="Whisper model name"):
        validate_runtime_capabilities(
            Settings(env="test", asr_provider="whisper", asr_model="paraformer-zh-streaming")
        )


def test_embedding_retrieval_fails_fast_without_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.startup.find_spec", lambda _name: None)

    with pytest.raises(ValueError, match="sentence-transformers"):
        validate_runtime_capabilities(
            Settings(env="test", asr_provider="disabled", knowledge_retriever="embedding")
        )
