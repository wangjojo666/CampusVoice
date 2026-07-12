import argparse
import hashlib
import json
import math
import os
import random
import shutil
import struct
import subprocess
import sys
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

AudioEngine = Literal["auto", "sapi", "tone"]


@dataclass(frozen=True)
class Sample:
    sample_id: str
    category: str
    text: str
    keywords: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    noise_profile: str = "clean"


COURSES = ("机器学习", "数据结构", "高等数学", "大学英语", "计算机视觉", "自然语言处理")
AI_TERMS = (
    "Transformer",
    "PyTorch",
    "Diffusion Model",
    "Reinforcement Learning",
    "Large Language Model",
    "Retrieval Augmented Generation",
)
DAYS = ("今天", "明天", "后天", "本周五", "下周一", "下周三")
TIMES = ("上午八点", "上午九点半", "中午十二点", "下午两点", "晚上七点", "晚上九点")
NOTICE_TOPICS = ("奖学金", "选课", "期末考试", "图书馆开放", "创新竞赛", "宿舍调整")


def _task_text(index: int) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
    course = COURSES[index % len(COURSES)]
    action = ("复习", "整理", "完成", "检查", "提交", "预习")[
        (index // len(COURSES)) % 6
    ]
    object_name = ("第一章笔记", "课程作业", "实验报告", "错题集", "课堂讲义")[
        (index // (len(COURSES) * 6)) % 5
    ]
    return f"提醒我{action}{course}的{object_name}", (course,), (), "clean"


def _datetime_text(index: int) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
    day = DAYS[index % len(DAYS)]
    time = TIMES[(index // len(DAYS)) % len(TIMES)]
    course = COURSES[(index * 5 + index // len(DAYS)) % len(COURSES)]
    return f"{day}{time}提醒我参加{course}答疑", (course,), (), "clean"


def _course_text(index: int) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
    course = COURSES[index % len(COURSES)]
    room = ("A302", "B201", "实验楼 405", "线上会议室")[(index // len(COURSES)) % 4]
    reminder = ("三十分钟", "一天")[(index // (len(COURSES) * 4)) % 2]
    return (
        f"把{course}课程安排到{room}并提前{reminder}提醒",
        (course, room),
        (),
        "clean",
    )


def _ai_term_text(index: int) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
    first = AI_TERMS[index % len(AI_TERMS)]
    second = AI_TERMS[
        (index % len(AI_TERMS) + index // len(AI_TERMS) + 1) % len(AI_TERMS)
    ]
    return f"今晚复习{first}和{second}的核心概念", (), (first, second), "clean"


def _notice_text(index: int) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
    topic = NOTICE_TOPICS[index % len(NOTICE_TOPICS)]
    question = (
        "什么时候截止",
        "适用于哪些同学",
        "需要提交什么材料",
        "最新版本有什么变化",
    )[(index // len(NOTICE_TOPICS)) % 4]
    return f"帮我查一下{topic}通知，{question}", (topic,), (), "clean"


def _colloquial_text(index: int) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
    course = COURSES[index % len(COURSES)]
    filler = ("嗯那个", "麻烦帮我", "对了", "我想一下", "就是那个")[(index // 6) % 5]
    return (
        f"{filler}，{DAYS[index % len(DAYS)]}{TIMES[index % len(TIMES)]}提醒我去上{course}，别忘了啊",
        (course,),
        (),
        "deterministic_low_noise",
    )


CATEGORY_BUILDERS: tuple[
    tuple[
        str, float, Callable[[int], tuple[str, tuple[str, ...], tuple[str, ...], str]]
    ],
    ...,
] = (
    ("ordinary_task", 0.225, _task_text),
    ("date_time", 0.2125, _datetime_text),
    ("campus_course", 0.1875, _course_text),
    ("ai_bilingual_term", 0.15, _ai_term_text),
    ("campus_notice_query", 0.1375, _notice_text),
    ("noisy_colloquial", 0.0875, _colloquial_text),
)


def _category_counts(count: int) -> list[int]:
    raw = [count * weight for _, weight, _ in CATEGORY_BUILDERS]
    allocated = [math.floor(value) for value in raw]
    remainder = count - sum(allocated)
    order = sorted(
        range(len(raw)), key=lambda item: raw[item] - allocated[item], reverse=True
    )
    for index in order[:remainder]:
        allocated[index] += 1
    return allocated


def build_samples(count: int, seed: int) -> list[Sample]:
    if count < len(CATEGORY_BUILDERS):
        raise ValueError(f"count must be at least {len(CATEGORY_BUILDERS)}")
    samples: list[Sample] = []
    for (category, _, builder), category_count in zip(
        CATEGORY_BUILDERS, _category_counts(count), strict=True
    ):
        for index in range(category_count):
            text, keywords, terms, noise_profile = builder(index)
            samples.append(
                Sample(
                    sample_id="pending",
                    category=category,
                    text=text,
                    keywords=keywords,
                    terms=terms,
                    noise_profile=noise_profile,
                )
            )
    random.Random(seed).shuffle(samples)
    return [
        Sample(
            sample_id=f"synthetic-{index:03d}",
            category=sample.category,
            text=sample.text,
            keywords=sample.keywords,
            terms=sample.terms,
            noise_profile=sample.noise_profile,
        )
        for index, sample in enumerate(samples, start=1)
    ]


def _tone_audio(
    sample: Sample, path: Path, seed: int, sample_rate: int = 16_000
) -> None:
    rng = random.Random(f"{seed}:{sample.sample_id}")
    pcm: list[int] = [0] * int(sample_rate * 0.08)
    tone_samples = int(sample_rate * 0.018)
    gap_samples = int(sample_rate * 0.004)
    phase = 0.0
    for character in sample.text:
        frequency = 180 + (ord(character) % 31) * 18
        for offset in range(tone_samples):
            envelope = min(1.0, offset / 35, (tone_samples - offset) / 35)
            value = 7_500 * envelope * math.sin(phase)
            phase += 2 * math.pi * frequency / sample_rate
            if sample.noise_profile != "clean":
                value += rng.uniform(-650, 650)
            pcm.append(max(-32_768, min(32_767, round(value))))
        pcm.extend([0] * gap_samples)
    pcm.extend([0] * int(sample_rate * 0.1))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(struct.pack(f"<{len(pcm)}h", *pcm))


def _add_low_noise(sample: Sample, path: Path, seed: int) -> None:
    with wave.open(str(path), "rb") as source:
        parameters = source.getparams()
        frames = source.readframes(source.getnframes())
    if parameters.sampwidth != 2:
        raise RuntimeError(f"expected 16-bit PCM before noise mixing: {path}")
    values = struct.unpack(f"<{len(frames) // 2}h", frames)
    rng = random.Random(f"noise:{seed}:{sample.sample_id}")
    mixed = [
        max(-32_768, min(32_767, value + rng.randint(-420, 420))) for value in values
    ]
    with wave.open(str(path), "wb") as output:
        output.setparams(parameters)
        output.writeframes(struct.pack(f"<{len(mixed)}h", *mixed))


def _sapi_audio(samples: list[Sample], audio_dir: Path) -> str:
    if os.name != "nt":
        raise RuntimeError("SAPI synthesis is available only on Windows")
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        raise RuntimeError("PowerShell is required for Windows SAPI synthesis")
    payload_path = audio_dir.parent / ".sapi-input.json"
    script_path = audio_dir.parent / ".sapi-generate.ps1"
    payload_path.write_text(
        json.dumps(
            [
                {
                    "text": sample.text,
                    "path": str(audio_dir / f"{sample.sample_id}.wav"),
                }
                for sample in samples
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8-sig",
    )
    script_path.write_text(
        """$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$items = Get-Content -LiteralPath $args[0] -Raw -Encoding UTF8 | ConvertFrom-Json
$synth = [System.Speech.Synthesis.SpeechSynthesizer]::new()
$voice = $synth.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'zh-*' } | Select-Object -First 1
if ($null -eq $voice) { throw 'No enabled zh-* SAPI voice is installed.' }
$synth.SelectVoice($voice.VoiceInfo.Name)
$format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($item in $items) {
  $synth.SetOutputToWaveFile([string]$item.path, $format)
  $synth.Speak([string]$item.text)
}
$synth.Dispose()
Write-Output $voice.VoiceInfo.Name
""",
        encoding="utf-8-sig",
    )
    try:
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                str(payload_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=max(180, len(samples) * 8),
        )
        return result.stdout.strip().splitlines()[-1]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        stderr = getattr(exc, "stderr", "") or str(exc)
        raise RuntimeError(f"SAPI generation failed: {stderr.strip()}") from exc
    finally:
        payload_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)


def _wave_metadata(path: Path) -> dict[str, int | float]:
    with wave.open(str(path), "rb") as audio:
        frames = audio.getnframes()
        rate = audio.getframerate()
        return {
            "sample_rate_hz": rate,
            "channels": audio.getnchannels(),
            "sample_width_bytes": audio.getsampwidth(),
            "duration_ms": round(frames / rate * 1000, 3),
        }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def generate_dataset(
    output: Path,
    *,
    count: int = 160,
    seed: int = 20_260_712,
    engine: AudioEngine = "auto",
    force: bool = False,
) -> dict[str, Any]:
    manifest_path = output / "dataset.jsonl"
    if manifest_path.exists() and not force:
        raise FileExistsError(
            f"{manifest_path} exists; pass --force to replace generated assets"
        )
    output.mkdir(parents=True, exist_ok=True)
    audio_dir = output / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    samples = build_samples(count, seed)
    expected_names = {f"{sample.sample_id}.wav" for sample in samples}
    if force:
        for stale in audio_dir.glob("synthetic-*.wav"):
            if stale.name not in expected_names:
                stale.unlink()

    actual_engine = engine
    engine_detail = ""
    if engine in {"auto", "sapi"}:
        try:
            engine_detail = _sapi_audio(samples, audio_dir)
            actual_engine = "sapi"
            for sample in samples:
                if sample.noise_profile != "clean":
                    _add_low_noise(sample, audio_dir / f"{sample.sample_id}.wav", seed)
        except RuntimeError as exc:
            if engine == "sapi":
                raise
            print(
                f"warning: {exc}; falling back to deterministic tone carrier",
                file=sys.stderr,
            )
            actual_engine = "tone"
    if actual_engine == "tone":
        for sample in samples:
            _tone_audio(sample, audio_dir / f"{sample.sample_id}.wav", seed)
        engine_detail = "deterministic_unicode_tone_carrier_v1"

    rows: list[dict[str, Any]] = []
    template_rows: list[dict[str, Any]] = []
    systems = ("raw_asr", "static_hotwords", "hotwords_context", "full_correction")
    for sample in samples:
        audio_path = audio_dir / f"{sample.sample_id}.wav"
        metadata = _wave_metadata(audio_path)
        relative_audio = audio_path.relative_to(output).as_posix()
        digest = hashlib.sha256(audio_path.read_bytes()).hexdigest()
        row = {
            "sample_id": sample.sample_id,
            "audio_path": relative_audio,
            "reference_text": sample.text,
            "reference_keywords": list(sample.keywords),
            "reference_terms": list(sample.terms),
            "category": sample.category,
            "noise_profile": sample.noise_profile,
            "audio_engine": actual_engine,
            "audio_engine_detail": engine_detail,
            "sha256": digest,
            **metadata,
        }
        rows.append(row)
        for system in systems:
            template_rows.append(
                {
                    "sample_id": sample.sample_id,
                    "system": system,
                    "audio_path": relative_audio,
                    "reference_text": sample.text,
                    "reference_keywords": list(sample.keywords),
                    "reference_terms": list(sample.terms),
                    "hypothesis_text": None,
                    "latency_ms": None,
                    "audio_duration_ms": metadata["duration_ms"],
                    "template_only": True,
                }
            )

    _write_jsonl(manifest_path, rows)
    _write_jsonl(output / "asr.template.jsonl", template_rows)
    counts = {
        category: sum(sample.category == category for sample in samples)
        for category, _, _ in CATEGORY_BUILDERS
    }
    card = f"""# CampusVoice generated evaluation dataset

- Samples: {len(samples)}
- Seed: {seed}
- Audio engine: `{actual_engine}` (`{engine_detail}`)
- Categories: `{json.dumps(counts, ensure_ascii=False, sort_keys=True)}`

## Authorization and validity boundary

All reference sentences are deterministic synthetic project text and contain no real student
records. Audio is generated locally and is ignored by Git. Do not redistribute SAPI output until
you have verified the installed voice's license. The `tone` engine is project-generated PCM, but
this repository currently grants no redistribution license; do not redistribute it unless an
explicit license is added. It is a pipeline carrier rather than intelligible speech and **must
not be used to claim ASR model quality**.

`dataset.jsonl` is the source manifest. `asr.template.jsonl` deliberately contains null
hypotheses and timings; run the four actual inference configurations and replace those fields
before passing the observations to `scripts/evaluate_asr.py`. Never report the template as an
experiment result.
"""
    (output / "DATASET_CARD.md").write_text(card, encoding="utf-8")
    summary = {
        "output": str(output.resolve()),
        "sample_count": len(samples),
        "asr_template_row_count": len(template_rows),
        "seed": seed,
        "audio_engine": actual_engine,
        "audio_engine_detail": engine_detail,
        "categories": counts,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a deterministic, privacy-safe CampusVoice evaluation corpus."
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/evaluation/generated/synthetic-160")
    )
    parser.add_argument("--count", type=int, default=160)
    parser.add_argument("--seed", type=int, default=20_260_712)
    parser.add_argument(
        "--engine",
        choices=("auto", "sapi", "tone"),
        default="auto",
        help="auto uses a local Windows zh-* SAPI voice or falls back to a tone carrier",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        summary = generate_dataset(
            args.output,
            count=args.count,
            seed=args.seed,
            engine=args.engine,
            force=args.force,
        )
    except (FileExistsError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
