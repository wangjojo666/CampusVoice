import asyncio
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import session as session_module


def _sqlite_error(code: int) -> sqlite3.OperationalError:
    error = sqlite3.OperationalError("simulated SQLite failure")
    error.sqlite_errorcode = code
    return error


class _FakeCursor:
    def __init__(
        self,
        *,
        journal_mode: str = "delete",
        wal_result: str = "wal",
        wal_errors: list[sqlite3.OperationalError] | None = None,
    ) -> None:
        self.journal_mode = journal_mode
        self.wal_result = wal_result
        self.wal_errors = list(wal_errors or [])
        self.statements: list[str] = []
        self.closed = False
        self._row: tuple[object, ...] | None = None

    def execute(self, statement: str) -> object:
        self.statements.append(statement)
        if statement == "PRAGMA journal_mode":
            self._row = (self.journal_mode,)
        elif statement == "PRAGMA journal_mode=WAL":
            if self.wal_errors:
                raise self.wal_errors.pop(0)
            self.journal_mode = self.wal_result
            self._row = (self.journal_mode,)
        else:
            self._row = None
        return self

    def fetchone(self) -> tuple[object, ...] | None:
        return self._row

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor


async def _read_journal_mode(engine: AsyncEngine) -> str:
    async with engine.connect() as connection:
        return str(await connection.scalar(text("PRAGMA journal_mode")))


def test_sqlite_pragmas_set_timeout_before_foreign_keys_and_wal() -> None:
    cursor = _FakeCursor()

    session_module._set_sqlite_pragmas(_FakeConnection(cursor), object())

    assert cursor.statements == [
        "PRAGMA busy_timeout=5000",
        "PRAGMA foreign_keys=ON",
        "PRAGMA journal_mode",
        "PRAGMA journal_mode=WAL",
    ]
    assert cursor.closed


@pytest.mark.parametrize("journal_mode", ["wal", "memory"])
def test_sqlite_pragmas_keep_compatible_journal_mode(journal_mode: str) -> None:
    cursor = _FakeCursor(journal_mode=journal_mode)

    session_module._set_sqlite_pragmas(_FakeConnection(cursor), object())

    assert "PRAGMA journal_mode=WAL" not in cursor.statements
    assert cursor.closed


@pytest.mark.parametrize("error_code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED])
def test_sqlite_wal_retries_lock_errors(error_code: int, monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _FakeCursor(wal_errors=[_sqlite_error(error_code), _sqlite_error(error_code)])
    sleeps: list[float] = []
    monkeypatch.setattr(session_module.time, "sleep", sleeps.append)

    session_module._set_sqlite_pragmas(_FakeConnection(cursor), object())

    assert sleeps == [0.01, 0.02]
    assert cursor.statements.count("PRAGMA journal_mode=WAL") == 3
    assert cursor.closed


def test_sqlite_wal_reraises_non_lock_error(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _sqlite_error(sqlite3.SQLITE_IOERR)
    cursor = _FakeCursor(wal_errors=[error])
    monkeypatch.setattr(
        session_module.time,
        "sleep",
        lambda _seconds: pytest.fail("non-lock errors must not be retried"),
    )

    with pytest.raises(sqlite3.OperationalError) as caught:
        session_module._set_sqlite_pragmas(_FakeConnection(cursor), object())

    assert caught.value is error
    assert cursor.closed


def test_sqlite_wal_reraises_lock_error_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = _sqlite_error(sqlite3.SQLITE_BUSY)
    cursor = _FakeCursor(wal_errors=[error])
    monotonic_values = iter([100.0, 105.0])
    monkeypatch.setattr(session_module.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        session_module.time,
        "sleep",
        lambda _seconds: pytest.fail("an expired retry must not sleep"),
    )

    with pytest.raises(sqlite3.OperationalError) as caught:
        session_module._set_sqlite_pragmas(_FakeConnection(cursor), object())

    assert caught.value is error
    assert cursor.closed


def test_sqlite_wal_does_not_retry_after_sleep_exhausts_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = _sqlite_error(sqlite3.SQLITE_BUSY)
    cursor = _FakeCursor(wal_errors=[error])
    monotonic_values = iter([100.0, 104.995, 105.0])
    sleeps: list[float] = []
    monkeypatch.setattr(session_module.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(session_module.time, "sleep", sleeps.append)

    with pytest.raises(sqlite3.OperationalError) as caught:
        session_module._set_sqlite_pragmas(_FakeConnection(cursor), object())

    assert caught.value is error
    assert sleeps == pytest.approx([0.005])
    assert cursor.statements.count("PRAGMA journal_mode=WAL") == 1
    assert cursor.closed


def test_sqlite_wal_rejects_unexpected_journal_mode() -> None:
    cursor = _FakeCursor(wal_result="delete")

    with pytest.raises(RuntimeError, match="refused WAL journal mode"):
        session_module._set_sqlite_pragmas(_FakeConnection(cursor), object())

    assert cursor.closed


async def test_real_sqlite_concurrent_first_connections_enable_wal(tmp_path: Path) -> None:
    for attempt in range(8):
        database_path = tmp_path / f"wal-race-{attempt}.db"
        with sqlite3.connect(database_path) as connection:
            connection.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY)")
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)

        database_url = f"sqlite+aiosqlite:///{database_path}"
        engines = [
            session_module.create_database_engine(database_url),
            session_module.create_database_engine(database_url),
        ]
        try:
            journal_modes = await asyncio.gather(
                *(_read_journal_mode(engine) for engine in engines),
                return_exceptions=True,
            )
        finally:
            await asyncio.gather(*(engine.dispose() for engine in engines))

        assert journal_modes == ["wal", "wal"]


async def test_real_in_memory_sqlite_keeps_memory_journal_mode() -> None:
    engine = session_module.create_database_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.connect() as connection:
            journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
            busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))
            foreign_keys = await connection.scalar(text("PRAGMA foreign_keys"))
    finally:
        await engine.dispose()

    assert journal_mode == "memory"
    assert busy_timeout == 5000
    assert foreign_keys == 1
