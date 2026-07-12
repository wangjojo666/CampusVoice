import argparse
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


def _boolean(mapping: dict[str, Any], key: str, line: int) -> bool:
    value = mapping.get(key)
    if not isinstance(value, bool):
        raise ManifestError(f"line {line}: {key} must be boolean")
    return value


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counters = {
        "expected_success": 0,
        "successful": 0,
        "conflicts": 0,
        "conflicts_detected": 0,
        "duplicates": 0,
        "duplicates_detected": 0,
        "should_clarify": 0,
        "clarified": 0,
        "should_not_clarify": 0,
        "unnecessarily_clarified": 0,
        "high_risk": 0,
        "high_risk_confirmed": 0,
        "save_failures": 0,
        "save_failures_detected": 0,
    }
    for row in rows:
        expected = require(row, "expected", dict)
        actual = require(row, "actual", dict)
        line = int(row["_line"])
        should_succeed = _boolean(expected, "should_succeed", line)
        has_conflict = _boolean(expected, "has_conflict", line)
        is_duplicate = _boolean(expected, "is_duplicate", line)
        should_clarify = _boolean(expected, "should_clarify", line)
        high_risk = _boolean(expected, "high_risk", line)
        save_failed = _boolean(expected, "save_failed", line)

        success = _boolean(actual, "success", line)
        conflict_detected = _boolean(actual, "conflict_detected", line)
        duplicate_detected = _boolean(actual, "duplicate_detected", line)
        asked_clarification = _boolean(actual, "asked_clarification", line)
        confirmation_requested = _boolean(actual, "confirmation_requested", line)
        save_failure_detected = _boolean(actual, "save_failure_detected", line)

        counters["expected_success"] += should_succeed
        counters["successful"] += should_succeed and success
        counters["conflicts"] += has_conflict
        counters["conflicts_detected"] += has_conflict and conflict_detected
        counters["duplicates"] += is_duplicate
        counters["duplicates_detected"] += is_duplicate and duplicate_detected
        counters["should_clarify"] += should_clarify
        counters["clarified"] += should_clarify and asked_clarification
        counters["should_not_clarify"] += not should_clarify
        counters["unnecessarily_clarified"] += (
            not should_clarify and asked_clarification
        )
        counters["high_risk"] += high_risk
        counters["high_risk_confirmed"] += high_risk and confirmation_requested
        counters["save_failures"] += save_failed
        counters["save_failures_detected"] += (
            save_failed and save_failure_detected and not success
        )

    return {
        "manifest_kind": "reliability",
        "sample_count": len(rows),
        "operation_success_rate": ratio(
            counters["successful"], counters["expected_success"]
        ),
        "conflict_detection_rate": ratio(
            counters["conflicts_detected"], counters["conflicts"]
        ),
        "duplicate_detection_rate": ratio(
            counters["duplicates_detected"], counters["duplicates"]
        ),
        "required_clarification_rate": ratio(
            counters["clarified"], counters["should_clarify"]
        ),
        "unnecessary_clarification_rate": ratio(
            counters["unnecessarily_clarified"], counters["should_not_clarify"]
        ),
        "high_risk_confirmation_rate": ratio(
            counters["high_risk_confirmed"], counters["high_risk"]
        ),
        "save_failure_detection_rate": ratio(
            counters["save_failures_detected"], counters["save_failures"]
        ),
        "counts": counters,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute reliable-execution metrics from raw JSONL observations."
    )
    parser.add_argument(
        "manifest", type=Path, help="raw reliable-execution observations in JSONL"
    )
    parser.add_argument("--output", type=Path, help="write JSON report to this path")
    args = parser.parse_args()
    try:
        write_report(evaluate(read_jsonl(args.manifest)), args.output)
    except ManifestError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
