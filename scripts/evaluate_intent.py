import argparse
from collections import Counter
from pathlib import Path
from typing import Any

if __package__:
    from .evaluation_metrics import (
        ManifestError,
        ratio,
        read_jsonl,
        require,
        write_report,
    )
else:
    from evaluation_metrics import (  # type: ignore[no-redef,import-not-found]
        ManifestError,
        ratio,
        read_jsonl,
        require,
        write_report,
    )

_SLOT_FIELDS = ("date", "start_time", "course")


def _object(row: dict[str, Any], parent: str, key: str) -> dict[str, Any]:
    value = row.get(parent)
    if not isinstance(value, dict) or not isinstance(value.get(key), dict):
        raise ManifestError(f"line {row['_line']}: {parent}.{key} must be an object")
    return value[key]


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    intent_correct = 0
    missing_correct = 0
    invalid_actual = 0
    slot_correct: Counter[str] = Counter()
    slot_total: Counter[str] = Counter()
    all_slot_correct = all_slot_total = 0

    for row in rows:
        expected = require(row, "expected", dict)
        actual = require(row, "actual", dict)
        expected_intent = expected.get("intent")
        actual_intent = actual.get("intent")
        if not isinstance(expected_intent, str):
            raise ManifestError(
                f"line {row['_line']}: expected.intent must be a string"
            )
        if not isinstance(actual_intent, str):
            invalid_actual += 1
        intent_correct += actual_intent == expected_intent

        expected_slots = _object(row, "expected", "slots")
        actual_slots = (
            actual.get("slots") if isinstance(actual.get("slots"), dict) else {}
        )
        if not isinstance(actual.get("slots"), dict):
            invalid_actual += 1
        for field, expected_value in expected_slots.items():
            if expected_value is None:
                continue
            all_slot_total += 1
            all_slot_correct += actual_slots.get(field) == expected_value
            if field in _SLOT_FIELDS:
                slot_total[field] += 1
                slot_correct[field] += actual_slots.get(field) == expected_value

        expected_missing = expected.get("missing_fields", [])
        actual_missing = actual.get("missing_fields", [])
        if not isinstance(expected_missing, list):
            raise ManifestError(
                f"line {row['_line']}: expected.missing_fields must be a list"
            )
        if not isinstance(actual_missing, list):
            invalid_actual += 1
            actual_missing = []
        missing_correct += set(actual_missing) == set(expected_missing)

    return {
        "manifest_kind": "intent",
        "sample_count": len(rows),
        "intent_accuracy": ratio(intent_correct, len(rows)),
        "intent_correct": intent_correct,
        "slot_accuracy": ratio(all_slot_correct, all_slot_total),
        "slot_correct": all_slot_correct,
        "slot_total": all_slot_total,
        "date_accuracy": ratio(slot_correct["date"], slot_total["date"]),
        "time_accuracy": ratio(slot_correct["start_time"], slot_total["start_time"]),
        "course_accuracy": ratio(slot_correct["course"], slot_total["course"]),
        "missing_information_detection_accuracy": ratio(missing_correct, len(rows)),
        "invalid_actual_field_count": invalid_actual,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute intent metrics from raw JSONL outputs."
    )
    parser.add_argument("manifest", type=Path, help="raw intent observations in JSONL")
    parser.add_argument("--output", type=Path, help="write JSON report to this path")
    args = parser.parse_args()
    try:
        write_report(evaluate(read_jsonl(args.manifest)), args.output)
    except ManifestError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
