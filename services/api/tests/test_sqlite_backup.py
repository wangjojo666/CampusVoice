import sqlite3
from pathlib import Path

import pytest

from app.jobs.sqlite_backup import create_backup, verify_database


def test_online_sqlite_backup_is_consistent_and_refuses_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "live.db"
    backup = tmp_path / "backup.db"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
        connection.execute("INSERT INTO records VALUES ('campusvoice')")

    result = create_backup(source, backup)

    assert result["integrity_check"] == "ok"
    assert len(result["sha256"]) == 64
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT value FROM records").fetchone() == ("campusvoice",)
    assert verify_database(backup)["sha256"] == result["sha256"]
    with pytest.raises(FileExistsError):
        create_backup(source, backup)
