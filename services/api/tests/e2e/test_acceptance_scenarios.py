from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.repositories.tasks import TaskRepository
from app.services.verification.service import VerificationReport, VerificationService


def _prepare(
    client: TestClient,
    action: str,
    payload: dict[str, Any],
    *,
    target_id: str | None = None,
    **options: Any,
) -> dict[str, Any]:
    request: dict[str, Any] = {"action": action, "payload": payload, **options}
    if target_id is not None:
        request["target_id"] = target_id
    response = client.post("/api/actions/prepare", json=request)
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _confirm(client: TestClient, action_id: str, token: str) -> dict[str, Any]:
    del token
    issued = client.post(f"/api/actions/{action_id}/challenge")
    assert issued.status_code == 200, issued.text
    response = client.post(
        f"/api/actions/{action_id}/confirm",
        json={"confirmed": True, "challenge": issued.json()["challenge"]},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _execute(client: TestClient, action_id: str) -> dict[str, Any]:
    response = client.post(f"/api/actions/{action_id}/execute")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _event_payload_from_intent(
    parsed: dict[str, Any],
    *,
    source_type: str = "voice",
    source_document_id: str | None = None,
) -> dict[str, Any]:
    slots = parsed["slots"]
    assert isinstance(slots, dict)
    assert isinstance(slots.get("title"), str)
    assert isinstance(slots.get("date"), str)
    assert isinstance(slots.get("start_time"), str)

    start = datetime.fromisoformat(f"{slots['date']}T{slots['start_time']}:00+08:00")
    end_time = slots.get("end_time")
    if isinstance(end_time, str):
        end = datetime.fromisoformat(f"{slots['date']}T{end_time}:00+08:00")
    else:
        end = start + timedelta(hours=1)

    payload: dict[str, Any] = {
        "title": slots["title"],
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "source_type": source_type,
    }
    if source_document_id is not None:
        payload["source_document_id"] = source_document_id
    return payload


def _create_task(client: TestClient, title: str, token: str) -> tuple[str, str]:
    prepared = _prepare(client, "create_task", {"title": title})
    action_id = str(prepared["id"])
    assert prepared["state"] == "awaiting_confirmation"
    assert _confirm(client, action_id, token)["state"] == "ready"
    result = _execute(client, action_id)
    assert result["success"] is True
    assert isinstance(result["record_id"], str)
    return action_id, str(result["record_id"])


def _create_event(
    client: TestClient,
    *,
    title: str,
    start_at: str,
    end_at: str,
    location: str | None = None,
    token: str,
) -> tuple[str, str]:
    payload: dict[str, Any] = {
        "title": title,
        "start_at": start_at,
        "end_at": end_at,
    }
    if location is not None:
        payload["location"] = location
    prepared = _prepare(client, "create_event", payload)
    action_id = str(prepared["id"])
    assert prepared["state"] == "awaiting_confirmation"
    assert _confirm(client, action_id, token)["state"] == "ready"
    result = _execute(client, action_id)
    assert result["success"] is True
    assert isinstance(result["record_id"], str)
    return action_id, str(result["record_id"])


def _upload_notice(
    client: TestClient,
    *,
    title: str,
    content: str,
    publish_date: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/documents",
        files={"file": ("notice.txt", content.encode("utf-8"), "text/plain")},
        data={"title": title, "publish_date": publish_date, "version": "v1"},
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def test_scenario_01_transcribed_task_runs_intent_prepare_confirm_execute_and_verifies_db(
    client: TestClient,
) -> None:
    source_text = "帮我创建一个待办：复习高等数学"
    parsed_response = client.post(
        "/api/intent/parse",
        json={"text": source_text, "asr_confidence": 0.96},
    )
    assert parsed_response.status_code == 200, parsed_response.text
    parsed = parsed_response.json()
    assert parsed["intent"] == "create_task"
    assert parsed["slots"]["title"] == "复习高等数学"
    assert parsed["missing_fields"] == []

    prepared = _prepare(
        client,
        "create_task",
        {"title": parsed["slots"]["title"], "source_type": "voice"},
        asr_confidence=0.96,
        source_text=parsed["source_text"],
    )
    action_id = str(prepared["id"])
    assert prepared["state"] == "awaiting_confirmation"
    assert _confirm(client, action_id, "scenario-01-confirm")["state"] == "ready"

    executed = _execute(client, action_id)
    assert executed["success"] is True
    assert executed["verified_fields"]["title"] is True
    assert executed["verified_fields"]["source_type"] is True

    tasks = client.get("/api/tasks")
    assert tasks.status_code == 200
    assert tasks.json()["total"] == 1
    persisted = tasks.json()["items"][0]
    assert persisted["id"] == executed["record_id"]
    assert persisted["title"] == "复习高等数学"
    assert persisted["source_type"] == "voice"


def test_scenario_02_missing_information_needs_input_then_completed_retry_executes(
    client: TestClient,
) -> None:
    incomplete_response = client.post(
        "/api/intent/parse",
        json={"text": "帮我创建一个日程：机器学习考试", "asr_confidence": 0.94},
    )
    assert incomplete_response.status_code == 200, incomplete_response.text
    incomplete = incomplete_response.json()
    assert incomplete["intent"] == "create_event"
    assert set(incomplete["missing_fields"]) == {"date", "start_time"}

    blocked = _prepare(
        client,
        "create_event",
        {"title": incomplete["slots"]["title"], "source_type": "voice"},
        missing_fields=incomplete["missing_fields"],
        source_text=incomplete["source_text"],
    )
    assert blocked["state"] == "needs_input"
    assert "missing_required_fields" in blocked["blocking_reasons"]
    cannot_confirm = client.post(f"/api/actions/{blocked['id']}/challenge")
    assert cannot_confirm.status_code == 409

    completed_response = client.post(
        "/api/intent/parse",
        json={
            "text": "把机器学习考试加到日历，2026年7月18日上午九点",
            "asr_confidence": 0.94,
            "context": [incomplete["source_text"]],
        },
    )
    assert completed_response.status_code == 200, completed_response.text
    completed = completed_response.json()
    assert completed["missing_fields"] == []
    payload = _event_payload_from_intent(completed)

    retry = _prepare(
        client,
        "create_event",
        payload,
        asr_confidence=0.94,
        source_text=completed["source_text"],
    )
    retry_id = str(retry["id"])
    assert _confirm(client, retry_id, "scenario-02-retry")["state"] == "ready"
    executed = _execute(client, retry_id)
    assert executed["success"] is True
    assert client.get("/api/events").json()["total"] == 1


def test_scenario_03_calendar_conflict_is_detected_and_blocks_execution(
    client: TestClient,
) -> None:
    _, existing_id = _create_event(
        client,
        title="已有课程",
        start_at="2026-07-18T09:00:00+08:00",
        end_at="2026-07-18T10:00:00+08:00",
        token="scenario-03-existing",
    )
    conflict = _prepare(
        client,
        "create_event",
        {
            "title": "实验课",
            "start_at": "2026-07-18T09:30:00+08:00",
            "end_at": "2026-07-18T10:30:00+08:00",
        },
    )
    assert conflict["state"] == "needs_input"
    assert "time_conflict_requires_override" in conflict["blocking_reasons"]
    assert existing_id in conflict["diagnostics"]["conflict_ids"]

    execution = client.post(f"/api/actions/{conflict['id']}/execute")
    assert execution.status_code == 409
    assert client.get("/api/events").json()["total"] == 1


def test_scenario_04_duplicate_event_is_blocked_without_creating_second_record(
    client: TestClient,
) -> None:
    _, existing_id = _create_event(
        client,
        title="高等数学考试",
        start_at="2026-07-19T14:00:00+08:00",
        end_at="2026-07-19T16:00:00+08:00",
        location="A302",
        token="scenario-04-existing",
    )
    duplicate = _prepare(
        client,
        "create_event",
        {
            "title": "高等数学考试",
            "start_at": "2026-07-19T14:00:00+08:00",
            "end_at": "2026-07-19T16:00:00+08:00",
            "location": "A302",
        },
    )
    assert duplicate["state"] == "needs_input"
    assert "duplicate_record" in duplicate["blocking_reasons"]
    assert existing_id in duplicate["diagnostics"]["duplicate_ids"]

    confirmation = client.post(f"/api/actions/{duplicate['id']}/challenge")
    assert confirmation.status_code == 409
    assert client.get("/api/events").json()["total"] == 1


def test_scenario_05_delete_requires_two_distinct_confirmations(client: TestClient) -> None:
    _, task_id = _create_task(client, "待删除任务", "scenario-05-create")
    deletion = _prepare(client, "delete_task", {}, target_id=task_id)
    deletion_id = str(deletion["id"])
    assert deletion["risk_level"] == "high"
    assert deletion["required_confirmations"] == 2

    issued = client.post(f"/api/actions/{deletion_id}/challenge").json()["challenge"]
    first_response = client.post(
        f"/api/actions/{deletion_id}/confirm",
        json={"confirmed": True, "challenge": issued},
    )
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()
    assert first["state"] == "awaiting_second_confirmation"
    assert first["confirmations_received"] == 1

    replayed = client.post(
        f"/api/actions/{deletion_id}/confirm",
        json={"confirmed": True, "challenge": issued},
    )
    assert replayed.status_code == 409
    assert replayed.json()["error"]["code"] in {
        "confirmation_challenge_mismatch",
        "confirmation_replayed",
    }
    assert client.post(f"/api/actions/{deletion_id}/execute").status_code == 409

    second = _confirm(client, deletion_id, "scenario-05-second")
    assert second["state"] == "ready"
    assert second["confirmations_received"] == 2
    executed = _execute(client, deletion_id)
    assert executed["success"] is True
    assert executed["verified_fields"] == {"absent": True}
    assert client.get("/api/tasks").json()["total"] == 0


def test_scenario_06_forced_save_and_verification_failures_never_report_success(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    save_action = _prepare(client, "create_task", {"title": "强制保存失败"})
    save_action_id = str(save_action["id"])
    _confirm(client, save_action_id, "scenario-06-save")

    async def fail_save(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("TEST-ONLY forced database write failure")

    # TEST-ONLY FAULT INJECTION: the HTTP request still traverses ActionService,
    # its transaction handling, failure logging, and response serialization.
    with monkeypatch.context() as patcher:
        patcher.setattr(TaskRepository, "create", fail_save)
        save_failure = _execute(client, save_action_id)

    assert save_failure["success"] is False
    assert "database_write_failed" in str(save_failure["error"])
    assert client.get("/api/tasks").json()["total"] == 0

    verify_action = _prepare(client, "create_task", {"title": "强制验证失败"})
    verify_action_id = str(verify_action["id"])
    _confirm(client, verify_action_id, "scenario-06-verify")

    async def fail_verification(*_args: object, **_kwargs: object) -> VerificationReport:
        return VerificationReport(
            False,
            {"title": False},
            ("TEST-ONLY forced verification failure",),
            None,
        )

    with monkeypatch.context() as patcher:
        patcher.setattr(VerificationService, "verify_task", fail_verification)
        verification_failure = _execute(client, verify_action_id)

    assert verification_failure["success"] is False
    assert verification_failure["error"] == "post_commit_verification_failed"
    logs = client.get("/api/action-logs", params={"success": "false"})
    assert logs.status_code == 200
    assert logs.json()["total"] == 2
    assert all(item["success"] is False for item in logs.json()["items"])


def test_scenario_07_notice_query_returns_citation_backed_by_uploaded_document(
    client: TestClient,
) -> None:
    original_text = "机器学习考试地点为教学楼A302，考试时间为2026年7月18日上午九点。"
    document = _upload_notice(
        client,
        title="机器学习考试通知",
        content=original_text,
        publish_date="2026-07-01",
    )
    answer = client.post(
        "/api/knowledge/ask",
        json={
            "question": "机器学习考试的地点在哪里？",
            "top_k": 3,
            "min_similarity": 0.01,
        },
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["sufficient_evidence"] is True
    assert body["citations"]
    citation = body["citations"][0]
    assert citation["document_id"] == document["id"]
    assert citation["chunk_id"]
    assert citation["original_text"] == original_text
    assert citation["file_title"] == "机器学习考试通知"
    assert citation["publish_date"] == "2026-07-01"
    assert citation["page_number"] is None
    assert original_text in body["answer"]


def test_scenario_08_notice_date_becomes_verified_document_sourced_calendar_event(
    client: TestClient,
) -> None:
    original_text = "人工智能导论考试安排在2026年7月20日上午十点，地点为教学楼B201。"
    document = _upload_notice(
        client,
        title="人工智能导论考试安排",
        content=original_text,
        publish_date="2026-07-02",
    )
    search = client.post(
        "/api/knowledge/search",
        json={"query": "人工智能导论考试时间", "top_k": 3, "min_similarity": 0.01},
    )
    assert search.status_code == 200, search.text
    citation = search.json()["results"][0]
    assert citation["document_id"] == document["id"]
    assert "2026年7月20日上午十点" in citation["original_text"]

    conversion = client.post(
        "/api/intent/parse",
        json={
            "text": "把人工智能导论考试加到日历，2026年7月20日上午十点",
            "context": [citation["original_text"]],
            "asr_confidence": 0.98,
        },
    )
    assert conversion.status_code == 200, conversion.text
    parsed = conversion.json()
    assert parsed["intent"] == "create_event"
    assert parsed["slots"]["date"] == "2026-07-20"
    assert parsed["slots"]["start_time"] == "10:00"

    draft = _event_payload_from_intent(
        parsed,
        source_type="document",
        source_document_id=str(document["id"]),
    )
    draft["location"] = "教学楼B201"
    prepared = _prepare(
        client,
        "create_event",
        draft,
        source_text=parsed["source_text"],
    )
    action_id = str(prepared["id"])
    _confirm(client, action_id, "scenario-08-confirm")
    executed = _execute(client, action_id)
    assert executed["success"] is True
    assert all(executed["verified_fields"].values())

    event = client.get("/api/events").json()["items"][0]
    assert event["id"] == executed["record_id"]
    assert event["source_type"] == "document"
    assert event["source_document_id"] == document["id"]
    assert event["location"] == "教学楼B201"
    start_at = datetime.fromisoformat(event["start_at"].replace("Z", "+00:00"))
    assert start_at.astimezone(UTC) == datetime(2026, 7, 20, 2, 0, tzinfo=UTC)


def test_scenario_09_low_confidence_terminology_requires_user_confirmation(
    client: TestClient,
) -> None:
    original_text = "复习深度学袭重点"
    response = client.post(
        "/api/correction/preview",
        json={
            "text": original_text,
            "asr_confidence": 0.2,
            "terms": [
                {
                    "term": "深度学习",
                    "aliases": ["深度学袭"],
                    "source": "ai_term",
                    "context_keywords": [],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["requires_user_input"] is True
    assert body["record"]["corrected_text"] == original_text
    assert body["record"]["user_confirmed"] is False
    relevant = [
        item
        for item in body["record"]["modifications"]
        if item["original"] == "深度学袭" and item["replacement"] == "深度学习"
    ]
    assert relevant
    assert relevant[0]["policy"] in {"suggest", "clarify"}


def test_scenario_10_undo_latest_operation_restores_database_state(
    client: TestClient,
) -> None:
    _, task_id = _create_task(client, "撤销前任务", "scenario-10-create")
    update = _prepare(
        client,
        "update_task",
        {"title": "撤销后任务", "priority": "high"},
        target_id=task_id,
    )
    update_id = str(update["id"])
    _confirm(client, update_id, "scenario-10-update")
    changed = _execute(client, update_id)
    assert changed["success"] is True
    persisted = client.get("/api/tasks").json()["items"][0]
    assert (persisted["title"], persisted["priority"]) == ("撤销后任务", "high")

    undo = client.post(f"/api/actions/{update_id}/undo")
    assert undo.status_code == 200, undo.text
    assert undo.json()["success"] is True
    assert undo.json()["original_action"] == "update_task"
    assert undo.json()["verified_fields"]["title"] is True
    restored = client.get("/api/tasks").json()["items"][0]
    assert restored["id"] == task_id
    assert (restored["title"], restored["priority"]) == ("撤销前任务", "medium")
    assert client.get(f"/api/actions/{update_id}").json()["state"] == "undone"
