import sqlite3
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol, cast

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_SQLITE_LOCK_ERROR_CODES = {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
_SQLITE_WAL_RETRY_DEADLINE_SECONDS = 5.0
_SQLITE_WAL_RETRY_INITIAL_SECONDS = 0.01
_SQLITE_WAL_RETRY_MAX_SECONDS = 0.25


class _SQLiteCursor(Protocol):
    def execute(self, statement: str) -> object: ...

    def fetchone(self) -> tuple[object, ...] | None: ...

    def close(self) -> None: ...


class _SQLiteConnection(Protocol):
    def cursor(self) -> _SQLiteCursor: ...


def _ensure_sqlite_parent(database_url: str) -> None:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite") or not url.database or url.database == ":memory:":
        return
    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def _read_sqlite_journal_mode(cursor: _SQLiteCursor) -> str:
    row = cursor.fetchone()
    if not row:
        raise RuntimeError("SQLite did not report its journal mode")
    return str(row[0]).casefold()


def _is_sqlite_lock_error(error: sqlite3.OperationalError) -> bool:
    error_code = getattr(error, "sqlite_errorcode", None)
    return isinstance(error_code, int) and (error_code & 0xFF) in _SQLITE_LOCK_ERROR_CODES


def _ensure_sqlite_wal(cursor: _SQLiteCursor) -> None:
    deadline = time.monotonic() + _SQLITE_WAL_RETRY_DEADLINE_SECONDS
    retry_delay = _SQLITE_WAL_RETRY_INITIAL_SECONDS

    while True:
        try:
            cursor.execute("PRAGMA journal_mode")
            journal_mode = _read_sqlite_journal_mode(cursor)
            if journal_mode in {"wal", "memory"}:
                return

            cursor.execute("PRAGMA journal_mode=WAL")
            journal_mode = _read_sqlite_journal_mode(cursor)
            if journal_mode != "wal":
                raise RuntimeError(f"SQLite refused WAL journal mode and returned {journal_mode!r}")
            return
        except sqlite3.OperationalError as error:
            if not _is_sqlite_lock_error(error):
                raise

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(retry_delay, remaining))
            if time.monotonic() >= deadline:
                raise
            retry_delay = min(retry_delay * 2, _SQLITE_WAL_RETRY_MAX_SECONDS)


def _set_sqlite_pragmas(dbapi_connection: object, _connection_record: object) -> None:
    connection = cast(_SQLiteConnection, dbapi_connection)
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        _ensure_sqlite_wal(cursor)
    finally:
        cursor.close()


def create_database_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    _ensure_sqlite_parent(database_url)
    engine = create_async_engine(
        database_url,
        echo=echo,
        hide_parameters=True,
        pool_pre_ping=True,
    )

    if make_url(database_url).drivername.startswith("sqlite"):
        event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)


async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
