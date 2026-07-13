"""Fail CI when the action or verification service drops below its coverage floor."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

CRITICAL_FILES = (
    "app/services/actions/service.py",
    "app/services/verification/service.py",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "report",
        nargs="?",
        type=Path,
        default=Path("coverage.json"),
        help="coverage.py JSON report (default: coverage.json)",
    )
    parser.add_argument("--minimum", type=float, default=70.0)
    return parser.parse_args()


def _normalise(path: str) -> str:
    return path.replace("\\", "/")


def main() -> int:
    arguments = _arguments()
    report: dict[str, Any] = json.loads(arguments.report.read_text(encoding="utf-8"))
    files: dict[str, Any] = report.get("files", {})
    normalised = {_normalise(name): details for name, details in files.items()}
    failed = False

    for target in CRITICAL_FILES:
        matches = [details for name, details in normalised.items() if name.endswith(target)]
        if len(matches) != 1:
            print(f"ERROR {target}: expected one coverage entry, found {len(matches)}")
            failed = True
            continue

        summary = matches[0].get("summary", {})
        branches = int(summary.get("num_branches", 0))
        percent = float(summary.get("percent_covered", 0.0))
        print(f"{target}: {percent:.2f}% ({branches} branches measured)")
        if branches == 0:
            print(f"ERROR {target}: branch coverage was not enabled")
            failed = True
        if percent < arguments.minimum:
            print(f"ERROR {target}: {percent:.2f}% is below the {arguments.minimum:.2f}% floor")
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
