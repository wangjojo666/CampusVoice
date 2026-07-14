# CampusVoice evaluation manifests

Evaluation results are generated from raw JSONL observations. Do not put aggregate metrics in
these manifests and do not edit files under `../results/` by hand.

## ASR (`asr.jsonl`)

One row is one inference from one real provider/configuration:

```json
{
  "sample_id": "voice-001",
  "system": "raw_asr",
  "reference_text": "...",
  "hypothesis_text": "...",
  "reference_keywords": ["机器学习"],
  "reference_terms": ["Transformer"],
  "latency_ms": 420.1,
  "audio_duration_ms": 2800.0
}
```

Use the same `sample_id` for the four systems `raw_asr`, `static_hotwords`,
`hotwords_context`, and `full_correction`. The evaluator computes CER, reference keyword and
Chinese/English term accuracy, average/P95 latency, and RTF directly from these rows. The
generated report also lists missing systems and incompletely paired sample IDs so an uneven
comparison is visible rather than silently accepted.

## Public human speech + simulated campus noise

This optional local workflow prepares demo evidence from licensed public human speech and
per-file licensed environmental sound. It does not download audio or models, and it must not be
described as a real campus field test. The authoritative source decisions and marketing limits
are in [`docs/evaluation/public-audio-licensing.md`](../../../docs/evaluation/public-audio-licensing.md).

The source recipe manifest is JSONL. Every row repeats its provenance so a mixed file can be
audited without relying on a folder name:

```json
{
  "speech_dataset": "Common Voice Scripted Speech 26.0 - Chinese (China)",
  "speech_source": "https://mozilladatacollective.com/datasets/cmqim47x700tunq074za20dq1",
  "speech_dataset_version": "cv-corpus-26.0-2026-06-12",
  "speech_license": "CC0-1.0",
  "speech_usage_restrictions": ["no_speaker_identification", "no_rehosting", "no_resharing"],
  "clip_id": "cv-zh-CN-example-001",
  "transcript": "周五上午九点有机器学习考试",
  "anonymous_speaker_id": "cv-speaker-001",
  "split": "test",
  "speech_path": "C:/licensed-audio/common-voice/example-001.wav",
  "checksum": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "noise_source": "https://freesound.org/s/000000/",
  "noise_clip_id": "freesound-000000",
  "noise_license": "CC-BY-4.0",
  "noise_license_verified": true,
  "noise_license_verified_at": "2026-07-14",
  "noise_attribution": "Title by uploader, source URL, CC BY 4.0 license URL; converted to PCM16 WAV and mixed",
  "noise_category": "cafeteria",
  "noise_path": "C:/licensed-audio/noise/cafeteria-001.wav",
  "snr_db": 20,
  "mix_seed": 20260714
}
```

The row above is structural documentation, not a licensed or runnable sample: replace every
placeholder path, sound page, ID, attribution, and checksum with locally verified values. Both
audio paths must be readable, non-empty signed 16-bit PCM WAV files (mono or stereo). `checksum`
is the SHA-256 of the declared speech WAV. Keep all source and generated audio outside tracked
Git paths.

Only the exact Common Voice 26.0 `zh-CN` data card and `CC0-1.0` tuple shown above is preapproved
for speech. `speech_usage_restrictions` must retain all three entries. WenetSpeech is rejected by
default because its official data terms are non-commercial and its official page says
WenetSpeech does not own the underlying audio rights. Noise must be verified per file as
`CC0-1.0` or `CC-BY-4.0`; CC BY rows
must include complete attribution (creator/uploader, title, source URL, license URL, and a
modification note). NC, ND, academic-only, unknown, inferred, or source-page-mismatched terms are
rejected.

Use only opaque `anonymous_speaker_id` values and never attempt to identify a Common Voice
speaker. A speaker may appear in `tuning` or `test`, never both. A complete preparation manifest
must cover the 3 × 3 matrix of `cafeteria`, `corridor`, and `outdoor` at 20, 10, and 5 dB SNR.
Identical inputs, SNR, and `mix_seed` produce the same mixture.

Prepare the local audio and write an optional machine-readable preparation report:

```powershell
python scripts/prepare_public_asr_demo.py C:\path\to\public-asr-source.jsonl `
  --output-dir data/evaluation/generated/public-asr-demo `
  --json-report data/evaluation/results/public-asr-demo-preparation.json
```

A missing source manifest or missing input audio returns a truthful `NOT_RUN` report. Invalid
provenance, licenses, speaker leakage, checksums, WAV data, categories, SNR values, or matrix
coverage fail validation. Successful preparation writes `mixed-manifest.jsonl` and PCM16 mixed
audio, but sets `metrics_status` to `NOT_RUN` until real inference observations are supplied.

The machine-readable contracts are
[`public-asr-demo-source.schema.json`](public-asr-demo-source.schema.json) and
[`public-asr-demo-inference.schema.json`](public-asr-demo-inference.schema.json). The checked-in
[`examples/public-asr-demo-source.jsonl`](examples/public-asr-demo-source.jsonl) is provenance and
schema documentation only: its audio paths deliberately do not exist, so preparation must report
`NOT_RUN` and cannot produce mixtures or metrics.

### Inference observations and reports

Create `inference.jsonl` next to the generated `mixed-manifest.jsonl`. For every system under
comparison, copy each prepared mixed-manifest row and add:

- `system`, `model_name`, exact `model_version`, `device`, and the complete
  `inference_parameters` object. These values must remain fixed within one system.
- `inference_status`: `COMPLETED`, `FAILED`, or `NOT_RUN`.
- `hypothesis_text` and `latency_ms`: measured values for `COMPLETED`; a null/empty hypothesis and
  optional measured latency for `FAILED`; both null for `NOT_RUN`.
- Optional `reference_keywords` and `reference_terms` lists for the corresponding recall metrics.

Use `funasr`, `funasr_hotwords`, and `whisper` (or equally explicit stable names) when those three
existing configurations are actually run. Do not copy a hypothesis across systems or mark a
planned system completed. A failed attempt remains in CER, sentence accuracy, and failure-rate
denominators; `NOT_RUN` is excluded from metrics. If every row is `NOT_RUN`, no metric block is
created.

```powershell
$testDate = Get-Date -Format yyyy-MM-dd
python scripts/evaluate_public_asr_demo.py `
  data/evaluation/generated/public-asr-demo/inference.jsonl `
  --json-report data/evaluation/results/public-asr-demo.json `
  --markdown-report data/evaluation/results/public-asr-demo.md `
  --test-date $testDate
```

The evaluator revalidates provenance, speaker isolation, the full noise/SNR matrix, mixed WAV
checksums, actual SNR tolerance, and fixed model metadata. Its paired reports share a fingerprint
and record data sources/licenses, sample and speaker counts, model/hardware metadata, mixing
method, test date, limitations, and the reproduction command. When observations exist, it reports
CER, complete-sentence accuracy, failure rate, and P50/P95 latency overall and grouped by system,
dataset, noise category, and SNR.

## Intent (`intent.jsonl`)

Each row contains the expected annotation and the unedited parser output:

```json
{
  "sample_id": "intent-001",
  "text": "...",
  "expected": {
    "intent": "create_event",
    "slots": { "date": "2026-07-18", "start_time": "09:00", "course": "机器学习" },
    "missing_fields": []
  },
  "actual": {
    "intent": "create_event",
    "slots": { "date": "2026-07-18", "start_time": "09:00", "course": "机器学习" },
    "missing_fields": []
  }
}
```

## Reliable execution (`reliability.jsonl`)

Every expected and actual flag is required so a missing observation cannot silently improve a
metric:

```json
{
  "scenario_id": "reliable-001",
  "expected": {
    "should_succeed": true,
    "has_conflict": false,
    "is_duplicate": false,
    "should_clarify": false,
    "high_risk": false,
    "save_failed": false
  },
  "actual": {
    "success": true,
    "conflict_detected": false,
    "duplicate_detected": false,
    "asked_clarification": false,
    "confirmation_requested": false,
    "save_failure_detected": false
  }
}
```

Run from the repository root:

```powershell
python scripts/evaluate_asr.py data/evaluation/manifests/asr.jsonl --output data/evaluation/results/asr.json
python scripts/evaluate_intent.py data/evaluation/manifests/intent.jsonl --output data/evaluation/results/intent.json
python scripts/evaluate_reliability.py data/evaluation/manifests/reliability.jsonl --output data/evaluation/results/reliability.json
```

The checked-in files under `examples/` are synthetic smoke-test inputs. They prove that the
command line and metric pipeline run, but they are not benchmark results and must not be mixed
with the target set of 150 to 200 authorized recordings. To exercise all three evaluators:

```powershell
python scripts/evaluate_asr.py data/evaluation/manifests/examples/asr.jsonl
python scripts/evaluate_intent.py data/evaluation/manifests/examples/intent.jsonl
python scripts/evaluate_reliability.py data/evaluation/manifests/examples/reliability.jsonl
```

## Reproducible 160-file corpus generator

From the repository root, generate 160 local WAV files, a one-row-per-audio source manifest, a
four-system inference template, a summary, and a dataset card:

```powershell
python scripts/generate_synthetic_evaluation.py
```

The default `auto` engine uses an installed Windows `zh-*` SAPI voice when available. If no such
voice exists, it falls back to a deterministic Unicode tone carrier. The tone carrier is useful
for validating audio ingestion and manifest plumbing, but it is not intelligible speech and must
never be presented as ASR quality evidence. Use `--engine sapi` to fail instead of falling back,
or `--engine tone` for a fully deterministic cross-platform pipeline fixture. Generated files are
Git-ignored under `data/evaluation/generated/synthetic-160`; pass `--force` to reproduce them with
the same seed.

All reference sentences are synthetic and contain no real student records. Locally generated SAPI
audio must not be redistributed until the installed voice's license has been checked. The
generated `asr.template.jsonl` intentionally contains null hypotheses and timings: replace them
with raw observations from each of the four real inference configurations before evaluation.
