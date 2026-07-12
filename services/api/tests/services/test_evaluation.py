from scripts.evaluate_asr import evaluate as evaluate_asr
from scripts.evaluate_intent import evaluate as evaluate_intent
from scripts.evaluate_reliability import evaluate as evaluate_reliability


def test_asr_metrics_are_computed_from_raw_transcripts_and_timings() -> None:
    rows = [
        {
            "system": "raw_asr",
            "reference_text": "机器学习",
            "hypothesis_text": "机器学期",
            "reference_keywords": ["机器学习"],
            "reference_terms": [],
            "latency_ms": 500.0,
            "audio_duration_ms": 2000.0,
            "_line": 1,
        },
        {
            "system": "full_correction",
            "reference_text": "机器学习",
            "hypothesis_text": "机器学习",
            "reference_keywords": ["机器学习"],
            "reference_terms": [],
            "latency_ms": 600.0,
            "audio_duration_ms": 2000.0,
            "_line": 2,
        },
    ]

    report = evaluate_asr(rows)

    assert report["systems"]["raw_asr"]["cer"] == 0.25
    assert report["systems"]["raw_asr"]["campus_keyword_accuracy"] == 0.0
    assert report["systems"]["full_correction"]["cer"] == 0.0
    assert report["systems"]["full_correction"]["rtf"] == 0.3


def test_intent_metrics_compare_actual_raw_objects() -> None:
    rows = [
        {
            "expected": {
                "intent": "create_event",
                "slots": {"date": "2026-07-18", "start_time": "09:00", "course": "机器学习"},
                "missing_fields": [],
            },
            "actual": {
                "intent": "create_event",
                "slots": {"date": "2026-07-18", "start_time": "10:00", "course": "机器学习"},
                "missing_fields": [],
            },
            "_line": 1,
        }
    ]

    report = evaluate_intent(rows)

    assert report["intent_accuracy"] == 1.0
    assert report["date_accuracy"] == 1.0
    assert report["time_accuracy"] == 0.0
    assert report["course_accuracy"] == 1.0


def test_reliability_metrics_require_expected_and_observed_flags() -> None:
    rows = [
        {
            "expected": {
                "should_succeed": False,
                "has_conflict": True,
                "is_duplicate": False,
                "should_clarify": True,
                "high_risk": True,
                "save_failed": False,
            },
            "actual": {
                "success": False,
                "conflict_detected": True,
                "duplicate_detected": False,
                "asked_clarification": True,
                "confirmation_requested": True,
                "save_failure_detected": False,
            },
            "_line": 1,
        }
    ]

    report = evaluate_reliability(rows)

    assert report["conflict_detection_rate"] == 1.0
    assert report["required_clarification_rate"] == 1.0
    assert report["high_risk_confirmation_rate"] == 1.0
