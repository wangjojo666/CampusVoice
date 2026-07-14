import argparse
import hashlib
import json
import math
import random
import re
import wave
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np

if __package__:
    from .evaluation_metrics import ManifestError, read_jsonl, require
else:
    from evaluation_metrics import (  # type: ignore[no-redef,import-not-found]
        ManifestError,
        read_jsonl,
        require,
    )


COMMON_VOICE_DATASET = "Common Voice Scripted Speech 26.0 - Chinese (China)"
COMMON_VOICE_VERSION = "cv-corpus-26.0-2026-06-12"
COMMON_VOICE_SOURCE = "https://mozilladatacollective.com/datasets/cmqim47x700tunq074za20dq1"
COMMON_VOICE_RESTRICTIONS = frozenset({"no_speaker_identification", "no_rehosting", "no_resharing"})
ALLOWED_NOISE_LICENSES = frozenset({"CC0-1.0", "CC-BY-4.0"})
NOISE_CATEGORIES = ("cafeteria", "corridor", "outdoor")
SNR_LEVELS_DB = (20.0, 10.0, 5.0)
TARGET_SAMPLE_RATE_HZ = 16_000
MIX_ALGORITHM = (
    "PCM float64 mono conversion; deterministic linear resampling; DC removal; "
    "seeded noise offset/loop; RMS scaling; shared anti-clipping gain; PCM16 output"
)
REQUIRED_FIELDS = frozenset(
    {
        "speech_dataset",
        "speech_source",
        "speech_dataset_version",
        "speech_license",
        "speech_usage_restrictions",
        "clip_id",
        "transcript",
        "anonymous_speaker_id",
        "split",
        "speech_path",
        "checksum",
        "noise_source",
        "noise_clip_id",
        "noise_license",
        "noise_license_verified",
        "noise_license_verified_at",
        "noise_attribution",
        "noise_category",
        "noise_path",
        "snr_db",
        "mix_seed",
    }
)
_CHECKSUM_RE = re.compile(r"(?:sha256:)?([0-9a-fA-F]{64})\Z")
_ANONYMOUS_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")


def _line(row: dict[str, Any]) -> int | str:
    return row.get("_line", "?")


def _non_empty_string(row: dict[str, Any], key: str) -> str:
    value = require(row, key, str).strip()
    if not value:
        raise ManifestError(f"line {_line(row)}: {key} must not be empty")
    return value


def _https_url(row: dict[str, Any], key: str) -> str:
    value = _non_empty_string(row, key)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ManifestError(f"line {_line(row)}: {key} must be an official per-source HTTPS URL")
    return value


def normalize_checksum(value: str, *, line: int | str) -> str:
    match = _CHECKSUM_RE.fullmatch(value.strip())
    if not match:
        raise ManifestError(
            f"line {line}: checksum must be a SHA-256 hex digest, optionally prefixed by sha256:"
        )
    return match.group(1).lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ManifestError(f"cannot read audio for checksum: {path}") from exc
    return digest.hexdigest()


def _validate_speech_policy(row: dict[str, Any]) -> None:
    dataset = _non_empty_string(row, "speech_dataset")
    source = _https_url(row, "speech_source")
    version = _non_empty_string(row, "speech_dataset_version")
    license_id = _non_empty_string(row, "speech_license")

    if "wenetspeech" in dataset.casefold() or "wenetspeech" in source.casefold():
        raise ManifestError(
            f"line {_line(row)}: WenetSpeech requires explicit legal approval because "
            "the official dataset terms are non-commercial; it is not preapproved for demos"
        )
    expected = (
        COMMON_VOICE_DATASET,
        COMMON_VOICE_SOURCE,
        COMMON_VOICE_VERSION,
        "CC0-1.0",
    )
    if (dataset, source, version, license_id) != expected:
        raise ManifestError(
            f"line {_line(row)}: speech source is not preapproved; expected Common Voice "
            "26.0 zh-CN with its exact official data-card URL, corpus version, and CC0-1.0"
        )

    restrictions = require(row, "speech_usage_restrictions", list)
    if not all(isinstance(item, str) and item.strip() for item in restrictions):
        raise ManifestError(
            f"line {_line(row)}: speech_usage_restrictions must contain non-empty strings"
        )
    missing = COMMON_VOICE_RESTRICTIONS - set(restrictions)
    if missing:
        raise ManifestError(
            f"line {_line(row)}: Common Voice usage restrictions missing {sorted(missing)}"
        )


def _validate_noise_policy(row: dict[str, Any]) -> None:
    _https_url(row, "noise_source")
    _non_empty_string(row, "noise_clip_id")
    license_id = _non_empty_string(row, "noise_license")
    if license_id not in ALLOWED_NOISE_LICENSES:
        raise ManifestError(
            f"line {_line(row)}: noise_license {license_id!r} is not allowed; only "
            "per-file verified CC0-1.0 or CC-BY-4.0 audio is accepted (no NC, ND, "
            "academic-only, non-commercial, or unknown terms)"
        )
    verified = row.get("noise_license_verified")
    if verified is not True:
        raise ManifestError(
            f"line {_line(row)}: noise_license_verified must be true after per-file review"
        )
    verified_at = _non_empty_string(row, "noise_license_verified_at")
    try:
        verification_date = date.fromisoformat(verified_at)
    except ValueError as exc:
        raise ManifestError(
            f"line {_line(row)}: noise_license_verified_at must be YYYY-MM-DD"
        ) from exc
    if verification_date > datetime.now(UTC).date():
        raise ManifestError(f"line {_line(row)}: noise_license_verified_at cannot be in the future")
    attribution = require(row, "noise_attribution", str).strip()
    if license_id == "CC-BY-4.0" and not attribution:
        raise ManifestError(f"line {_line(row)}: CC-BY-4.0 noise requires noise_attribution")


def validate_source_rows(rows: list[dict[str, Any]], *, require_full_matrix: bool = True) -> None:
    """Validate provenance, licensing, split isolation, and the noise/SNR matrix."""

    recipes: set[tuple[str, str, str, float, int]] = set()
    speaker_splits: dict[str, set[str]] = {}
    test_combinations: set[tuple[str, float]] = set()
    for row in rows:
        missing_fields = sorted(REQUIRED_FIELDS - row.keys())
        if missing_fields:
            raise ManifestError(f"line {_line(row)}: missing required fields {missing_fields}")
        _validate_speech_policy(row)
        _validate_noise_policy(row)

        clip_id = _non_empty_string(row, "clip_id")
        transcript = _non_empty_string(row, "transcript")
        if not transcript:
            raise ManifestError(f"line {_line(row)}: transcript must not be empty")
        speaker_id = _non_empty_string(row, "anonymous_speaker_id")
        if not _ANONYMOUS_ID_RE.fullmatch(speaker_id) or "@" in speaker_id:
            raise ManifestError(
                f"line {_line(row)}: anonymous_speaker_id must be an opaque 1-64 "
                "character identifier"
            )
        split = _non_empty_string(row, "split")
        if split not in {"tuning", "test"}:
            raise ManifestError(f"line {_line(row)}: split must be 'tuning' or 'test'")
        speaker_splits.setdefault(speaker_id, set()).add(split)

        _non_empty_string(row, "speech_path")
        normalize_checksum(_non_empty_string(row, "checksum"), line=_line(row))
        _non_empty_string(row, "noise_path")
        category = _non_empty_string(row, "noise_category")
        if category not in NOISE_CATEGORIES:
            raise ManifestError(
                f"line {_line(row)}: noise_category must be one of {list(NOISE_CATEGORIES)}"
            )
        snr = row.get("snr_db")
        if isinstance(snr, bool) or not isinstance(snr, (int, float)):
            raise ManifestError(f"line {_line(row)}: snr_db must be numeric")
        snr_value = float(snr)
        if snr_value not in SNR_LEVELS_DB:
            raise ManifestError(f"line {_line(row)}: snr_db must be one of {list(SNR_LEVELS_DB)}")
        mix_seed = row.get("mix_seed")
        if isinstance(mix_seed, bool) or not isinstance(mix_seed, int) or mix_seed < 0:
            raise ManifestError(f"line {_line(row)}: mix_seed must be a non-negative integer")

        recipe = (
            clip_id,
            _non_empty_string(row, "noise_clip_id"),
            category,
            snr_value,
            mix_seed,
        )
        if recipe in recipes:
            raise ManifestError(f"line {_line(row)}: duplicate mixing recipe {recipe}")
        recipes.add(recipe)
        if split == "test":
            test_combinations.add((category, snr_value))

    leaked = sorted(
        speaker_id for speaker_id, splits in speaker_splits.items() if {"tuning", "test"} <= splits
    )
    if leaked:
        raise ManifestError(
            "speaker-disjoint violation: anonymous speakers appear in both tuning and "
            f"test splits: {leaked}"
        )

    if require_full_matrix:
        expected = {(category, snr) for category in NOISE_CATEGORIES for snr in SNR_LEVELS_DB}
        missing_combinations = sorted(expected - test_combinations)
        if missing_combinations:
            raise ManifestError(
                "test split does not cover the required noise/SNR matrix; missing "
                f"{missing_combinations}"
            )


def resolve_audio_path(manifest_path: Path, declared_path: str) -> Path:
    path = Path(declared_path).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path.resolve()


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as source:
            if source.getcomptype() != "NONE":
                raise ManifestError(f"unsupported compressed WAV: {path}")
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frame_count = source.getnframes()
            if channels not in {1, 2}:
                raise ManifestError(f"WAV must be mono or stereo: {path}")
            if sample_width != 2:
                raise ManifestError(f"WAV must use signed 16-bit PCM: {path}")
            if sample_rate <= 0 or frame_count <= 0:
                raise ManifestError(f"WAV has no usable audio frames: {path}")
            raw = source.readframes(frame_count)
    except ManifestError:
        raise
    except (EOFError, OSError, wave.Error) as exc:
        raise ManifestError(f"missing or corrupt WAV: {path}") from exc

    expected_bytes = frame_count * channels * sample_width
    if len(raw) != expected_bytes:
        raise ManifestError(f"truncated WAV payload: {path}")
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float64)
    if channels == 2:
        pcm = pcm.reshape(-1, 2).mean(axis=1)
    return pcm / 32768.0, sample_rate


def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return samples.copy()
    output_length = max(1, round(len(samples) * target_rate / source_rate))
    source_positions = np.arange(output_length, dtype=np.float64) * (source_rate / target_rate)
    source_positions = np.minimum(source_positions, len(samples) - 1)
    return np.interp(source_positions, np.arange(len(samples)), samples)


def _noise_segment(noise: np.ndarray, length: int, seed_material: str) -> np.ndarray:
    rng = random.Random(seed_material)
    if len(noise) >= length:
        start = rng.randrange(len(noise) - length + 1)
        return noise[start : start + length].copy()
    start = rng.randrange(len(noise))
    repeats = math.ceil((length + start) / len(noise))
    return np.tile(noise, repeats)[start : start + length].copy()


def _rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))


def mix_wav(
    speech_path: Path,
    noise_path: Path,
    output_path: Path,
    *,
    snr_db: float,
    mix_seed: int,
    target_sample_rate_hz: int = TARGET_SAMPLE_RATE_HZ,
) -> dict[str, Any]:
    """Create a deterministic PCM16 mixture and return measured component SNR."""

    speech, speech_rate = read_wav(speech_path)
    noise, noise_rate = read_wav(noise_path)
    speech = _resample(speech, speech_rate, target_sample_rate_hz)
    noise = _resample(noise, noise_rate, target_sample_rate_hz)
    seed_material = f"{mix_seed}:{sha256_file(speech_path)}:{sha256_file(noise_path)}:{snr_db}"
    noise = _noise_segment(noise, len(speech), seed_material)
    speech = speech - float(np.mean(speech))
    noise = noise - float(np.mean(noise))
    speech_rms = _rms(speech)
    noise_rms = _rms(noise)
    if speech_rms <= 1e-9:
        raise ManifestError(f"speech WAV is silent after DC removal: {speech_path}")
    if noise_rms <= 1e-9:
        raise ManifestError(f"noise WAV is silent after DC removal: {noise_path}")

    target_noise_rms = speech_rms / (10 ** (snr_db / 20.0))
    noise_component = noise * (target_noise_rms / noise_rms)
    peak = float(np.max(np.abs(speech + noise_component)))
    common_gain = min(1.0, 0.98 / peak) if peak > 0 else 1.0
    speech_component = speech * common_gain
    noise_component *= common_gain
    mixture = speech_component + noise_component
    actual_snr_db = 20 * math.log10(_rms(speech_component) / _rms(noise_component))
    if abs(actual_snr_db - snr_db) > 0.05:
        raise ManifestError(
            f"internal SNR verification failed: target={snr_db}, actual={actual_snr_db}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    pcm = np.clip(np.rint(mixture * 32767.0), -32768, 32767).astype("<i2")
    try:
        with wave.open(str(temporary_path), "wb") as destination:
            destination.setnchannels(1)
            destination.setsampwidth(2)
            destination.setframerate(target_sample_rate_hz)
            destination.writeframes(pcm.tobytes())
        temporary_path.replace(output_path)
    except OSError as exc:
        raise ManifestError(f"cannot write mixed WAV: {output_path}") from exc
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    return {
        "actual_snr_db": round(actual_snr_db, 6),
        "sample_rate_hz": target_sample_rate_hz,
        "audio_duration_ms": round(len(pcm) * 1000 / target_sample_rate_hz, 3),
        "common_gain": round(common_gain, 9),
        "mixed_audio_checksum": sha256_file(output_path),
    }


def _source_summary(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    speech = {
        (
            row["speech_dataset"],
            row["speech_dataset_version"],
            row["speech_source"],
            row["speech_license"],
        )
        for row in rows
    }
    noise = {
        (
            row["noise_clip_id"],
            row["noise_source"],
            row["noise_license"],
            row["noise_attribution"],
            row["noise_license_verified_at"],
        )
        for row in rows
    }
    return {
        "speech": [
            {
                "dataset": item[0],
                "version": item[1],
                "source": item[2],
                "license": item[3],
                "usage_restrictions": sorted(COMMON_VOICE_RESTRICTIONS),
            }
            for item in sorted(speech)
        ],
        "noise": [
            {
                "clip_id": item[0],
                "source": item[1],
                "license": item[2],
                "attribution": item[3],
                "license_verified_at": item[4],
            }
            for item in sorted(noise)
        ],
    }


def _base_report(rows: list[dict[str, Any]], command: str) -> dict[str, Any]:
    return {
        "report_kind": "public_human_speech_simulated_campus_noise",
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_count": len(rows),
        "speaker_count": len({row["anonymous_speaker_id"] for row in rows}),
        "data_sources": _source_summary(rows),
        "noise_categories": list(NOISE_CATEGORIES),
        "snr_levels_db": list(SNR_LEVELS_DB),
        "mix_method": MIX_ALGORITHM,
        "reproduction_command": command,
        "limitations": [
            "Public human speech plus simulated campus noise is not a real campus field test.",
            "Common Voice speaker identification and dataset re-hosting/re-sharing are prohibited.",
            "Only aggregate results may be used; source and noise licenses still apply.",
            "Prepared audio contains no inference result and cannot support accuracy claims.",
        ],
    }


def prepare_public_demo(
    manifest_path: Path,
    output_dir: Path,
    *,
    require_full_matrix: bool = True,
) -> dict[str, Any]:
    command = f"python scripts/prepare_public_asr_demo.py {manifest_path} --output-dir {output_dir}"
    if not manifest_path.is_file():
        return {
            "report_kind": "public_human_speech_simulated_campus_noise",
            "status": "NOT_RUN",
            "reason": "SOURCE_MANIFEST_MISSING",
            "missing_files": [str(manifest_path)],
            "reproduction_command": command,
            "limitations": ["No metrics were generated because the source manifest is missing."],
        }

    rows = read_jsonl(manifest_path)
    validate_source_rows(rows, require_full_matrix=require_full_matrix)
    report = _base_report(rows, command)

    missing_files: set[str] = set()
    resolved: list[tuple[Path, Path]] = []
    for row in rows:
        speech_path = resolve_audio_path(manifest_path, row["speech_path"])
        noise_path = resolve_audio_path(manifest_path, row["noise_path"])
        resolved.append((speech_path, noise_path))
        for path in (speech_path, noise_path):
            if not path.is_file():
                missing_files.add(str(path))
    if missing_files:
        report.update(
            {
                "status": "NOT_RUN",
                "reason": "INPUT_AUDIO_MISSING",
                "missing_files": sorted(missing_files),
            }
        )
        return report

    checked_paths: set[Path] = set()
    for (speech_path, noise_path), row in zip(resolved, rows, strict=True):
        for path in (speech_path, noise_path):
            if path not in checked_paths:
                read_wav(path)
                checked_paths.add(path)
        expected_checksum = normalize_checksum(row["checksum"], line=_line(row))
        actual_checksum = sha256_file(speech_path)
        if actual_checksum != expected_checksum:
            raise ManifestError(
                f"line {_line(row)}: speech checksum mismatch for {speech_path}; "
                f"expected {expected_checksum}, got {actual_checksum}"
            )

    audio_dir = output_dir / "audio"
    generated_rows: list[dict[str, Any]] = []
    for row, (speech_path, noise_path) in zip(rows, resolved, strict=True):
        recipe_key = json.dumps(
            {
                "clip_id": row["clip_id"],
                "noise_clip_id": row["noise_clip_id"],
                "noise_category": row["noise_category"],
                "snr_db": float(row["snr_db"]),
                "mix_seed": row["mix_seed"],
            },
            sort_keys=True,
        )
        recipe_digest = hashlib.sha256(recipe_key.encode()).hexdigest()[:16]
        output_path = audio_dir / f"mix-{recipe_digest}.wav"
        statistics = mix_wav(
            speech_path,
            noise_path,
            output_path,
            snr_db=float(row["snr_db"]),
            mix_seed=row["mix_seed"],
        )
        generated = {key: value for key, value in row.items() if key != "_line"}
        generated.update(
            {
                "sample_id": f"{row['clip_id']}:{recipe_digest}",
                "reference_text": row["transcript"],
                "mixed_audio_path": str(output_path.relative_to(output_dir)),
                "speech_audio_checksum": sha256_file(speech_path),
                "noise_audio_checksum": sha256_file(noise_path),
                "mix_algorithm": MIX_ALGORITHM,
                **statistics,
            }
        )
        generated_rows.append(generated)

    output_dir.mkdir(parents=True, exist_ok=True)
    generated_manifest = output_dir / "mixed-manifest.jsonl"
    generated_manifest.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in generated_rows
        ),
        encoding="utf-8",
    )
    report.update(
        {
            "status": "PREPARED",
            "mixed_manifest": str(generated_manifest),
            "mixed_audio_count": len(generated_rows),
            "metrics_status": "NOT_RUN",
            "metrics_reason": "ASR_INFERENCE_NOT_SUPPLIED",
        }
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate licensed public speech/noise metadata and create deterministic "
            "simulated-campus-noise WAV files without downloading data or models."
        )
    )
    parser.add_argument("manifest", type=Path, help="source recipe manifest in JSONL")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/evaluation/generated/public-asr-demo"),
    )
    parser.add_argument("--json-report", type=Path)
    args = parser.parse_args()
    try:
        report = prepare_public_demo(args.manifest, args.output_dir)
    except ManifestError as exc:
        parser.error(str(exc))
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
