import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = API_ROOT / "alembic.ini"
HEAD_REVISION = "0007_notice_migration_safety"

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
V05_TABLES = {"oidc_login_transactions", "oidc_sessions"}
V06_TABLES = {
    "notice_series",
    "notice_claims",
    "notice_change_sets",
    "notice_change_items",
    "impact_cases",
    "impact_migration_plans",
    "impact_migration_items",
}
ALL_TABLES = V01_TABLES | V02_TABLES | V03_TABLES | V04_TABLES | V05_TABLES | V06_TABLES


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


def _column_metadata(connection: sqlite3.Connection, table: str) -> dict[str, tuple[object, ...]]:
    return {str(row[1]): row for row in connection.execute(f"PRAGMA table_info({table})")}


def _index_column_sets(
    connection: sqlite3.Connection,
    table: str,
    *,
    unique: bool | None = None,
) -> set[tuple[str, ...]]:
    columns: set[tuple[str, ...]] = set()
    for index in connection.execute(f"PRAGMA index_list({table})"):
        if unique is not None and bool(index[2]) is not unique:
            continue
        name = str(index[1]).replace("'", "''")
        columns.add(
            tuple(str(row[2]) for row in connection.execute(f"PRAGMA index_info('{name}')"))
        )
    return columns


def _unique_column_sets(connection: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    return _index_column_sets(connection, table, unique=True)


def test_fresh_database_upgrade_check_downgrade_and_reupgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh-migrations.db"

    _run_alembic(database_path, "upgrade", "head")
    assert _application_tables(database_path) == ALL_TABLES
    assert _current_revision(database_path) == HEAD_REVISION
    _run_alembic(database_path, "check")

    _run_alembic(database_path, "downgrade", "base")
    assert _application_tables(database_path) == set()
    assert _current_revision(database_path) is None

    _run_alembic(database_path, "upgrade", "head")
    assert _application_tables(database_path) == ALL_TABLES
    assert _current_revision(database_path) == HEAD_REVISION


def test_database_at_public_v01_revision_receives_all_later_tables(
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
    assert _application_tables(database_path) == ALL_TABLES
    assert _current_revision(database_path) == HEAD_REVISION

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


def test_database_at_v02_receives_all_later_tables(tmp_path: Path) -> None:
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

    assert _application_tables(database_path) == ALL_TABLES
    assert _current_revision(database_path) == HEAD_REVISION
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM users WHERE id = ?", ("v02-user",)
        ).fetchone() == (1,)


def test_database_at_v03_receives_write_challenge_and_oidc_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "v03-upgrade.db"

    _run_alembic(database_path, "upgrade", "0003_privacy_controls")
    assert _application_tables(database_path) == V01_TABLES | V02_TABLES | V03_TABLES
    assert _current_revision(database_path) == "0003_privacy_controls"

    _run_alembic(database_path, "upgrade", "head")
    assert _application_tables(database_path) == ALL_TABLES
    assert _current_revision(database_path) == HEAD_REVISION


def test_database_at_v04_receives_only_oidc_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "v04-upgrade.db"

    _run_alembic(database_path, "upgrade", "0004_write_challenges")
    assert _application_tables(database_path) == ALL_TABLES - V05_TABLES - V06_TABLES
    assert _current_revision(database_path) == "0004_write_challenges"

    _run_alembic(database_path, "upgrade", "head")
    assert _application_tables(database_path) == ALL_TABLES
    assert _current_revision(database_path) == HEAD_REVISION


def test_v07_backfills_safety_columns_and_enforces_generation_and_undo_keys(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "v07-safety-upgrade.db"
    _run_alembic(database_path, "upgrade", "0006_notice_impact_migrations")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO impact_cases "
            "(id, user_id, change_item_id, entity_type, entity_id, entity_version, reason, "
            "severity, current_snapshot, proposed_patch, status, detected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "impact-before-v07",
                "user-v07",
                "change-before-v07",
                "task",
                "task-before-v07",
                1,
                "existing impact",
                "medium",
                "{}",
                "{}",
                "open",
                "2026-07-13T00:00:00",
            ),
        )
        connection.execute(
            "INSERT INTO impact_migration_plans "
            "(id, user_id, change_set_id, status, risk_level, conflicts_json, "
            "verification_json, version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "plan-before-v07",
                "user-v07",
                "change-set-before-v07",
                "ready",
                "low",
                "[]",
                "{}",
                1,
                "2026-07-13T00:00:00",
                "2026-07-13T00:00:00",
            ),
        )
        connection.execute(
            "INSERT INTO impact_migration_items "
            "(id, plan_id, user_id, entity_type, entity_id, expected_version, "
            "before_snapshot, proposed_patch, source_claim_ids, verification_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "item-before-v07",
                "plan-before-v07",
                "user-v07",
                "task",
                "task-before-v07",
                1,
                "{}",
                "{}",
                "[]",
                "{}",
                "2026-07-13T00:00:00",
            ),
        )
        connection.commit()

    _run_alembic(database_path, "upgrade", "head")
    assert _current_revision(database_path) == HEAD_REVISION

    with sqlite3.connect(database_path) as connection:
        impact_columns = _column_metadata(connection, "impact_cases")
        plan_columns = _column_metadata(connection, "impact_migration_plans")
        item_columns = _column_metadata(connection, "impact_migration_items")
        assert impact_columns.keys() >= {"recommended_action", "requires_manual_review"}
        assert plan_columns.keys() >= {
            "generation",
            "execute_receipt_json",
            "undo_receipt_json",
        }
        assert item_columns.keys() >= {
            "execute_verification_json",
            "undo_verification_json",
        }
        for columns, names in (
            (impact_columns, ("recommended_action", "requires_manual_review")),
            (
                plan_columns,
                ("generation", "execute_receipt_json", "undo_receipt_json"),
            ),
            (
                item_columns,
                ("execute_verification_json", "undo_verification_json"),
            ),
        ):
            assert all(int(columns[name][3]) == 1 for name in names)

        assert connection.execute(
            "SELECT recommended_action, requires_manual_review FROM impact_cases WHERE id = ?",
            ("impact-before-v07",),
        ).fetchone() == ("manual_review", 1)
        assert connection.execute(
            "SELECT generation, execute_receipt_json, undo_receipt_json "
            "FROM impact_migration_plans WHERE id = ?",
            ("plan-before-v07",),
        ).fetchone() == (1, "{}", "{}")
        assert connection.execute(
            "SELECT execute_verification_json, undo_verification_json "
            "FROM impact_migration_items WHERE id = ?",
            ("item-before-v07",),
        ).fetchone() == ("{}", "{}")

        connection.execute(
            "UPDATE impact_migration_plans SET execute_receipt_json = ? WHERE id = ?",
            ('{"operation":"execute"}', "plan-before-v07"),
        )
        assert connection.execute(
            "SELECT execute_receipt_json, undo_receipt_json "
            "FROM impact_migration_plans WHERE id = ?",
            ("plan-before-v07",),
        ).fetchone() == ('{"operation":"execute"}', "{}")
        connection.execute(
            "UPDATE impact_migration_plans SET undo_receipt_json = ? WHERE id = ?",
            ('{"operation":"undo"}', "plan-before-v07"),
        )
        assert connection.execute(
            "SELECT execute_receipt_json, undo_receipt_json "
            "FROM impact_migration_plans WHERE id = ?",
            ("plan-before-v07",),
        ).fetchone() == ('{"operation":"execute"}', '{"operation":"undo"}')

        connection.execute(
            "UPDATE impact_migration_items SET execute_verification_json = ? WHERE id = ?",
            ('{"verified":true}', "item-before-v07"),
        )
        assert connection.execute(
            "SELECT execute_verification_json, undo_verification_json "
            "FROM impact_migration_items WHERE id = ?",
            ("item-before-v07",),
        ).fetchone() == ('{"verified":true}', "{}")
        connection.execute(
            "UPDATE impact_migration_items SET undo_verification_json = ? WHERE id = ?",
            ('{"verified":false}', "item-before-v07"),
        )
        assert connection.execute(
            "SELECT execute_verification_json, undo_verification_json "
            "FROM impact_migration_items WHERE id = ?",
            ("item-before-v07",),
        ).fetchone() == ('{"verified":true}', '{"verified":false}')
        connection.commit()

        unique_plans = _unique_column_sets(connection, "impact_migration_plans")
        assert ("user_id", "change_set_id", "generation") in unique_plans
        assert ("user_id", "execution_idempotency_key") in unique_plans
        assert ("user_id", "undo_idempotency_key") in unique_plans
        assert ("user_id", "change_set_id") not in unique_plans
        assert ("user_id", "status") in _index_column_sets(connection, "impact_cases", unique=False)
        assert ("migration_plan_id",) in _index_column_sets(
            connection, "impact_cases", unique=False
        )
        assert ("user_id", "status") in _index_column_sets(
            connection, "impact_migration_plans", unique=False
        )
        assert ("plan_id",) in _index_column_sets(
            connection, "impact_migration_items", unique=False
        )

        connection.execute(
            "INSERT INTO impact_cases "
            "(id, user_id, change_item_id, entity_type, entity_id, entity_version, reason, "
            "severity, current_snapshot, proposed_patch, status, detected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "impact-after-v07",
                "user-v07",
                "change-after-v07",
                "task",
                "task-after-v07",
                1,
                "new impact",
                "low",
                "{}",
                '{"title":"updated"}',
                "open",
                "2026-07-13T01:00:00",
            ),
        )
        assert connection.execute(
            "SELECT recommended_action, requires_manual_review FROM impact_cases WHERE id = ?",
            ("impact-after-v07",),
        ).fetchone() == ("apply", 0)
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE impact_cases SET recommended_action = ? WHERE id = ?",
                ("unsafe", "impact-after-v07"),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE impact_cases "
                "SET recommended_action = ?, requires_manual_review = ? WHERE id = ?",
                ("manual_review", 0, "impact-after-v07"),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO impact_migration_plans "
                "(id, user_id, change_set_id, status, risk_level, conflicts_json, "
                "verification_json, version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "duplicate-generation",
                    "user-v07",
                    "change-set-before-v07",
                    "ready",
                    "low",
                    "[]",
                    "{}",
                    1,
                    "2026-07-13T00:00:00",
                    "2026-07-13T00:00:00",
                ),
            )
        connection.rollback()

        connection.execute(
            "INSERT INTO impact_migration_plans "
            "(id, user_id, change_set_id, generation, status, risk_level, conflicts_json, "
            "verification_json, version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "second-generation",
                "user-v07",
                "change-set-before-v07",
                2,
                "ready",
                "low",
                "[]",
                "{}",
                1,
                "2026-07-13T00:00:00",
                "2026-07-13T00:00:00",
            ),
        )
        connection.execute(
            "INSERT INTO impact_migration_items "
            "(id, plan_id, user_id, entity_type, entity_id, expected_version, "
            "before_snapshot, proposed_patch, source_claim_ids, verification_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "item-second-generation",
                "second-generation",
                "user-v07",
                "task",
                "task-before-v07",
                2,
                "{}",
                "{}",
                "[]",
                "{}",
                "2026-07-13T00:00:00",
            ),
        )
        connection.execute(
            "UPDATE impact_migration_plans "
            "SET execution_idempotency_key = ?, undo_idempotency_key = ? WHERE id = ?",
            ("shared-execution-key", "shared-undo-key", "plan-before-v07"),
        )
        connection.execute(
            "UPDATE impact_cases SET migration_plan_id = ? WHERE id = ?",
            ("plan-before-v07", "impact-before-v07"),
        )
        connection.commit()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO impact_migration_plans "
                "(id, user_id, change_set_id, status, risk_level, conflicts_json, "
                "verification_json, undo_idempotency_key, version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "duplicate-undo-key",
                    "user-v07",
                    "another-change-set",
                    "ready",
                    "low",
                    "[]",
                    "{}",
                    "shared-undo-key",
                    1,
                    "2026-07-13T00:00:00",
                    "2026-07-13T00:00:00",
                ),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO impact_migration_plans "
                "(id, user_id, change_set_id, status, risk_level, conflicts_json, "
                "verification_json, execution_idempotency_key, version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "duplicate-execution-key",
                    "user-v07",
                    "yet-another-change-set",
                    "ready",
                    "low",
                    "[]",
                    "{}",
                    "shared-execution-key",
                    1,
                    "2026-07-13T00:00:00",
                    "2026-07-13T00:00:00",
                ),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE impact_migration_plans SET version = 0 WHERE id = ?",
                ("second-generation",),
            )
        connection.rollback()

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE impact_migration_plans SET generation = 0 WHERE id = ?",
                ("second-generation",),
            )
        connection.rollback()

    _run_alembic(database_path, "downgrade", "0006_notice_impact_migrations")
    assert _current_revision(database_path) == "0006_notice_impact_migrations"

    with sqlite3.connect(database_path) as connection:
        assert (
            _column_metadata(connection, "impact_cases")
            .keys()
            .isdisjoint({"recommended_action", "requires_manual_review"})
        )
        assert (
            _column_metadata(connection, "impact_migration_plans")
            .keys()
            .isdisjoint({"generation", "execute_receipt_json", "undo_receipt_json"})
        )
        assert (
            _column_metadata(connection, "impact_migration_items")
            .keys()
            .isdisjoint({"execute_verification_json", "undo_verification_json"})
        )
        downgraded_uniques = _unique_column_sets(connection, "impact_migration_plans")
        assert ("user_id", "change_set_id") in downgraded_uniques
        assert ("user_id", "execution_idempotency_key") in downgraded_uniques
        assert ("user_id", "undo_idempotency_key") not in downgraded_uniques
        assert connection.execute(
            "SELECT id FROM impact_migration_plans WHERE user_id = ? AND change_set_id = ?",
            ("user-v07", "change-set-before-v07"),
        ).fetchall() == [("second-generation",)]
        assert connection.execute(
            "SELECT id FROM impact_migration_items ORDER BY id"
        ).fetchall() == [("item-second-generation",)]
        assert connection.execute(
            "SELECT migration_plan_id FROM impact_cases WHERE id = ?",
            ("impact-before-v07",),
        ).fetchone() == ("second-generation",)

    _run_alembic(database_path, "upgrade", "head")
    assert _current_revision(database_path) == HEAD_REVISION
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT generation, execute_receipt_json, undo_receipt_json "
            "FROM impact_migration_plans WHERE id = ?",
            ("second-generation",),
        ).fetchone() == (1, "{}", "{}")
        assert connection.execute(
            "SELECT recommended_action, requires_manual_review FROM impact_cases WHERE id = ?",
            ("impact-before-v07",),
        ).fetchone() == ("manual_review", 1)
