import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from app.jobs import sqlite_backup
from app.jobs.sqlite_backup import create_backup, verify_database


def _create_source(path: Path, value: str = "campusvoice") -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES (?)", (value,))


def test_online_sqlite_backup_is_consistent_and_refuses_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    backup = tmp_path / "backup.db"
    _create_source(source)

    result = create_backup(source, backup)

    assert result["integrity_check"] == "ok"
    assert len(result["sha256"]) == 64
    with closing(sqlite3.connect(backup)) as connection:
        assert connection.execute("SELECT value FROM records").fetchone() == ("campusvoice",)
    assert verify_database(backup)["sha256"] == result["sha256"]
    with pytest.raises(FileExistsError):
        create_backup(source, backup)


def test_verify_database_rejects_foreign_key_violations(tmp_path: Path) -> None:
    database = tmp_path / "orphaned.db"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("CREATE TABLE parents (id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE children (parent_id INTEGER NOT NULL REFERENCES parents(id))"
        )
        connection.execute("INSERT INTO children VALUES (1)")

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is not None

    with pytest.raises(RuntimeError, match="foreign key check failed"):
        verify_database(database)

    backup = tmp_path / "orphaned-backup.db"
    with pytest.raises(RuntimeError, match="foreign key check failed"):
        create_backup(database, backup)
    assert not backup.exists()


def test_failed_backup_removes_only_its_reservation_and_can_retry(tmp_path: Path) -> None:
    source = tmp_path / "corrupt.db"
    destination = tmp_path / "retry.db"
    source.write_bytes(b"this is not a sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        create_backup(source, destination)
    assert not destination.exists()

    source.unlink()
    _create_source(source, "repaired")
    assert create_backup(source, destination)["integrity_check"] == "ok"
    with closing(sqlite3.connect(destination)) as connection:
        assert connection.execute("SELECT value FROM records").fetchone() == ("repaired",)


def test_existing_backup_target_is_never_modified(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "existing.db"
    _create_source(source)
    sentinel = b"already belongs to another backup"
    destination.write_bytes(sentinel)

    with pytest.raises(FileExistsError):
        create_backup(source, destination)
    assert destination.read_bytes() == sentinel


def test_concurrent_backup_to_same_target_has_exactly_one_winner(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "shared.db"
    _create_source(source)
    barrier = Barrier(2)

    def attempt() -> dict[str, Any] | BaseException:
        barrier.wait()
        try:
            return create_backup(source, destination)
        except BaseException as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: attempt(), range(2)))

    successes = [result for result in results if isinstance(result, dict)]
    failures = [result for result in results if isinstance(result, BaseException)]
    assert len(successes) == len(failures) == 1
    assert isinstance(failures[0], FileExistsError)
    assert verify_database(destination)["integrity_check"] == "ok"


def test_verify_database_closes_connection_on_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "tracked.db"
    path.write_bytes(b"digest input")

    class Result:
        def __init__(self, value: tuple[Any, ...] | None) -> None:
            self._value = value

        def fetchone(self) -> tuple[Any, ...] | None:
            return self._value

    class Connection:
        def __init__(
            self,
            integrity_result: tuple[Any, ...],
            foreign_key_result: tuple[Any, ...] | None = None,
        ) -> None:
            self.results = {
                "PRAGMA integrity_check": integrity_result,
                "PRAGMA foreign_key_check": foreign_key_result,
            }
            self.statements: list[str] = []
            self.closed = False

        def execute(self, statement: str) -> Result:
            self.statements.append(statement)
            return Result(self.results[statement])

        def close(self) -> None:
            self.closed = True

    successful = Connection(("ok",))
    monkeypatch.setattr(sqlite_backup.sqlite3, "connect", lambda *_args, **_kwargs: successful)
    assert verify_database(path)["integrity_check"] == "ok"
    assert successful.statements == [
        "PRAGMA integrity_check",
        "PRAGMA foreign_key_check",
    ]
    assert successful.closed is True

    failed = Connection(("not ok", 1))
    monkeypatch.setattr(sqlite_backup.sqlite3, "connect", lambda *_args, **_kwargs: failed)
    with pytest.raises(RuntimeError, match="integrity check failed"):
        verify_database(path)
    assert failed.statements == ["PRAGMA integrity_check"]
    assert failed.closed is True

    foreign_key_failed = Connection(("ok",), ("children", 1, "parents", 0))
    monkeypatch.setattr(
        sqlite_backup.sqlite3,
        "connect",
        lambda *_args, **_kwargs: foreign_key_failed,
    )
    with pytest.raises(RuntimeError, match="foreign key check failed") as caught:
        verify_database(path)
    assert "children" not in str(caught.value)
    assert foreign_key_failed.closed is True


def test_cleanup_error_does_not_replace_original_backup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "corrupt.db"
    destination = tmp_path / "cleanup-fails.db"
    source.write_bytes(b"not sqlite")
    original_unlink = Path.unlink

    def fail_destination_cleanup(path: Path, *, missing_ok: bool = False) -> None:
        if path == destination:
            raise PermissionError("simulated cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_destination_cleanup)
    with pytest.raises(sqlite3.DatabaseError) as caught:
        create_backup(source, destination)
    assert not isinstance(caught.value, PermissionError)
