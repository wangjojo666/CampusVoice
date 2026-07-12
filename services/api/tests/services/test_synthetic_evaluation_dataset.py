import hashlib
import json
import wave
from pathlib import Path

from scripts.generate_synthetic_evaluation import generate_dataset


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_tone_dataset_is_complete_private_and_reproducible(tmp_path: Path) -> None:
    output = tmp_path / "synthetic"
    first = generate_dataset(output, count=12, seed=42, engine="tone")
    manifest = _rows(output / "dataset.jsonl")
    template = _rows(output / "asr.template.jsonl")

    assert first["sample_count"] == 12
    assert first["asr_template_row_count"] == 48
    assert len(manifest) == 12
    assert len(template) == 48
    assert len({row["reference_text"] for row in manifest}) == 12
    assert {row["category"] for row in manifest} == {
        "ordinary_task",
        "date_time",
        "campus_course",
        "ai_bilingual_term",
        "campus_notice_query",
        "noisy_colloquial",
    }
    assert all(row["hypothesis_text"] is None for row in template)
    assert all(row["template_only"] is True for row in template)

    first_audio = output / str(manifest[0]["audio_path"])
    with wave.open(str(first_audio), "rb") as audio:
        assert audio.getframerate() == 16_000
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getnframes() > 0
    original_digest = hashlib.sha256(first_audio.read_bytes()).hexdigest()

    generate_dataset(output, count=12, seed=42, engine="tone", force=True)
    regenerated_manifest = _rows(output / "dataset.jsonl")
    regenerated_audio = output / str(regenerated_manifest[0]["audio_path"])
    assert hashlib.sha256(regenerated_audio.read_bytes()).hexdigest() == original_digest
