import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any

if __package__:
    from .evaluation_metrics import (
        ManifestError,
        edit_distance,
        normalize_text,
        percentile,
        ratio,
        read_jsonl,
        require,
        write_report,
    )
else:
    from evaluation_metrics import (  # type: ignore[no-redef,import-not-found]
        ManifestError,
        edit_distance,
        normalize_text,
        percentile,
        ratio,
        read_jsonl,
        require,
        write_report,
    )


_EXPECTED_SYSTEMS = (
    "raw_asr",
    "static_hotwords",
    "hotwords_context",
    "full_correction",
)


def _comparison_coverage(
    rows: list[dict[str, Any]], grouped: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    sample_ids_available = all(
        isinstance(row.get("sample_id"), str) and bool(row["sample_id"].strip())
        for row in rows
    )
    report: dict[str, Any] = {
        "expected_systems": list(_EXPECTED_SYSTEMS),
        "present_systems": sorted(grouped),
        "missing_systems": [
            system for system in _EXPECTED_SYSTEMS if system not in grouped
        ],
        "unexpected_systems": [
            system for system in sorted(grouped) if system not in _EXPECTED_SYSTEMS
        ],
        "sample_ids_available": sample_ids_available,
    }
    if not sample_ids_available:
        return report

    ids_by_system: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        system = require(row, "system", str)
        sample_id = require(row, "sample_id", str).strip()
        if sample_id in ids_by_system[system]:
            raise ManifestError(
                f"line {row['_line']}: duplicate system/sample_id pair "
                f"{system!r}/{sample_id!r}"
            )
        ids_by_system[system].add(sample_id)

    all_sample_ids = set().union(*ids_by_system.values())
    fully_paired_ids = set(all_sample_ids)
    for system in _EXPECTED_SYSTEMS:
        fully_paired_ids &= ids_by_system.get(system, set())
    report.update(
        {
            "unique_sample_count": len(all_sample_ids),
            "fully_paired_sample_count": len(fully_paired_ids),
            "incomplete_sample_ids": sorted(all_sample_ids - fully_paired_ids),
            "per_system_sample_count": {
                system: len(sample_ids)
                for system, sample_ids in sorted(ids_by_system.items())
            },
        }
    )
    return report


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        system = require(row, "system", str)
        if not system.strip():
            raise ManifestError(f"line {row['_line']}: system must not be empty")
        grouped[system].append(row)

    comparison_coverage = _comparison_coverage(rows, grouped)

    systems: dict[str, Any] = {}
    for system, samples in sorted(grouped.items()):
        total_edits = 0
        total_reference_characters = 0
        keyword_correct = keyword_total = 0
        term_correct = term_total = 0
        latencies: list[float] = []
        total_latency = total_audio = 0.0
        for row in samples:
            reference = normalize_text(require(row, "reference_text", str))
            hypothesis = normalize_text(require(row, "hypothesis_text", str))
            if not reference:
                raise ManifestError(
                    f"line {row['_line']}: reference_text is empty after normalization"
                )
            total_edits += edit_distance(reference, hypothesis)
            total_reference_characters += len(reference)

            keywords = require(row, "reference_keywords", list)
            terms = require(row, "reference_terms", list)
            if not all(isinstance(item, str) and item for item in [*keywords, *terms]):
                raise ManifestError(
                    f"line {row['_line']}: reference_keywords/reference_terms must contain strings"
                )
            for keyword in keywords:
                keyword_total += 1
                keyword_correct += normalize_text(keyword) in hypothesis
            for term in terms:
                term_total += 1
                term_correct += normalize_text(term) in hypothesis

            latency = require(row, "latency_ms", (int, float))
            audio = require(row, "audio_duration_ms", (int, float))
            if latency < 0 or audio <= 0:
                raise ManifestError(
                    f"line {row['_line']}: latency_ms must be >= 0 and audio_duration_ms > 0"
                )
            latencies.append(float(latency))
            total_latency += float(latency)
            total_audio += float(audio)

        systems[system] = {
            "sample_count": len(samples),
            "cer": ratio(total_edits, total_reference_characters),
            "character_edits": total_edits,
            "reference_characters": total_reference_characters,
            "campus_keyword_accuracy": ratio(keyword_correct, keyword_total),
            "campus_keyword_correct": keyword_correct,
            "campus_keyword_total": keyword_total,
            "bilingual_term_accuracy": ratio(term_correct, term_total),
            "bilingual_term_correct": term_correct,
            "bilingual_term_total": term_total,
            "average_latency_ms": round(sum(latencies) / len(latencies), 3),
            "p95_latency_ms": percentile(latencies, 0.95),
            "rtf": ratio(total_latency, total_audio),
        }
    return {
        "manifest_kind": "asr",
        "normalization": "lowercase; remove whitespace and non-alphanumeric/non-CJK characters",
        "comparison_coverage": comparison_coverage,
        "systems": systems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute ASR metrics from raw JSONL outputs."
    )
    parser.add_argument("manifest", type=Path, help="raw ASR observations in JSONL")
    parser.add_argument("--output", type=Path, help="write JSON report to this path")
    args = parser.parse_args()
    try:
        write_report(evaluate(read_jsonl(args.manifest)), args.output)
    except ManifestError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
