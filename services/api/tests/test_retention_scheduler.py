import logging
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

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


class _FailingExecutor(RetentionExecutor):
    def __init__(self, settings: Settings, message: str) -> None:
        self._settings = settings
        self._message = message

    async def run_once(self) -> dict[str, dict[str, int]]:
        raise RuntimeError(self._message)

    async def _sleep(self, _delay: float) -> None:  # type: ignore[override]
        return None


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


def test_retention_job_failure_uses_safe_stderr_and_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_sentinel = "retention-cli-private-sentinel"

    async def fail() -> None:
        raise RuntimeError(private_sentinel)

    monkeypatch.setattr(retention_job, "_run", fail)

    with pytest.raises(SystemExit) as caught:
        retention_job.main()

    captured = capsys.readouterr()
    assert caught.value.code == 1
    assert captured.out == ""
    assert captured.err.strip() == "retention_job_failed"
    assert private_sentinel not in captured.err
    assert "Traceback" not in captured.err


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
async def test_retention_failure_logs_do_not_include_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_sentinel = "retention-log-private-sentinel"
    executor = _FailingExecutor(
        Settings(
            env="test",
            retention_scheduler_max_retries=1,
            retention_scheduler_retry_base_seconds=0.1,
        ),
        private_sentinel,
    )

    with (
        caplog.at_level(logging.WARNING, logger="campusvoice.retention"),
        pytest.raises(RuntimeError, match=private_sentinel),
    ):
        await executor.run_with_retries()

    records = [record for record in caplog.records if record.name == "campusvoice.retention"]
    assert [record.message for record in records] == [
        "retention_run_retry",
        "retention_run_failed",
    ]
    assert [record.exception_type for record in records] == ["RuntimeError", "RuntimeError"]
    assert all(record.exc_info is None for record in records)
    assert private_sentinel not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_database_engine_hides_bound_parameters_in_errors() -> None:
    private_sentinel = "retention-engine-private-sentinel"
    engine = create_database_engine("sqlite+aiosqlite:///:memory:")
    try:
        with pytest.raises(OperationalError) as caught:
            async with engine.connect() as connection:
                await connection.execute(
                    text(
                        "SELECT value FROM deliberately_missing_retention_table "
                        "WHERE user_id = :user_id"
                    ),
                    {"user_id": private_sentinel},
                )
        rendered = str(caught.value)
        assert private_sentinel not in rendered
        assert "parameters hidden" in rendered.lower()
    finally:
        await engine.dispose()


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
