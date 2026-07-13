from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.base import Base
from app.db.session import create_database_engine, create_session_factory
from app.db.types import utc_now
from app.jobs import retention as retention_job
from app.models.entities import OidcLoginTransaction, User
from app.services.privacy import scheduler as scheduler_module
from app.services.privacy.scheduler import RetentionExecutor


class _RetryingExecutor(RetentionExecutor):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sleep_delays: list[float] = []
        self.calls = 0

    async def run_once(self) -> dict[str, dict[str, int]]:
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("transient database failure")
        return {"user": {"transcriptions": 2}}

    async def _sleep(self, delay: float) -> None:  # type: ignore[override]
        self._sleep_delays.append(delay)


@pytest.mark.asyncio
async def test_retention_job_emits_summary_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = Settings(env="test")
    factory = object()

    class _EngineStub:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    engine = _EngineStub()

    class _ExecutorStub:
        def __init__(self, supplied_factory: object, supplied_settings: Settings) -> None:
            assert supplied_factory is factory
            assert supplied_settings is settings

        async def run_with_retries(self) -> dict[str, dict[str, int]]:
            return {"user_demo": {"transcriptions": 2}}

    monkeypatch.setattr(retention_job, "get_settings", lambda: settings)
    monkeypatch.setattr(retention_job, "create_database_engine", lambda _url: engine)
    monkeypatch.setattr(retention_job, "create_session_factory", lambda _engine: factory)
    monkeypatch.setattr(retention_job, "RetentionExecutor", _ExecutorStub)

    await retention_job._run()

    assert engine.disposed is True
    assert capsys.readouterr().out.strip() == (
        '{"deleted_counts": {"transcriptions": 2}, "users_processed": 1}'
    )


@pytest.mark.asyncio
async def test_retention_executor_retries_with_bounded_exponential_backoff() -> None:
    settings = Settings(
        env="test",
        retention_scheduler_max_retries=3,
        retention_scheduler_retry_base_seconds=0.1,
    )
    executor = _RetryingExecutor(settings)
    result = await executor.run_with_retries()
    assert result == {"user": {"transcriptions": 2}}
    assert executor.calls == 3
    assert executor._sleep_delays == [0.1, 0.2]


@pytest.mark.asyncio
async def test_retention_executor_covers_inactive_users_and_expired_oidc_flows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        env="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'retention.db'}",
        database_auto_create=False,
    )
    engine = create_database_engine(settings.database_url)
    factory = create_session_factory(engine)
    now = utc_now()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session, session.begin():
        session.add_all(
            [
                User(id="active", display_name="Active", is_active=True),
                User(id="inactive", display_name="Inactive", is_active=False),
                OidcLoginTransaction(
                    flow_hash="a" * 64,
                    state_hash="b" * 64,
                    nonce="expired",
                    code_verifier="v" * 64,
                    expires_at=now - timedelta(seconds=1),
                    created_at=now - timedelta(minutes=5),
                ),
                OidcLoginTransaction(
                    flow_hash="c" * 64,
                    state_hash="d" * 64,
                    nonce="live",
                    code_verifier="w" * 64,
                    expires_at=now + timedelta(minutes=5),
                    created_at=now,
                ),
            ]
        )

    processed: list[str] = []

    class _PrivacyServiceStub:
        def __init__(self, *_args: object) -> None:
            pass

        async def run_retention(self, user_id: str) -> SimpleNamespace:
            processed.append(user_id)
            return SimpleNamespace(deleted_counts={"expired": 0})

    monkeypatch.setattr(scheduler_module, "PrivacyService", _PrivacyServiceStub)
    try:
        results = await RetentionExecutor(factory, settings).run_once()
        assert list(results) == ["active", "inactive"]
        assert processed == ["active", "inactive"]
        async with factory() as session:
            remaining = list(
                await session.scalars(
                    select(OidcLoginTransaction).order_by(OidcLoginTransaction.flow_hash)
                )
            )
        assert [item.nonce for item in remaining] == ["live"]
    finally:
        await engine.dispose()
