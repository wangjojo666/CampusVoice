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
