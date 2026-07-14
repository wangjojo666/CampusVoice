import copy
import hashlib
import json
import math
import wave
from pathlib import Path

import numpy as np
import pytest
from scripts.evaluate_public_asr_demo import (
    build_report,
    validate_inference_rows,
    write_report_pair,
)
from scripts.evaluation_metrics import ManifestError, read_jsonl
from scripts.prepare_public_asr_demo import (
    COMMON_VOICE_DATASET,
    COMMON_VOICE_RESTRICTIONS,
    COMMON_VOICE_SOURCE,
    COMMON_VOICE_VERSION,
    NOISE_CATEGORIES,
    SNR_LEVELS_DB,
    mix_wav,
    prepare_public_demo,
    sha256_file,
    validate_source_rows,
)


def _write_tone(
    path: Path, frequency: float, *, seconds: float = 0.24, sample_rate: int = 16_000
) -> None:
    positions = np.arange(round(seconds * sample_rate), dtype=np.float64)
    signal = 0.22 * np.sin(2 * math.pi * frequency * positions / sample_rate)
    signal += 0.07 * np.sin(2 * math.pi * (frequency * 1.73) * positions / sample_rate)
    pcm = np.rint(signal * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        destination.writeframes(pcm.tobytes())


def _source_rows(tmp_path: Path) -> list[dict[str, object]]:
    speech_path = tmp_path / "speech.wav"
    _write_tone(speech_path, 233)
    noise_paths: dict[str, Path] = {}
    for index, category in enumerate(NOISE_CATEGORIES):
        noise_path = tmp_path / f"{category}.wav"
        _write_tone(noise_path, 401 + index * 97, seconds=0.31)
        noise_paths[category] = noise_path

    rows: list[dict[str, object]] = []
    line = 1
    for category in NOISE_CATEGORIES:
        for snr_db in SNR_LEVELS_DB:
            rows.append(
                {
                    "speech_dataset": COMMON_VOICE_DATASET,
                    "speech_source": COMMON_VOICE_SOURCE,
                    "speech_dataset_version": COMMON_VOICE_VERSION,
                    "speech_license": "CC0-1.0",
                    "speech_usage_restrictions": sorted(COMMON_VOICE_RESTRICTIONS),
                    "clip_id": "cv26-zh-cn-fixture-001",
                    "transcript": "周五上午九点有机器学习考试",
                    "anonymous_speaker_id": "speaker-fixture-a",
                    "split": "test",
                    "speech_path": str(speech_path),
                    "checksum": sha256_file(speech_path),
                    "noise_source": f"https://example.invalid/noise/{category}-001",
                    "noise_clip_id": f"noise-{category}-001",
                    "noise_license": "CC0-1.0",
                    "noise_license_verified": True,
                    "noise_license_verified_at": "2026-07-01",
                    "noise_attribution": "",
                    "noise_category": category,
                    "noise_path": str(noise_paths[category]),
                    "snr_db": snr_db,
                    "mix_seed": 1000 + line,
                    "_line": line,
                }
            )
            line += 1
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(
            json.dumps({key: value for key, value in row.items() if key != "_line"}) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _prepared_rows(tmp_path: Path) -> tuple[Path, list[dict[str, object]]]:
    source_manifest = tmp_path / "source.jsonl"
    _write_jsonl(source_manifest, _source_rows(tmp_path))
    output_dir = tmp_path / "prepared"
    report = prepare_public_demo(source_manifest, output_dir)
    assert report["status"] == "PREPARED"
    mixed_manifest = output_dir / "mixed-manifest.jsonl"
    return mixed_manifest, read_jsonl(mixed_manifest)


def _inference_rows(
    prepared_rows: list[dict[str, object]], *, status: str = "COMPLETED"
) -> list[dict[str, object]]:
    rows = copy.deepcopy(prepared_rows)
    for index, row in enumerate(rows):
        row.update(
            {
                "system": "funasr-fixed-fixture",
                "model_name": "FunASR fixture metadata only",
                "model_version": "fixture-version-1",
                "device": "cpu-test-fixture",
                "inference_parameters": {
                    "language": "zh",
                    "hotwords": False,
                    "beam_size": 1,
                },
                "inference_status": status,
                "hypothesis_text": row["transcript"] if status == "COMPLETED" else None,
                "latency_ms": 100.0 + index if status == "COMPLETED" else None,
                "reference_keywords": ["机器学习"],
                "reference_terms": [],
            }
        )
    return rows


def test_noise_mix_is_byte_deterministic_for_same_seed(tmp_path: Path) -> None:
    rows = _source_rows(tmp_path)
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"

    first_stats = mix_wav(
        Path(str(rows[0]["speech_path"])),
        Path(str(rows[0]["noise_path"])),
        first,
        snr_db=float(rows[0]["snr_db"]),
        mix_seed=int(rows[0]["mix_seed"]),
    )
    second_stats = mix_wav(
        Path(str(rows[0]["speech_path"])),
        Path(str(rows[0]["noise_path"])),
        second,
        snr_db=float(rows[0]["snr_db"]),
        mix_seed=int(rows[0]["mix_seed"]),
    )

    assert first.read_bytes() == second.read_bytes()
    assert first_stats == second_stats


@pytest.mark.parametrize("snr_db", SNR_LEVELS_DB)
def test_noise_mix_measures_requested_snr(tmp_path: Path, snr_db: float) -> None:
    row = _source_rows(tmp_path)[0]
    output = tmp_path / f"snr-{snr_db:g}.wav"

    statistics = mix_wav(
        Path(str(row["speech_path"])),
        Path(str(row["noise_path"])),
        output,
        snr_db=snr_db,
        mix_seed=42,
    )

    assert statistics["actual_snr_db"] == pytest.approx(snr_db, abs=0.05)


def test_manifest_rejects_missing_unknown_restricted_and_unapproved_licenses(
    tmp_path: Path,
) -> None:
    rows = _source_rows(tmp_path)
    missing_source = copy.deepcopy(rows)
    del missing_source[0]["speech_source"]
    with pytest.raises(ManifestError, match="missing required fields"):
        validate_source_rows(missing_source)

    unknown_noise = copy.deepcopy(rows)
    unknown_noise[0]["noise_license"] = "UNKNOWN"
    with pytest.raises(ManifestError, match="not allowed"):
        validate_source_rows(unknown_noise)

    noncommercial_noise = copy.deepcopy(rows)
    noncommercial_noise[0]["noise_license"] = "CC-BY-NC-4.0"
    with pytest.raises(ManifestError, match="not allowed"):
        validate_source_rows(noncommercial_noise)

    academic_only = copy.deepcopy(rows)
    academic_only[0]["noise_license"] = "ACADEMIC-ONLY"
    with pytest.raises(ManifestError, match="not allowed"):
        validate_source_rows(academic_only)

    wenetspeech = copy.deepcopy(rows)
    wenetspeech[0]["speech_dataset"] = "WenetSpeech"
    with pytest.raises(ManifestError, match="requires explicit legal approval"):
        validate_source_rows(wenetspeech)


def test_cc_by_noise_requires_attribution_and_verification_date(tmp_path: Path) -> None:
    rows = _source_rows(tmp_path)
    rows[0]["noise_license"] = "CC-BY-4.0"
    with pytest.raises(ManifestError, match="requires noise_attribution"):
        validate_source_rows(rows)

    rows[0]["noise_attribution"] = "Author; title; source; license; mixed at 10 dB"
    rows[0]["noise_license_verified_at"] = "not-a-date"
    with pytest.raises(ManifestError, match="must be YYYY-MM-DD"):
        validate_source_rows(rows)


def test_manifest_rejects_speaker_overlap_between_tuning_and_test(
    tmp_path: Path,
) -> None:
    rows = _source_rows(tmp_path)
    rows[0]["split"] = "tuning"

    with pytest.raises(ManifestError, match="speaker-disjoint violation"):
        validate_source_rows(rows)


def test_prepare_returns_not_run_for_missing_audio_and_rejects_corrupt_wav(
    tmp_path: Path,
) -> None:
    rows = _source_rows(tmp_path)
    manifest = tmp_path / "source.jsonl"
    missing_noise = Path(str(rows[0]["noise_path"]))
    missing_noise.unlink()
    _write_jsonl(manifest, rows)

    not_run = prepare_public_demo(manifest, tmp_path / "missing-output")

    assert not_run["status"] == "NOT_RUN"
    assert not_run["reason"] == "INPUT_AUDIO_MISSING"
    assert "metrics" not in not_run

    _write_tone(missing_noise, 444)
    missing_noise.write_bytes(b"not a wav")
    with pytest.raises(ManifestError, match="missing or corrupt WAV"):
        prepare_public_demo(manifest, tmp_path / "corrupt-output")


def test_preparation_is_reproducible_and_records_measured_snr(tmp_path: Path) -> None:
    rows = _source_rows(tmp_path)
    manifest = tmp_path / "source.jsonl"
    _write_jsonl(manifest, rows)

    first = prepare_public_demo(manifest, tmp_path / "first")
    second = prepare_public_demo(manifest, tmp_path / "second")
    first_rows = read_jsonl(Path(first["mixed_manifest"]))
    second_rows = read_jsonl(Path(second["mixed_manifest"]))

    assert first["status"] == second["status"] == "PREPARED"
    assert [row["mixed_audio_checksum"] for row in first_rows] == [
        row["mixed_audio_checksum"] for row in second_rows
    ]
    assert all(row["actual_snr_db"] == pytest.approx(row["snr_db"], abs=0.05) for row in first_rows)
    assert first["metrics_status"] == "NOT_RUN"
    assert "metrics" not in first


def test_report_groups_real_results_and_json_markdown_share_fingerprint(
    tmp_path: Path,
) -> None:
    mixed_manifest, prepared = _prepared_rows(tmp_path)
    inference = _inference_rows(prepared)
    inference[-1]["inference_status"] = "FAILED"
    inference[-1]["hypothesis_text"] = None
    inference[-1]["latency_ms"] = None
    report = build_report(
        inference,
        test_date="2026-07-14",
        reproduction_command="python scripts/evaluate_public_asr_demo.py fixture.jsonl",
        manifest_path=mixed_manifest,
    )
    json_report = tmp_path / "report.json"
    markdown_report = tmp_path / "report.md"

    finalized = write_report_pair(report, json_report, markdown_report)
    parsed = json.loads(json_report.read_text(encoding="utf-8"))
    markdown = markdown_report.read_text(encoding="utf-8")

    assert parsed == finalized
    assert parsed["status"] == "COMPLETE"
    assert parsed["metrics"]["overall"]["failure_rate"] == pytest.approx(1 / 9)
    assert parsed["metrics"]["overall"]["sentence_accuracy"] == pytest.approx(8 / 9)
    assert set(parsed["metrics"]["by_noise_category"]) == set(NOISE_CATEGORIES)
    assert set(parsed["metrics"]["by_snr_db"]) == {"5", "10", "20"}
    fingerprint_payload = dict(parsed)
    fingerprint = fingerprint_payload.pop("report_fingerprint")
    canonical = json.dumps(
        fingerprint_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == fingerprint
    assert parsed["report_fingerprint"] in markdown
    assert str(parsed["metrics"]["overall"]["p50_latency_ms"]) in markdown


def test_report_with_no_inference_is_not_run_and_has_no_metric_section(
    tmp_path: Path,
) -> None:
    mixed_manifest, prepared = _prepared_rows(tmp_path)
    report = build_report(
        _inference_rows(prepared, status="NOT_RUN"),
        test_date="2026-07-14",
        reproduction_command="python scripts/evaluate_public_asr_demo.py fixture.jsonl",
        manifest_path=mixed_manifest,
    )
    json_report = tmp_path / "not-run.json"
    markdown_report = tmp_path / "not-run.md"

    finalized = write_report_pair(report, json_report, markdown_report)
    markdown = markdown_report.read_text(encoding="utf-8")

    assert finalized["status"] == "NOT_RUN"
    assert finalized["reason"] == "ASR_INFERENCE_NOT_SUPPLIED"
    assert "metrics" not in finalized
    assert "## Measured metrics" not in markdown
    assert "No ASR quality or latency metrics were generated" in markdown


def test_report_returns_not_run_for_missing_mixed_audio_and_rejects_corruption(
    tmp_path: Path,
) -> None:
    mixed_manifest, prepared = _prepared_rows(tmp_path)
    inference = _inference_rows(prepared)
    mixed_path = mixed_manifest.parent / str(prepared[0]["mixed_audio_path"])
    original = mixed_path.read_bytes()
    mixed_path.unlink()

    missing = build_report(
        inference,
        test_date="2026-07-14",
        reproduction_command="fixture",
        manifest_path=mixed_manifest,
    )

    assert missing["status"] == "NOT_RUN"
    assert missing["reason"] == "MIXED_AUDIO_MISSING"
    assert "metrics" not in missing

    mixed_path.write_bytes(original[:20])
    with pytest.raises(ManifestError, match="missing or corrupt WAV|truncated WAV"):
        build_report(
            inference,
            test_date="2026-07-14",
            reproduction_command="fixture",
            manifest_path=mixed_manifest,
        )


def test_inference_metadata_must_be_fixed_per_system(tmp_path: Path) -> None:
    _, prepared = _prepared_rows(tmp_path)
    inference = _inference_rows(prepared)
    inference[1]["model_version"] = "silently-changed-version"

    with pytest.raises(ManifestError, match="changed within system"):
        validate_inference_rows(inference)

    changed_audio = _inference_rows(prepared)
    changed_audio[1]["system"] = "funasr-second-system"
    changed_audio[1]["mixed_audio_checksum"] = "f" * 64
    changed_audio[1]["sample_id"] = changed_audio[0]["sample_id"]
    changed_audio[1]["noise_category"] = changed_audio[0]["noise_category"]
    changed_audio[1]["snr_db"] = changed_audio[0]["snr_db"]
    changed_audio[1]["noise_clip_id"] = changed_audio[0]["noise_clip_id"]
    changed_audio[1]["mix_seed"] = changed_audio[0]["mix_seed"]
    with pytest.raises(ManifestError, match="source metadata changed across systems"):
        validate_inference_rows(changed_audio)
