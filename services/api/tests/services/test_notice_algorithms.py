from datetime import UTC, datetime, timedelta

from app.models.entities import (
    CalendarEvent,
    ImpactCase,
    ImpactMigrationItem,
    ImpactMigrationPlan,
    NoticeClaim,
    Task,
)
from app.models.enums import SourceType, TaskPriority, TaskStatus
from app.services.notices.claims import (
    extract_claims,
    normalize_course,
    normalize_grade,
    normalize_major,
    normalize_semantic_text,
)
from app.services.notices.service import (
    _apply_patch,
    _as_datetime,
    _canonical_value,
    _change_severity,
    _entity_depends_on_claim,
    _impact_patch,
    _impact_view,
    _migration_item_view,
    _normalize_title,
    _operation_receipt,
    _patched_snapshot,
    _plan_signature,
    _plan_view,
    _required_confirmations,
    _restore_snapshot,
    _snapshot,
    _snapshots_match,
    _stable_json,
    _verification_time,
)

NOW = datetime(2026, 7, 13, tzinfo=UTC)


def test_legacy_receipt_fallback_requires_an_exact_operation_match() -> None:
    separated = {"operation": "execute", "marker": "separated"}
    legacy_execute = {
        "operation": "execute",
        "verified": True,
        "verified_count": 1,
        "total_count": 1,
        "status": "verified",
        "verified_at": NOW.isoformat(),
    }
    legacy_undo = legacy_execute | {"operation": "undo", "status": "undone"}

    assert _operation_receipt(separated, legacy_undo, "execute", receipt_type="plan") is separated
    assert _operation_receipt({}, legacy_execute, "execute", receipt_type="plan") is legacy_execute
    assert _operation_receipt({}, legacy_undo, "execute", receipt_type="plan") == {}
    assert _operation_receipt({}, {"operation": "Execute"}, "execute", receipt_type="plan") == {}
    assert _operation_receipt({}, {"operation": "execute"}, "execute", receipt_type="plan") == {}
    assert _operation_receipt({}, {"verified": True}, "execute", receipt_type="plan") == {}
    legacy_item = {
        "operation": "execute",
        "verified": True,
        "verified_at": NOW.isoformat(),
        "expected_snapshot": {"version": 1},
        "database_snapshot": {"version": 2},
    }
    assert _operation_receipt({}, legacy_item, "execute", receipt_type="item") is legacy_item
    assert _operation_receipt({}, {"operation": "execute"}, "execute", receipt_type="item") == {}


def _claim(key: str, value: dict[str, object], normalized: dict[str, object]) -> NoticeClaim:
    return NoticeClaim(
        id=f"claim-{key}",
        user_id="user",
        document_id="doc-v2",
        chunk_id="chunk-v2",
        claim_key=key,
        claim_type="test",
        value_json=value,
        normalized_value_json=normalized,
        audience_rule_json={},
        confidence=0.98,
        evidence_start=0,
        evidence_end=1,
        extractor_version="test",
        review_state="approved",
        created_at=NOW,
    )


def _task() -> Task:
    return Task(
        id="task-1",
        user_id="user",
        title="复习",
        description=None,
        course_id=None,
        course="人工智能",
        due_at=NOW + timedelta(days=2),
        reminder_at=NOW + timedelta(days=1),
        priority=TaskPriority.HIGH,
        status=TaskStatus.PENDING,
        source_type=SourceType.DOCUMENT,
        source_document_id="doc-v1",
        source_chunk_id="chunk-v1",
        source_claim_id="claim-v1",
        source_history=[],
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def _event() -> CalendarEvent:
    return CalendarEvent(
        id="event-1",
        user_id="user",
        title="考试",
        description=None,
        course_id=None,
        course="人工智能",
        start_at=NOW + timedelta(days=5, hours=1),
        end_at=NOW + timedelta(days=5, hours=3),
        location="A302",
        reminder_minutes=30,
        source_type=SourceType.DOCUMENT,
        source_document_id="doc-v1",
        source_chunk_id="chunk-v1",
        source_claim_id="claim-v1",
        source_history=[],
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )


def test_supported_impact_patch_variants_are_deterministic() -> None:
    task = _task()
    event = _event()
    old_time = _claim("event.start_at", {}, {"iso": "2026-07-18T09:00:00+08:00"})
    new_time = _claim("event.start_at", {}, {"iso": "2026-07-18T14:00:00+08:00"})
    end = _claim("event.end_at", {}, {"iso": "2026-07-18T16:00:00+08:00"})
    location = _claim("event.location", {"text": "B205"}, {"text": "b205"})
    reminder = _claim("reminder.minutes", {}, {"minutes": 60})
    deadline = _claim("task.due_at", {}, {"iso": "2026-07-20T12:00:00+08:00"})

    assert _impact_patch("event", event, "event.start_at", old_time, new_time) == {
        "start_at": "2026-07-18T14:00:00+08:00"
    }
    assert _impact_patch("event", event, "event.end_at", None, end)["end_at"].endswith("+08:00")
    assert _impact_patch("event", event, "event.location", None, location) == {"location": "B205"}
    assert _impact_patch("event", event, "reminder.minutes", None, reminder) == {
        "reminder_minutes": 60
    }
    assert _impact_patch("task", task, "task.due_at", None, deadline)["due_at"].endswith("+08:00")
    assert _impact_patch("task", task, "reminder.minutes", None, reminder)["reminder_at"].endswith(
        "+00:00"
    )
    shifted = _impact_patch("task", task, "event.start_at", old_time, new_time)
    assert set(shifted) == {"due_at", "reminder_at"}
    assert _impact_patch("task", task, "event.location", None, location) == {}
    assert _impact_patch("event", event, "event.location", None, None) == {}


def test_snapshots_apply_restore_and_canonical_verification() -> None:
    task = _task()
    event = _event()
    before_task = _snapshot("task", task)
    before_event = _snapshot("event", event)
    assert before_task["priority"] == "high"
    assert before_event["location"] == "A302"

    patch = {
        "due_at": "2026-07-20T14:00:00+08:00",
        "source_document_id": "doc-v2",
        "source_chunk_id": "chunk-v2",
        "source_claim_id": "claim-v2",
    }
    preview = _patched_snapshot(before_task, patch, ["claim-v2"])
    assert preview["version"] == 2
    assert preview["source_history"][0]["document_id"] == "doc-v1"
    _apply_patch(task, patch, ["claim-v2"])
    assert task.version == 2
    assert task.source_document_id == "doc-v2"
    assert task.source_history[0]["claim_id"] == "claim-v1"
    _restore_snapshot(task, before_task)
    assert task.version == 3
    assert task.source_document_id == "doc-v1"

    expected = {"start_at": "2026-07-18T14:00:00+08:00", "version": 1}
    actual = {"start_at": "2026-07-18T06:00:00+00:00", "version": 99}
    assert _snapshots_match(expected, actual, ignore_version=True)
    assert not _snapshots_match(expected, actual, ignore_version=False)
    assert _canonical_value([{"at": "2026-07-18T14:00:00+08:00"}]) == [
        {"at": "2026-07-18T06:00:00+00:00"}
    ]
    assert _as_datetime(None) is None
    assert _as_datetime(NOW) is NOW
    assert _as_datetime("2026-07-18T06:00:00").tzinfo is UTC


def test_plan_and_impact_views_expose_risk_lineage_and_verification() -> None:
    before = _snapshot("event", _event())
    after = before | {"location": "B205", "version": 2}
    item = ImpactMigrationItem(
        id="item-1",
        plan_id="plan-1",
        user_id="user",
        entity_type="event",
        entity_id="event-1",
        expected_version=1,
        before_snapshot=before,
        proposed_patch={"location": "B205"},
        after_snapshot=after,
        source_claim_ids=["claim-v2"],
        verification_json={"verified": True},
        execute_verification_json={"operation": "execute", "verified": True},
        undo_verification_json={"operation": "undo", "verified": True},
        created_at=NOW,
    )
    plan = ImpactMigrationPlan(
        id="plan-1",
        user_id="user",
        change_set_id="set-1",
        status="verified",
        risk_level="high",
        conflicts_json=[{"event": "other"}],
        verification_json={"verified": True, "verified_at": NOW.isoformat()},
        execute_receipt_json={"operation": "execute", "verified": True},
        undo_receipt_json={},
        generation=3,
        execution_idempotency_key="execute-key",
        undo_idempotency_key=None,
        version=2,
        executed_at=NOW,
        undone_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    assert _required_confirmations(plan) == 2
    view = _plan_view(plan, [item])
    assert view.required_confirmations == 2
    assert view.generation == 3
    assert view.execute_receipt == {"operation": "execute", "verified": True}
    assert view.items[0].after["location"] == "B205"
    assert _migration_item_view(item).verification == {"verified": True}
    assert _migration_item_view(item).execute_verification["operation"] == "execute"
    assert _migration_item_view(item).undo_verification["operation"] == "undo"
    assert _verification_time(plan) == NOW
    plan.verification_json = {}
    assert _verification_time(plan) == NOW

    impact = ImpactCase(
        id="impact-1",
        user_id="user",
        change_item_id="change-1",
        entity_type="event",
        entity_id="event-1",
        entity_version=1,
        reason="time changed",
        severity="high",
        current_snapshot=before,
        proposed_patch={"location": "B205"},
        recommended_action="manual_review",
        requires_manual_review=True,
        status="open",
        migration_plan_id="plan-1",
        detected_at=NOW,
        resolved_at=None,
    )
    impact_view = _impact_view(impact)
    assert impact_view.entity_id == "event-1"
    assert impact_view.recommended_action == "manual_review"
    assert impact_view.requires_manual_review is True
    assert _change_severity("event.start_at", "changed") == "high"
    assert _change_severity("event.location", "changed") == "medium"
    assert _change_severity("reminder.minutes", "changed") == "low"
    assert _change_severity("anything", "removed") == "high"
    assert _normalize_title("  Exam   Notice ") == "exam notice"
    assert _stable_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert _plan_signature([], []) == '{"conflicts":[],"items":[]}'


def test_unicode_fullwidth_semantics_grade_and_astral_evidence_offsets() -> None:
    assert normalize_semantic_text(" 教\u3000学楼 Ａ３０２！") == normalize_semantic_text(
        "教学楼A302"
    )
    assert normalize_grade("２０２４ 级本科生") == "2024"
    assert normalize_major("２０２４ 级 人 工 智 能 专业") == normalize_major("人工智能")
    assert normalize_course("课程： ＡＩ－１０１") == normalize_course("AI-101")

    content = (
        "😀𠀀【前缀】\n"
        "面向 ２０２４ 级 人 工 智 能 专业；\n"
        "考试时间：２０２６年０７月１８日 ０９：００～１１：００。\n"
        "地点：教\u3000学楼 Ａ３０２！"
    )
    claims = extract_claims(content)
    by_key = {claim.key: claim for claim in claims}
    assert by_key.keys() >= {"audience", "event.start_at", "event.end_at", "event.location"}
    assert by_key["audience"].audience == {
        "grade": "2024",
        "major": normalize_major("人工智能"),
        "raw": "２０２４ 级 人 工 智 能 专业",
    }
    assert by_key["event.start_at"].normalized["iso"] == "2026-07-18T09:00:00+08:00"
    assert by_key["event.end_at"].normalized["iso"] == "2026-07-18T11:00:00+08:00"
    assert by_key["event.location"].normalized["text"] == normalize_semantic_text("教学楼A302")
    for claim in claims:
        evidence = content[claim.start : claim.end]
        assert evidence
        assert content.index(evidence) == claim.start
    assert by_key["audience"].start == content.index("面向")
    assert len("😀𠀀") == 2


def test_exact_claim_or_matching_value_is_required_for_dependency() -> None:
    task = _task()
    event = _event()
    start = _claim("event.start_at", {}, {"iso": event.start_at.isoformat()})
    end = _claim("event.end_at", {}, {"iso": event.end_at.isoformat()})
    location = _claim(
        "event.location",
        {"text": "A302"},
        {"text": normalize_semantic_text("A302")},
    )

    task.source_claim_id = start.id
    assert _entity_depends_on_claim("task", task, start)
    task.source_claim_id = "different-claim"
    assert not _entity_depends_on_claim("task", task, start)

    event.source_claim_id = "different-claim"
    assert _entity_depends_on_claim("event", event, start)
    assert _entity_depends_on_claim("event", event, end)
    assert _entity_depends_on_claim("event", event, location)
    event.location = "用户手工改为 C404"
    assert not _entity_depends_on_claim("event", event, location)
    event.source_claim_id = location.id
    assert _entity_depends_on_claim("event", event, location)
