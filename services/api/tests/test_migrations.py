import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = API_ROOT / "alembic.ini"
HEAD_REVISION = "0008_notice_current_and_receipt_repair"

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


def _alembic_process(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "CAMPUSVOICE_AUTH_MODE": "demo",
            "CAMPUSVOICE_DATABASE_AUTO_CREATE": "false",
            "CAMPUSVOICE_DATABASE_URL": database_url,
            "CAMPUSVOICE_ENV": "test",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *arguments],
        cwd=API_ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )


def _run_alembic_url(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    completed = _alembic_process(database_url, *arguments)
    assert completed.returncode == 0, (
        f"alembic {' '.join(arguments)} failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    return completed


def _run_alembic(database_path: Path, *arguments: str) -> None:
    _run_alembic_url(_database_url(database_path), *arguments)


def _application_tables(database_path: Path) -> set[str]:
    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' "
            "AND name != 'alembic_version'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def _current_revision(database_path: Path) -> str | None:
    with closing(sqlite3.connect(database_path)) as connection:
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


def test_alembic_accepts_percent_encoded_database_url(tmp_path: Path) -> None:
    database_path = tmp_path / "percent%40encoded.db"

    _run_alembic(database_path, "upgrade", "0001_initial_schema", "--sql")

    assert not database_path.exists()


def test_offline_postgresql_upgrade_renders_through_head() -> None:
    completed = _run_alembic_url(
        "postgresql://offline:offline@127.0.0.1:1/offline",
        "upgrade",
        "head",
        "--sql",
    )

    assert f"version_num='{HEAD_REVISION}'" in completed.stdout
    assert "CREATE UNIQUE INDEX uq_documents_series_current" in completed.stdout
    assert completed.stdout.count("UPDATE impact_migration_plans") == 2
    assert completed.stdout.count("UPDATE impact_migration_items") == 2
    assert "SELECT impact_migration_plans.id" not in completed.stdout
    assert "SELECT impact_migration_items.id" not in completed.stdout
    expected_targets = {
        "execute_receipt_json": "execute",
        "undo_receipt_json": "undo",
        "execute_verification_json": "execute",
        "undo_verification_json": "undo",
    }
    for target, operation in expected_targets.items():
        assignment = f"SET {target} = verification_json"
        assert completed.stdout.count(assignment) == 1
        predicate = completed.stdout.split(assignment, maxsplit=1)[1].split(";", maxsplit=1)[0]
        assert f"verification_json ->> 'operation' = '{operation}'" in predicate
        assert f"CASE WHEN json_typeof({target}) = 'object'" in predicate
        assert f"THEN {target} ELSE CAST('{{}}' AS json) END" in predicate
        assert "CASE WHEN json_typeof(verification_json) = 'object'" in predicate
        assert "json_typeof(verification_json -> 'verified') = 'boolean'" in predicate
    assert "CAST(execute_receipt_json AS jsonb)" not in completed.stdout
    assert " AS numeric)" not in completed.stdout
    assert completed.stdout.rstrip().endswith("COMMIT;")


def test_offline_v08_rejects_unsupported_dialect_before_revision_sql() -> None:
    completed = _alembic_process(
        "mysql://offline:offline@127.0.0.1:1/offline",
        "upgrade",
        "0007_notice_migration_safety:head",
        "--sql",
    )

    assert completed.returncode != 0
    assert "offline SQL supports only SQLite and PostgreSQL" in completed.stderr
    assert "WITH ranked AS" not in completed.stdout
    assert "uq_documents_series_current" not in completed.stdout
    assert "UPDATE impact_migration_plans" not in completed.stdout


def test_offline_sqlite_v08_repairs_receipts_and_current_heads(tmp_path: Path) -> None:
    database_path = tmp_path / "offline-v08.db"
    render_path = tmp_path / "render-only.db"
    _run_alembic(database_path, "upgrade", "0007_notice_migration_safety")
    verified_at = "2026-07-15T01:02:03+00:00"
    plan_execute = {
        "operation": "execute",
        "verified": True,
        "verified_count": 1,
        "total_count": 1,
        "verified_at": verified_at,
        "status": "verified",
    }
    plan_undo = plan_execute | {"operation": "undo", "status": "undone"}
    plan_large_count = plan_execute | {
        "verified_count": 9_223_372_036_854_775_808,
        "total_count": 9_223_372_036_854_775_809,
    }
    item_execute = {
        "operation": "execute",
        "verified": True,
        "verified_at": verified_at,
        "expected_snapshot": {"version": 1},
        "database_snapshot": {"version": 2},
    }
    item_undo = item_execute | {"operation": "undo"}
    invalid_plan_receipts: dict[str, object] = {
        "negative": plan_execute | {"verified_count": -1},
        "over-count": plan_execute | {"verified_count": 2},
        "float-count": plan_execute | {"verified_count": 1.0},
        "large-over-count": plan_execute
        | {
            "verified_count": 9_223_372_036_854_775_809,
            "total_count": 9_223_372_036_854_775_808,
        },
        "wrong-type": plan_execute | {"verified": "true"},
        "invalid-time": plan_execute | {"verified_at": "2026-02-30T01:02:03+00:00"},
        "ambiguous": {"operation": "execute", "verified": True},
        "duplicate-operation": (
            '{"operation":"execute","operation":"undo","verified":true,'
            '"verified_count":1,"total_count":1,"verified_at":'
            f'"{verified_at}","status":"verified"}}'
        ),
        "duplicate-count": (
            '{"operation":"execute","verified":true,"verified_count":1,'
            '"verified_count":2,"total_count":1,"verified_at":'
            f'"{verified_at}","status":"verified"}}'
        ),
        "malformed": "{not-json",
    }
    invalid_item_receipts: dict[str, object] = {
        "array-snapshot": item_execute | {"expected_snapshot": []},
        "wrong-type": item_execute | {"verified": 1},
        "invalid-time": item_execute | {"verified_at": "2026-07-15T25:02:03+00:00"},
        "ambiguous": {"operation": "execute", "verified": True},
        "duplicate-operation": (
            '{"operation":"execute","operation":"undo","verified":true,'
            f'"verified_at":"{verified_at}","expected_snapshot":{{}},'
            '"database_snapshot":{}}'
        ),
        "malformed": "{not-json",
    }

    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.executemany(
            "INSERT INTO documents "
            "(id, user_id, title, file_type, storage_path, content_sha256, status, "
            "series_id, revision_number, is_current, ingest_source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "doc-offline-old",
                    "user-offline",
                    "Old",
                    "txt",
                    "inline://old",
                    "a" * 64,
                    "ready",
                    "series-offline",
                    1,
                    1,
                    "test",
                    verified_at,
                    verified_at,
                ),
                (
                    "doc-offline-new",
                    "user-offline",
                    "New",
                    "txt",
                    "inline://new",
                    "b" * 64,
                    "ready",
                    "series-offline",
                    2,
                    1,
                    "test",
                    verified_at,
                    verified_at,
                ),
            ],
        )
        plan_rows = [
            ("plan-offline-execute", plan_execute, {}, {}),
            ("plan-offline-undo", plan_undo, {}, {}),
            ("plan-offline-large-count", plan_large_count, {}, {}),
            (
                "plan-offline-occupied",
                plan_execute,
                {"operation": "execute", "marker": "keep"},
                {},
            ),
            (
                "plan-offline-undo-occupied",
                plan_undo,
                {},
                {"operation": "undo", "marker": "keep"},
            ),
            ("plan-offline-malformed-target", plan_execute, {}, {}),
        ]
        plan_rows.extend(
            (f"plan-offline-{suffix}", receipt, {}, {})
            for suffix, receipt in invalid_plan_receipts.items()
        )
        connection.executemany(
            "INSERT INTO impact_migration_plans "
            "(id, user_id, change_set_id, status, risk_level, conflicts_json, "
            "verification_json, execute_receipt_json, undo_receipt_json, generation, "
            "version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    plan_id,
                    "user-offline",
                    f"change-{plan_id}",
                    "verified",
                    "low",
                    "[]",
                    receipt if isinstance(receipt, str) else json.dumps(receipt),
                    json.dumps(execute_target),
                    json.dumps(undo_target),
                    1,
                    1,
                    verified_at,
                    verified_at,
                )
                for plan_id, receipt, execute_target, undo_target in plan_rows
            ],
        )
        item_rows = [
            ("item-offline-execute", item_execute, {}, {}),
            ("item-offline-undo", item_undo, {}, {}),
            (
                "item-offline-occupied",
                item_execute,
                {"operation": "execute", "marker": "keep"},
                {},
            ),
            (
                "item-offline-undo-occupied",
                item_undo,
                {},
                {"operation": "undo", "marker": "keep"},
            ),
            ("item-offline-malformed-target", item_execute, {}, {}),
        ]
        item_rows.extend(
            (f"item-offline-{suffix}", receipt, {}, {})
            for suffix, receipt in invalid_item_receipts.items()
        )
        connection.executemany(
            "INSERT INTO impact_migration_items "
            "(id, plan_id, user_id, entity_type, entity_id, expected_version, "
            "before_snapshot, proposed_patch, source_claim_ids, verification_json, "
            "execute_verification_json, undo_verification_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item_id,
                    "plan-offline-execute",
                    "user-offline",
                    "task",
                    f"task-{item_id}",
                    1,
                    "{}",
                    "{}",
                    "[]",
                    receipt if isinstance(receipt, str) else json.dumps(receipt),
                    json.dumps(execute_target),
                    json.dumps(undo_target),
                    verified_at,
                )
                for item_id, receipt, execute_target, undo_target in item_rows
            ],
        )
        connection.execute(
            "UPDATE impact_migration_plans SET execute_receipt_json = ? WHERE id = ?",
            ("{not-json", "plan-offline-malformed-target"),
        )
        connection.execute(
            "UPDATE impact_migration_items SET execute_verification_json = ? WHERE id = ?",
            ("{not-json", "item-offline-malformed-target"),
        )

    completed = _run_alembic_url(
        _database_url(render_path),
        "upgrade",
        "0007_notice_migration_safety:head",
        "--sql",
    )

    assert not render_path.exists()
    assert "SELECT impact_migration_plans.id" not in completed.stdout
    assert "SELECT impact_migration_items.id" not in completed.stdout
    assert f"version_num='{HEAD_REVISION}'" in completed.stdout
    with closing(sqlite3.connect(database_path)) as connection:
        connection.executescript(completed.stdout)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            HEAD_REVISION,
        )
        assert connection.execute(
            "SELECT id FROM documents WHERE series_id = ? AND is_current = 1",
            ("series-offline",),
        ).fetchall() == [("doc-offline-new",)]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE documents SET is_current = 1 WHERE id = ?",
                ("doc-offline-old",),
            )
        connection.rollback()

        def decode_json(raw: str) -> object:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw

        plan_receipts = {
            str(plan_id): (decode_json(execute_raw), decode_json(undo_raw))
            for plan_id, execute_raw, undo_raw in connection.execute(
                "SELECT id, execute_receipt_json, undo_receipt_json FROM impact_migration_plans"
            )
        }
        assert plan_receipts["plan-offline-execute"] == (plan_execute, {})
        assert plan_receipts["plan-offline-undo"] == ({}, plan_undo)
        assert plan_receipts["plan-offline-large-count"] == (plan_large_count, {})
        assert plan_receipts["plan-offline-occupied"] == (
            {"operation": "execute", "marker": "keep"},
            {},
        )
        assert plan_receipts["plan-offline-undo-occupied"] == (
            {},
            {"operation": "undo", "marker": "keep"},
        )
        assert plan_receipts["plan-offline-malformed-target"] == ("{not-json", {})
        assert all(
            plan_receipts[f"plan-offline-{suffix}"] == ({}, {}) for suffix in invalid_plan_receipts
        )

        item_receipts = {
            str(item_id): (decode_json(execute_raw), decode_json(undo_raw))
            for item_id, execute_raw, undo_raw in connection.execute(
                "SELECT id, execute_verification_json, undo_verification_json "
                "FROM impact_migration_items"
            )
        }
        assert item_receipts["item-offline-execute"] == (item_execute, {})
        assert item_receipts["item-offline-undo"] == ({}, item_undo)
        assert item_receipts["item-offline-occupied"] == (
            {"operation": "execute", "marker": "keep"},
            {},
        )
        assert item_receipts["item-offline-undo-occupied"] == (
            {},
            {"operation": "undo", "marker": "keep"},
        )
        assert item_receipts["item-offline-malformed-target"] == ("{not-json", {})
        assert all(
            item_receipts[f"item-offline-{suffix}"] == ({}, {}) for suffix in invalid_item_receipts
        )


def test_v08_repairs_current_heads_and_unambiguous_legacy_receipts(tmp_path: Path) -> None:
    database_path = tmp_path / "v08-repair.db"
    _run_alembic(database_path, "upgrade", "0007_notice_migration_safety")
    verified_at = "2026-07-15T01:02:03+00:00"
    plan_execute = {
        "operation": "execute",
        "verified": True,
        "verified_count": 1,
        "total_count": 1,
        "verified_at": verified_at,
        "status": "verified",
    }
    plan_undo = plan_execute | {"operation": "undo", "status": "undone"}
    item_execute = {
        "operation": "execute",
        "verified": True,
        "verified_at": verified_at,
        "expected_snapshot": {"version": 2},
        "database_snapshot": {"version": 2},
    }
    item_undo = item_execute | {
        "operation": "undo",
        "expected_snapshot": {"version": 1},
        "database_snapshot": {"version": 3},
    }

    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute(
            "INSERT INTO users (id, display_name, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("user-v08", "V08", 1, verified_at, verified_at),
        )
        connection.execute(
            "INSERT INTO notice_series "
            "(id, user_id, canonical_key, normalized_title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("series-v08", "user-v08", "v08", "v08", verified_at, verified_at),
        )
        for revision, document_id, created_at in (
            (1, "doc-v08-a", "2026-07-15T00:00:00"),
            (2, "doc-v08-b", "2026-07-15T00:01:00"),
            (3, "doc-v08-c", "2026-07-15T00:02:00"),
            (None, "doc-v08-null", "2026-07-15T00:03:00"),
        ):
            connection.execute(
                "INSERT INTO documents "
                "(id, user_id, title, file_type, storage_path, content_sha256, status, "
                "series_id, revision_number, is_current, ingest_source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    "user-v08",
                    document_id,
                    "txt",
                    f"inline://{document_id}",
                    document_id.ljust(64, "0"),
                    "ready",
                    "series-v08",
                    revision,
                    1,
                    "test",
                    created_at,
                    created_at,
                ),
            )
        for suffix, legacy, execute_target in (
            ("execute", plan_execute, {}),
            ("undo", plan_undo, {}),
            ("occupied", plan_execute, {"operation": "execute", "marker": "keep"}),
            ("ambiguous", {"verified": True}, {}),
        ):
            connection.execute(
                "INSERT INTO impact_migration_plans "
                "(id, user_id, change_set_id, status, risk_level, conflicts_json, "
                "verification_json, execute_receipt_json, undo_receipt_json, version, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"plan-v08-{suffix}",
                    "user-v08",
                    f"change-v08-{suffix}",
                    "verified",
                    "low",
                    "[]",
                    json.dumps(legacy),
                    json.dumps(execute_target),
                    "{}",
                    1,
                    verified_at,
                    verified_at,
                ),
            )
        for suffix, plan_id, legacy in (
            ("execute", "plan-v08-execute", item_execute),
            ("undo", "plan-v08-undo", item_undo),
            ("ambiguous", "plan-v08-ambiguous", {"verified": True}),
            ("occupied", "plan-v08-occupied", item_execute),
        ):
            connection.execute(
                "INSERT INTO impact_migration_items "
                "(id, plan_id, user_id, entity_type, entity_id, expected_version, "
                "before_snapshot, proposed_patch, source_claim_ids, verification_json, "
                "execute_verification_json, undo_verification_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"item-v08-{suffix}",
                    plan_id,
                    "user-v08",
                    "task",
                    f"task-v08-{suffix}",
                    1,
                    "{}",
                    "{}",
                    "[]",
                    json.dumps(legacy),
                    json.dumps({"operation": "execute", "marker": "keep"})
                    if suffix == "occupied"
                    else "{}",
                    "{}",
                    verified_at,
                ),
            )
        connection.commit()

    _run_alembic(database_path, "upgrade", "head")
    with closing(sqlite3.connect(database_path)) as connection, connection:
        assert connection.execute(
            "SELECT id FROM documents WHERE series_id = ? AND is_current = 1",
            ("series-v08",),
        ).fetchall() == [("doc-v08-c",)]
        assert ("series_id",) in _unique_column_sets(connection, "documents")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE documents SET is_current = 1 WHERE id = ?", ("doc-v08-b",))
        connection.rollback()

        assert (
            json.loads(
                connection.execute(
                    "SELECT execute_receipt_json FROM impact_migration_plans WHERE id = ?",
                    ("plan-v08-execute",),
                ).fetchone()[0]
            )
            == plan_execute
        )
        assert (
            json.loads(
                connection.execute(
                    "SELECT undo_receipt_json FROM impact_migration_plans WHERE id = ?",
                    ("plan-v08-undo",),
                ).fetchone()[0]
            )
            == plan_undo
        )
        assert json.loads(
            connection.execute(
                "SELECT execute_receipt_json FROM impact_migration_plans WHERE id = ?",
                ("plan-v08-occupied",),
            ).fetchone()[0]
        ) == {"operation": "execute", "marker": "keep"}
        assert connection.execute(
            "SELECT execute_receipt_json, undo_receipt_json "
            "FROM impact_migration_plans WHERE id = ?",
            ("plan-v08-ambiguous",),
        ).fetchone() == ("{}", "{}")
        assert (
            json.loads(
                connection.execute(
                    "SELECT execute_verification_json FROM impact_migration_items WHERE id = ?",
                    ("item-v08-execute",),
                ).fetchone()[0]
            )
            == item_execute
        )
        assert (
            json.loads(
                connection.execute(
                    "SELECT undo_verification_json FROM impact_migration_items WHERE id = ?",
                    ("item-v08-undo",),
                ).fetchone()[0]
            )
            == item_undo
        )
        assert json.loads(
            connection.execute(
                "SELECT execute_verification_json FROM impact_migration_items WHERE id = ?",
                ("item-v08-occupied",),
            ).fetchone()[0]
        ) == {"operation": "execute", "marker": "keep"}

    _run_alembic(database_path, "downgrade", "0007_notice_migration_safety")
    with closing(sqlite3.connect(database_path)) as connection:
        assert (
            json.loads(
                connection.execute(
                    "SELECT execute_receipt_json FROM impact_migration_plans WHERE id = ?",
                    ("plan-v08-execute",),
                ).fetchone()[0]
            )
            == plan_execute
        )

    _run_alembic(database_path, "upgrade", "head")


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

    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute(
            "INSERT INTO users "
            "(id, display_name, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("existing-user", "Existing User", 1, "2026-07-12", "2026-07-12"),
        )

    _run_alembic(database_path, "upgrade", "head")
    assert _application_tables(database_path) == ALL_TABLES
    assert _current_revision(database_path) == HEAD_REVISION

    with closing(sqlite3.connect(database_path)) as connection:
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

    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute(
            "INSERT INTO users "
            "(id, display_name, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("v02-user", "V02 User", 1, "2026-07-12", "2026-07-12"),
        )

    _run_alembic(database_path, "upgrade", "head")

    assert _application_tables(database_path) == ALL_TABLES
    assert _current_revision(database_path) == HEAD_REVISION
    with closing(sqlite3.connect(database_path)) as connection:
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

    with closing(sqlite3.connect(database_path)) as connection, connection:
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

    with closing(sqlite3.connect(database_path)) as connection, connection:
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

    with closing(sqlite3.connect(database_path)) as connection:
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
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute(
            "SELECT generation, execute_receipt_json, undo_receipt_json "
            "FROM impact_migration_plans WHERE id = ?",
            ("second-generation",),
        ).fetchone() == (1, "{}", "{}")
        assert connection.execute(
            "SELECT recommended_action, requires_manual_review FROM impact_cases WHERE id = ?",
            ("impact-before-v07",),
        ).fetchone() == ("manual_review", 1)
