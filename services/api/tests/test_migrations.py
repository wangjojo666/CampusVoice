import os
import sqlite3
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = API_ROOT / "alembic.ini"

V01_TABLES = {
    "action_logs",
    "calendar_events",
    "conversations",
    "correction_records",
    "courses",
    "document_chunks",
    "documents",
    "hotwords",
    "pending_actions",
    "tasks",
    "transcriptions",
    "undo_records",
    "user_settings",
    "users",
    "voice_sessions",
}
V02_TABLES = {"confirmation_nonces", "websocket_tickets"}
V03_TABLES = {"privacy_deletion_challenges"}
V04_TABLES = {"write_challenges"}


def _database_url(database_path: Path) -> str:
    return f"sqlite+aiosqlite:///{database_path.resolve().as_posix()}"


def _run_alembic(database_path: Path, *arguments: str) -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "CAMPUSVOICE_AUTH_MODE": "demo",
            "CAMPUSVOICE_DATABASE_AUTO_CREATE": "false",
            "CAMPUSVOICE_DATABASE_URL": _database_url(database_path),
            "CAMPUSVOICE_ENV": "test",
        }
    )
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *arguments],
        cwd=API_ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, (
        f"alembic {' '.join(arguments)} failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )


def _application_tables(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' "
            "AND name != 'alembic_version'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def _current_revision(database_path: Path) -> str | None:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return str(row[0]) if row is not None else None


def test_fresh_database_upgrade_check_downgrade_and_reupgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh-migrations.db"

    _run_alembic(database_path, "upgrade", "head")
    assert _application_tables(database_path) == V01_TABLES | V02_TABLES | V03_TABLES | V04_TABLES
    assert _current_revision(database_path) == "0004_write_challenges"
    _run_alembic(database_path, "check")

    _run_alembic(database_path, "downgrade", "base")
    assert _application_tables(database_path) == set()
    assert _current_revision(database_path) is None

    _run_alembic(database_path, "upgrade", "head")
    assert _application_tables(database_path) == V01_TABLES | V02_TABLES | V03_TABLES | V04_TABLES
    assert _current_revision(database_path) == "0004_write_challenges"


def test_database_at_public_v01_revision_receives_v02_v03_and_v04_tables(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "v01-upgrade.db"

    _run_alembic(database_path, "upgrade", "0001_initial_schema")
    assert _application_tables(database_path) == V01_TABLES
    assert _current_revision(database_path) == "0001_initial_schema"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO users "
            "(id, display_name, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("existing-user", "Existing User", 1, "2026-07-12", "2026-07-12"),
        )

    _run_alembic(database_path, "upgrade", "head")
    assert _application_tables(database_path) == V01_TABLES | V02_TABLES | V03_TABLES | V04_TABLES
    assert _current_revision(database_path) == "0004_write_challenges"

    with sqlite3.connect(database_path) as connection:
        existing_user_count = connection.execute(
            "SELECT count(*) FROM users WHERE id = ?",
            ("existing-user",),
        ).fetchone()
        confirmation_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(confirmation_nonces)")
        }
        ticket_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(websocket_tickets)")
        }
        privacy_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(privacy_deletion_challenges)")
        }
        write_challenge_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(write_challenges)")
        }

    assert existing_user_count == (1,)
    assert confirmation_columns == {
        "consumed_at",
        "expires_at",
        "nonce_hash",
        "payload_hash",
        "pending_action_id",
        "stage",
        "user_id",
    }
    assert ticket_columns == {
        "consumed_at",
        "created_at",
        "expires_at",
        "origin",
        "ticket_hash",
        "user_id",
    }
    assert privacy_columns == {
        "consumed_at",
        "created_at",
        "expires_at",
        "id",
        "nonce_hash",
        "scope",
        "user_id",
    }
    assert write_challenge_columns == {
        "body_hash",
        "consumed_at",
        "created_at",
        "expires_at",
        "flow_id",
        "method",
        "path",
        "required_stages",
        "stage",
        "token_hash",
        "user_id",
    }


def test_database_at_v02_receives_privacy_and_write_challenge_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "v02-upgrade.db"

    _run_alembic(database_path, "upgrade", "0002_security_tokens")
    assert _application_tables(database_path) == V01_TABLES | V02_TABLES
    assert _current_revision(database_path) == "0002_security_tokens"

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO users "
            "(id, display_name, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("v02-user", "V02 User", 1, "2026-07-12", "2026-07-12"),
        )

    _run_alembic(database_path, "upgrade", "head")

    assert _application_tables(database_path) == V01_TABLES | V02_TABLES | V03_TABLES | V04_TABLES
    assert _current_revision(database_path) == "0004_write_challenges"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM users WHERE id = ?", ("v02-user",)
        ).fetchone() == (1,)


def test_database_at_v03_receives_only_write_challenge_table(tmp_path: Path) -> None:
    database_path = tmp_path / "v03-upgrade.db"

    _run_alembic(database_path, "upgrade", "0003_privacy_controls")
    assert _application_tables(database_path) == V01_TABLES | V02_TABLES | V03_TABLES
    assert _current_revision(database_path) == "0003_privacy_controls"

    _run_alembic(database_path, "upgrade", "head")
    assert _application_tables(database_path) == V01_TABLES | V02_TABLES | V03_TABLES | V04_TABLES
    assert _current_revision(database_path) == "0004_write_challenges"
