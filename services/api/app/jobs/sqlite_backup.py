import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


def verify_database(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"SQLite database does not exist: {resolved}")
    uri = f"file:{resolved.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result != ("ok",):
        raise RuntimeError(f"SQLite integrity check failed: {result!r}")
    digest = hashlib.sha256()
    with resolved.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": digest.hexdigest(),
        "integrity_check": "ok",
    }


def create_backup(source: Path, destination: Path) -> dict[str, Any]:
    source_path = source.resolve()
    destination_path = destination.resolve()
    if source_path == destination_path:
        raise ValueError("backup source and destination must differ")
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {source_path}")
    if destination_path.exists():
        raise FileExistsError(f"backup destination already exists: {destination_path}")
    if not destination_path.parent.is_dir():
        raise FileNotFoundError(
            f"backup destination directory does not exist: {destination_path.parent}"
        )

    source_uri = f"file:{source_path.as_posix()}?mode=ro"
    with (
        sqlite3.connect(source_uri, uri=True) as source_connection,
        sqlite3.connect(destination_path) as destination_connection,
    ):
        source_connection.backup(destination_connection)
        destination_connection.commit()
    return verify_database(destination_path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or verify a consistent CampusVoice SQLite backup."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create a new online SQLite backup")
    create.add_argument("source", type=Path)
    create.add_argument("destination", type=Path)
    verify = commands.add_parser("verify", help="Verify integrity and checksum of a backup")
    verify.add_argument("path", type=Path)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    if arguments.command == "create":
        result = create_backup(arguments.source, arguments.destination)
    else:
        result = verify_database(arguments.path)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))


if __name__ == "__main__":
    main()
