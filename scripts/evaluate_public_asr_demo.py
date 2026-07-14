import argparse
import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

if __package__:
    from .evaluate_asr import summarize_asr_samples
    from .evaluation_metrics import ManifestError, read_jsonl, require
    from .prepare_public_asr_demo import (
        MIX_ALGORITHM,
        REQUIRED_FIELDS,
        _source_summary,
        normalize_checksum,
        read_wav,
        resolve_audio_path,
        sha256_file,
        validate_source_rows,
    )
else:
    from evaluate_asr import (  # type: ignore[no-redef,import-not-found]
        summarize_asr_samples,
    )
    from evaluation_metrics import (  # type: ignore[no-redef,import-not-found]
        ManifestError,
        read_jsonl,
        require,
    )
    from prepare_public_asr_demo import (  # type: ignore[no-redef,import-not-found]
        MIX_ALGORITHM,
        REQUIRED_FIELDS,
        _source_summary,
        normalize_checksum,
        read_wav,
        resolve_audio_path,
        sha256_file,
        validate_source_rows,
    )


INFERENCE_STATUSES = frozenset({"COMPLETED", "FAILED", "NOT_RUN"})
INFERENCE_FIELDS = frozenset(
    {
        "sample_id",
        "system",
        "model_name",
        "model_version",
        "device",
        "inference_parameters",
        "inference_status",
        "hypothesis_text",
        "latency_ms",
        "mixed_audio_path",
        "mixed_audio_checksum",
        "actual_snr_db",
        "sample_rate_hz",
        "audio_duration_ms",
        "mix_algorithm",
    }
)


def _line(row: dict[str, Any]) -> int | str:
    return row.get("_line", "?")


def _non_empty_string(row: dict[str, Any], key: str) -> str:
    value = require(row, key, str).strip()
    if not value:
        raise ManifestError(f"line {_line(row)}: {key} must not be empty")
    return value


def _validate_test_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ManifestError("test_date must be YYYY-MM-DD") from exc
    if parsed > datetime.now(UTC).date():
        raise ManifestError("test_date cannot be in the future")
    return value


def _json_identity(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ManifestError("inference_parameters must be JSON serializable") from exc


def validate_inference_rows(rows: list[dict[str, Any]]) -> None:
    """Validate source metadata once per sample and fixed model metadata per system."""

    unique_samples: dict[str, dict[str, Any]] = {}
    source_identity_fields = sorted(
        REQUIRED_FIELDS
        | {
            "sample_id",
            "mixed_audio_path",
            "mixed_audio_checksum",
            "actual_snr_db",
            "sample_rate_hz",
            "audio_duration_ms",
            "mix_algorithm",
        }
    )
    model_by_system: dict[str, str] = {}
    system_sample_pairs: set[tuple[str, str]] = set()
    for row in rows:
        missing = sorted((REQUIRED_FIELDS | INFERENCE_FIELDS) - row.keys())
        if missing:
            raise ManifestError(f"line {_line(row)}: missing required fields {missing}")

        sample_id = _non_empty_string(row, "sample_id")
        system = _non_empty_string(row, "system")
        pair = (system, sample_id)
        if pair in system_sample_pairs:
            raise ManifestError(f"line {_line(row)}: duplicate system/sample_id pair {pair}")
        system_sample_pairs.add(pair)

        source_identity = {field: row[field] for field in source_identity_fields}
        existing = unique_samples.get(sample_id)
        if existing is None:
            unique_samples[sample_id] = row
        else:
            existing_identity = {field: existing[field] for field in source_identity_fields}
            if _json_identity(source_identity) != _json_identity(existing_identity):
                raise ManifestError(
                    f"line {_line(row)}: source metadata changed across systems for "
                    f"sample_id {sample_id!r}"
                )

        model_name = _non_empty_string(row, "model_name")
        model_version = _non_empty_string(row, "model_version")
        device = _non_empty_string(row, "device")
        parameters = require(row, "inference_parameters", dict)
        configuration = _json_identity(
            {
                "model_name": model_name,
                "model_version": model_version,
                "device": device,
                "inference_parameters": parameters,
            }
        )
        if system in model_by_system and model_by_system[system] != configuration:
            raise ManifestError(
                f"line {_line(row)}: model/version/device/inference_parameters changed "
                f"within system {system!r}"
            )
        model_by_system[system] = configuration

        status = _non_empty_string(row, "inference_status")
        if status not in INFERENCE_STATUSES:
            raise ManifestError(
                f"line {_line(row)}: inference_status must be one of {sorted(INFERENCE_STATUSES)}"
            )
        hypothesis = row.get("hypothesis_text")
        latency = row.get("latency_ms")
        if status == "COMPLETED":
            if not isinstance(hypothesis, str):
                raise ManifestError(f"line {_line(row)}: completed inference needs hypothesis_text")
            if isinstance(latency, bool) or not isinstance(latency, (int, float)):
                raise ManifestError(
                    f"line {_line(row)}: completed inference needs numeric latency_ms"
                )
        elif status == "FAILED":
            if hypothesis not in (None, ""):
                raise ManifestError(
                    f"line {_line(row)}: failed inference must not contain a hypothesis"
                )
            if latency is not None and (
                isinstance(latency, bool) or not isinstance(latency, (int, float))
            ):
                raise ManifestError(
                    f"line {_line(row)}: failed inference latency_ms must be numeric or null"
                )
        elif hypothesis is not None or latency is not None:
            raise ManifestError(
                f"line {_line(row)}: NOT_RUN rows must have null hypothesis_text and latency_ms"
            )
        if isinstance(latency, (int, float)) and not isinstance(latency, bool) and latency < 0:
            raise ManifestError(f"line {_line(row)}: latency_ms must be >= 0")

        duration = row.get("audio_duration_ms")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
            raise ManifestError(f"line {_line(row)}: audio_duration_ms must be > 0")
        if row.get("sample_rate_hz") != 16_000:
            raise ManifestError(f"line {_line(row)}: sample_rate_hz must be 16000")
        if row.get("mix_algorithm") != MIX_ALGORITHM:
            raise ManifestError(
                f"line {_line(row)}: mix_algorithm does not match the fixed demo method"
            )
        actual_snr = row.get("actual_snr_db")
        if isinstance(actual_snr, bool) or not isinstance(actual_snr, (int, float)):
            raise ManifestError(f"line {_line(row)}: actual_snr_db must be numeric")
        if abs(float(actual_snr) - float(row["snr_db"])) > 0.1:
            raise ManifestError(
                f"line {_line(row)}: actual_snr_db differs from target by more than 0.1 dB"
            )
        _non_empty_string(row, "mixed_audio_path")
        normalize_checksum(_non_empty_string(row, "mixed_audio_checksum"), line=_line(row))

        for key in ("reference_keywords", "reference_terms"):
            values = row.get(key, [])
            if not isinstance(values, list) or not all(
                isinstance(item, str) and item for item in values
            ):
                raise ManifestError(f"line {_line(row)}: {key} must be a list of non-empty strings")

    validate_source_rows(list(unique_samples.values()), require_full_matrix=True)


def _model_configurations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    configurations: dict[str, dict[str, Any]] = {}
    for row in rows:
        configurations.setdefault(
            row["system"],
            {
                "system": row["system"],
                "model_name": row["model_name"],
                "model_version": row["model_version"],
                "device": row["device"],
                "inference_parameters": row["inference_parameters"],
            },
        )
    return [configurations[key] for key in sorted(configurations)]


def _metric_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "_line": row["_line"],
        "reference_text": row["transcript"],
        "hypothesis_text": row["hypothesis_text"],
        "reference_keywords": row.get("reference_keywords", []),
        "reference_terms": row.get("reference_terms", []),
        "latency_ms": row["latency_ms"],
        "audio_duration_ms": row["audio_duration_ms"],
        "inference_status": row["inference_status"],
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return summarize_asr_samples(
        [_metric_row(row) for row in rows], allow_failed_without_latency=True
    )


def _group_metrics(
    rows: list[dict[str, Any]], key: Callable[[dict[str, Any]], str]
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return {name: _summarize(group) for name, group in sorted(grouped.items())}


def _unique_source_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_sample: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_sample.setdefault(row["sample_id"], row)
    return list(by_sample.values())


def _missing_or_invalid_mixed_audio(manifest_path: Path, rows: list[dict[str, Any]]) -> list[str]:
    missing: set[str] = set()
    checked: dict[Path, str] = {}
    for row in _unique_source_rows(rows):
        path = resolve_audio_path(manifest_path, row["mixed_audio_path"])
        if not path.is_file():
            missing.add(str(path))
            continue
        expected = normalize_checksum(row["mixed_audio_checksum"], line=_line(row))
        previous = checked.get(path)
        if previous is not None and previous != expected:
            raise ManifestError(f"mixed audio has conflicting checksums: {path}")
        if previous is None:
            samples, sample_rate = read_wav(path)
            if sample_rate != 16_000 or len(samples) == 0:
                raise ManifestError(f"mixed WAV is not valid 16 kHz PCM audio: {path}")
            actual = sha256_file(path)
            if actual != expected:
                raise ManifestError(
                    f"mixed audio checksum mismatch for {path}; expected {expected}, got {actual}"
                )
            checked[path] = expected
    return sorted(missing)


def build_report(
    rows: list[dict[str, Any]],
    *,
    test_date: str,
    reproduction_command: str,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    validate_inference_rows(rows)
    test_date = _validate_test_date(test_date)
    test_rows = [row for row in rows if row["split"] == "test"]
    sources = _unique_source_rows(test_rows)
    report: dict[str, Any] = {
        "report_kind": "public_human_speech_simulated_campus_noise_asr",
        "generated_at": datetime.now(UTC).isoformat(),
        "test_date": test_date,
        "planned_inference_count": len(test_rows),
        "tuning_observation_count": len(rows) - len(test_rows),
        "unique_audio_count": len(sources),
        "speaker_count": len({row["anonymous_speaker_id"] for row in sources}),
        "data_sources": _source_summary(sources),
        "model_configurations": _model_configurations(test_rows),
        "mix_method": MIX_ALGORITHM,
        "reproduction_command": reproduction_command,
        "limitations": [
            "Public human speech plus simulated campus noise is not a real campus field test.",
            "Results apply only to the listed datasets, noise clips, systems, and SNR levels.",
            "Common Voice speaker identification and dataset re-hosting/re-sharing are prohibited.",
            "Only aggregate metrics may be used in demonstration materials.",
        ],
    }
    if manifest_path is not None:
        missing = _missing_or_invalid_mixed_audio(manifest_path, test_rows)
        if missing:
            report.update(
                {
                    "status": "NOT_RUN",
                    "reason": "MIXED_AUDIO_MISSING",
                    "missing_files": missing,
                }
            )
            return report

    attempted = [row for row in test_rows if row["inference_status"] != "NOT_RUN"]
    not_run_count = len(test_rows) - len(attempted)
    if not attempted:
        report.update(
            {
                "status": "NOT_RUN",
                "reason": "ASR_INFERENCE_NOT_SUPPLIED",
                "not_run_count": not_run_count,
            }
        )
        return report

    report.update(
        {
            "status": "PARTIAL" if not_run_count else "COMPLETE",
            "attempted_inference_count": len(attempted),
            "not_run_count": not_run_count,
            "metrics": {
                "overall": _summarize(attempted),
                "by_system": _group_metrics(attempted, lambda row: row["system"]),
                "by_dataset": _group_metrics(
                    attempted,
                    lambda row: f"{row['speech_dataset']} ({row['speech_dataset_version']})",
                ),
                "by_noise_category": _group_metrics(attempted, lambda row: row["noise_category"]),
                "by_snr_db": _group_metrics(attempted, lambda row: f"{float(row['snr_db']):g}"),
            },
        }
    )
    return report


def _fingerprinted(report: dict[str, Any]) -> dict[str, Any]:
    finalized = dict(report)
    finalized.pop("report_fingerprint", None)
    canonical = json.dumps(finalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    finalized["report_fingerprint"] = hashlib.sha256(canonical.encode()).hexdigest()
    return finalized


def _format_metric(value: Any) -> str:
    return "N/A" if value is None else str(value)


def render_markdown(report: dict[str, Any]) -> str:
    if "report_fingerprint" not in report:
        raise ManifestError("report must be fingerprinted before Markdown rendering")
    lines = [
        "# Public human speech + simulated campus noise ASR report",
        "",
        f"- Status: `{report['status']}`",
        f"- Report fingerprint: `{report['report_fingerprint']}`",
    ]
    if report.get("test_date"):
        lines.append(f"- Test date: `{report['test_date']}`")
    lines.extend(
        [
            f"- Planned inference observations: {report.get('planned_inference_count', 0)}",
            f"- Unique mixed audio files: {report.get('unique_audio_count', 0)}",
            f"- Anonymous speakers: {report.get('speaker_count', 0)}",
            "",
        ]
    )
    if report["status"] == "NOT_RUN":
        lines.extend(
            [
                "## NOT_RUN",
                "",
                f"Reason: `{report.get('reason', 'UNSPECIFIED')}`.",
                "No ASR quality or latency metrics were generated.",
                "",
            ]
        )

    lines.extend(
        [
            "## Data sources and licenses",
            "",
            "### Speech",
            "",
            "| Dataset | Version | License | Source |",
            "| --- | --- | --- | --- |",
        ]
    )
    for source in report.get("data_sources", {}).get("speech", []):
        lines.append(
            f"| {source['dataset']} | {source['version']} | {source['license']} | "
            f"{source['source']} |"
        )
    lines.extend(
        [
            "",
            "### Noise clips",
            "",
            "| Clip | License | Verified | Source | Attribution |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for source in report.get("data_sources", {}).get("noise", []):
        lines.append(
            f"| {source['clip_id']} | {source['license']} | "
            f"{source['license_verified_at']} | {source['source']} | "
            f"{source['attribution'] or 'Not required (CC0)'} |"
        )

    lines.extend(
        [
            "",
            "## Fixed model and hardware metadata",
            "",
            "| System | Model | Version | Device | Parameters |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for model in report.get("model_configurations", []):
        parameters = json.dumps(
            model["inference_parameters"], ensure_ascii=False, sort_keys=True
        ).replace("|", "\\|")
        lines.append(
            f"| {model['system']} | {model['model_name']} | {model['model_version']} | "
            f"{model['device']} | `{parameters}` |"
        )

    if "metrics" in report:
        lines.extend(
            [
                "",
                "## Measured metrics",
                "",
                "| Grouping | Group | Samples | CER | Sentence accuracy | Failure rate | "
                "P50 latency (ms) | P95 latency (ms) |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        sections = [("overall", {"all": report["metrics"]["overall"]})]
        sections.extend(
            (name, report["metrics"][name])
            for name in (
                "by_system",
                "by_dataset",
                "by_noise_category",
                "by_snr_db",
            )
        )
        for grouping, groups in sections:
            for group, metrics in groups.items():
                lines.append(
                    f"| {grouping} | {group} | {metrics['sample_count']} | "
                    f"{_format_metric(metrics['cer'])} | "
                    f"{_format_metric(metrics['sentence_accuracy'])} | "
                    f"{_format_metric(metrics['failure_rate'])} | "
                    f"{_format_metric(metrics['p50_latency_ms'])} | "
                    f"{_format_metric(metrics['p95_latency_ms'])} |"
                )

    lines.extend(["", "## Mixing method", "", report.get("mix_method", "N/A")])
    lines.extend(["", "## Known limitations", ""])
    lines.extend(f"- {item}" for item in report.get("limitations", []))
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```powershell",
            report.get("reproduction_command", ""),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def write_report_pair(
    report: dict[str, Any], json_path: Path, markdown_path: Path
) -> dict[str, Any]:
    finalized = _fingerprinted(report)
    rendered_json = json.dumps(finalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    rendered_markdown = render_markdown(finalized)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(rendered_json, encoding="utf-8")
    markdown_path.write_text(rendered_markdown, encoding="utf-8")
    return finalized


def _missing_manifest_report(manifest_path: Path, command: str) -> dict[str, Any]:
    return {
        "report_kind": "public_human_speech_simulated_campus_noise_asr",
        "status": "NOT_RUN",
        "reason": "INFERENCE_MANIFEST_MISSING",
        "missing_files": [str(manifest_path)],
        "planned_inference_count": 0,
        "unique_audio_count": 0,
        "speaker_count": 0,
        "data_sources": {"speech": [], "noise": []},
        "model_configurations": [],
        "reproduction_command": command,
        "limitations": [
            "No ASR quality or latency metrics were generated because inference data is missing."
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate evidence-backed JSON and Markdown reports from real ASR observations "
            "over prepared public-speech/simulated-noise audio."
        )
    )
    parser.add_argument("manifest", type=Path, help="inference observations in JSONL")
    parser.add_argument(
        "--json-report",
        type=Path,
        default=Path("data/evaluation/results/public-asr-demo.json"),
    )
    parser.add_argument(
        "--markdown-report",
        type=Path,
        default=Path("data/evaluation/results/public-asr-demo.md"),
    )
    parser.add_argument(
        "--test-date", default=datetime.now(UTC).date().isoformat(), help="YYYY-MM-DD"
    )
    args = parser.parse_args()
    command = (
        f"python scripts/evaluate_public_asr_demo.py {args.manifest} "
        f"--json-report {args.json_report} --markdown-report {args.markdown_report} "
        f"--test-date {args.test_date}"
    )
    try:
        if args.manifest.is_file():
            report = build_report(
                read_jsonl(args.manifest),
                test_date=args.test_date,
                reproduction_command=command,
                manifest_path=args.manifest,
            )
        else:
            report = _missing_manifest_report(args.manifest, command)
        finalized = write_report_pair(report, args.json_report, args.markdown_report)
    except ManifestError as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "status": finalized["status"],
                "json_report": str(args.json_report),
                "markdown_report": str(args.markdown_report),
                "report_fingerprint": finalized["report_fingerprint"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
