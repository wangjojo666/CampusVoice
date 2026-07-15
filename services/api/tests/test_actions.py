import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models.entities import ActionLog, PendingAction, Task, UndoRecord
from app.models.enums import PendingActionState
from app.repositories.actions import ActionRepository
from app.repositories.tasks import TaskRepository
from app.schemas.actions import CancelActionRequest, ExecutionResult, UndoResult
from app.services.actions.service import ActionService, AppliedOperation
from app.services.errors import ConflictError
from app.services.verification.service import VerificationReport, VerificationService
from tests.helpers import confirm_action, confirmed_write


def _pending_action_count(client: TestClient) -> int:
    async def count() -> int:
        factory = client.app.state.session_factory
        async with factory() as session:
            result = await session.scalar(select(func.count(PendingAction.id)))
            return int(result or 0)

    return asyncio.run(count())


def _action_side_effect_counts(client: TestClient) -> tuple[int, int, int]:
    async def count() -> tuple[int, int, int]:
        factory = client.app.state.session_factory
        async with factory() as session:
            return (
                int(await session.scalar(select(func.count(Task.id))) or 0),
                int(await session.scalar(select(func.count(ActionLog.id))) or 0),
                int(await session.scalar(select(func.count(UndoRecord.id))) or 0),
            )

    return asyncio.run(count())


def _undo_persistence(
    client: TestClient, action_id: str
) -> tuple[str, str, dict[str, object] | None]:
    async def read() -> tuple[str, str, dict[str, object] | None]:
        factory = client.app.state.session_factory
        async with factory() as session:
            row = (
                await session.execute(
                    select(PendingAction.state, UndoRecord.state, PendingAction.result)
                    .join(ActionLog, ActionLog.pending_action_id == PendingAction.id)
                    .join(UndoRecord, UndoRecord.action_log_id == ActionLog.id)
                    .where(PendingAction.id == action_id)
                )
            ).one()
            result = dict(row[2]) if row[2] is not None else None
            return row[0].value, row[1].value, result

    return asyncio.run(read())


def test_completeness_and_low_confidence_risk_are_deterministic(client: TestClient) -> None:
    incomplete = client.post(
        "/api/actions/prepare",
        json={"action": "create_event", "payload": {"title": "考试"}},
    )
    assert incomplete.status_code == 201
    body = incomplete.json()
    assert body["state"] == "needs_input"
    assert body["missing_fields"] == ["start_at"]

    low_confidence = client.post(
        "/api/actions/prepare",
        json={
            "action": "create_task",
            "payload": {"title": "机器学习作业"},
            "asr_confidence": 0.4,
        },
    ).json()
    assert low_confidence["risk_level"] == "high"
    assert low_confidence["required_confirmations"] == 2
    assert "low_asr_confidence" in low_confidence["risk_factors"]


def test_unknown_payload_fields_are_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/actions/prepare",
        json={
            "action": "create_task",
            "payload": {"title": "合法标题", "invented_field": "must fail"},
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_action_payload"


def test_action_prepare_rejects_null_end_for_create_without_writing(client: TestClient) -> None:
    assert _pending_action_count(client) == 0

    response = client.post(
        "/api/actions/prepare",
        json={
            "action": "create_event",
            "payload": {
                "title": "显式空结束时间",
                "start_at": "2026-07-18T09:00:00+08:00",
                "end_at": None,
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_action_payload"
    assert client.get("/api/events").json() == {"items": [], "total": 0}
    assert _pending_action_count(client) == 0


@pytest.mark.parametrize("field", ["title", "priority", "status", "source_type"])
def test_action_prepare_rejects_null_task_fields_without_modifying_target(
    client: TestClient,
    field: str,
) -> None:
    task_id = confirmed_write(client, "POST", "/api/tasks", {"title": "动作空值待办"}).json()[
        "record_id"
    ]
    task_before = client.get("/api/tasks").json()["items"][0]

    response = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_task",
            "target_id": task_id,
            "payload": {field: None},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_action_payload"
    assert client.get("/api/tasks").json()["items"][0] == task_before


@pytest.mark.parametrize(
    "field",
    ["title", "start_at", "end_at", "reminder_minutes", "source_type"],
)
def test_action_prepare_rejects_null_event_fields_without_modifying_target(
    client: TestClient,
    field: str,
) -> None:
    event_id = confirmed_write(
        client,
        "POST",
        "/api/events",
        {
            "title": "动作空值日程",
            "start_at": "2026-07-18T09:00:00+08:00",
            "end_at": "2026-07-18T10:00:00+08:00",
        },
    ).json()["record_id"]
    event_before = client.get("/api/events").json()["items"][0]

    response = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_event",
            "target_id": event_id,
            "payload": {field: None},
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_action_payload"
    assert client.get("/api/events").json()["items"][0] == event_before


def test_unique_task_title_is_resolved_without_exposing_an_internal_id(client: TestClient) -> None:
    task_id = confirmed_write(client, "POST", "/api/tasks", {"title": "机器学习作业"}).json()[
        "record_id"
    ]

    prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_task",
            "target_title": "机器学习作业",
            "payload": {"priority": "high"},
        },
    )

    assert prepared.status_code == 201, prepared.text
    body = prepared.json()
    assert body["target_id"] == task_id
    assert body["payload"]["expected_version"] == 1
    assert body["state"] == "awaiting_confirmation"
    assert body["diagnostics"]["target_resolution"] == "unique_title_match"


def test_same_title_candidates_and_missing_title_match_need_input(client: TestClient) -> None:
    for due_at in ("2026-07-18T01:00:00Z", "2026-07-19T01:00:00Z"):
        created = confirmed_write(
            client,
            "POST",
            "/api/tasks",
            {"title": "实验报告", "due_at": due_at},
        )
        assert created.status_code == 201, created.text

    ambiguous = client.post(
        "/api/actions/prepare",
        json={"action": "delete_task", "target_title": "实验报告", "payload": {}},
    )
    assert ambiguous.status_code == 201, ambiguous.text
    ambiguous_body = ambiguous.json()
    assert ambiguous_body["state"] == "needs_input"
    assert "ambiguous_target" in ambiguous_body["blocking_reasons"]
    assert ambiguous_body["missing_fields"] == ["target_selection"]
    assert len(ambiguous_body["diagnostics"]["target_candidates"]) == 2

    missing = client.post(
        "/api/actions/prepare",
        json={"action": "delete_task", "target_title": "不存在的任务", "payload": {}},
    )
    assert missing.status_code == 201, missing.text
    missing_body = missing.json()
    assert missing_body["state"] == "needs_input"
    assert "target_not_found" in missing_body["blocking_reasons"]
    assert missing_body["diagnostics"]["target_candidates"] == []


def test_unique_event_title_is_resolved_before_high_risk_delete_confirmation(
    client: TestClient,
) -> None:
    event_id = confirmed_write(
        client,
        "POST",
        "/api/events",
        {"title": "项目答辩", "start_at": "2026-07-20T01:00:00Z"},
    ).json()["record_id"]

    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "delete_event", "target_title": "项目答辩", "payload": {}},
    )

    assert prepared.status_code == 201, prepared.text
    assert prepared.json()["target_id"] == event_id
    assert prepared.json()["state"] == "awaiting_confirmation"
    assert prepared.json()["required_confirmations"] == 2


def test_unique_event_title_update_freezes_current_version(client: TestClient) -> None:
    event_id = confirmed_write(
        client,
        "POST",
        "/api/events",
        {"title": "版本冻结答辩", "start_at": "2026-07-20T01:00:00Z"},
    ).json()["record_id"]

    prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_event",
            "target_title": "版本冻结答辩",
            "payload": {"location": "A101"},
        },
    )

    assert prepared.status_code == 201, prepared.text
    body = prepared.json()
    assert body["target_id"] == event_id
    assert body["payload"]["expected_version"] == 1
    assert body["diagnostics"]["target_resolution"] == "unique_title_match"


def test_prepare_and_execute_are_idempotent(client: TestClient) -> None:
    request = {
        "action": "create_task",
        "payload": {"title": "幂等任务"},
        "idempotency_key": "voice-session-42-action-1",
    }
    first = client.post("/api/actions/prepare", json=request)
    second = client.post("/api/actions/prepare", json=request)
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]

    reused = client.post(
        "/api/actions/prepare",
        json=request | {"payload": {"title": "不同任务"}},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_key_reused"

    action_id = first.json()["id"]
    confirm_action(client, action_id)
    executed = client.post(f"/api/actions/{action_id}/execute")
    repeated = client.post(f"/api/actions/{action_id}/execute")
    assert executed.json()["success"] is True
    assert repeated.json() == executed.json()
    assert client.get("/api/tasks").json()["total"] == 1


def test_task_update_undo_restores_confirmed_snapshot(client: TestClient) -> None:
    task_id = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {"title": "撤销前", "priority": "low"},
    ).json()["record_id"]
    prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_task",
            "target_id": task_id,
            "payload": {"title": "撤销后", "priority": "high"},
        },
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)
    assert client.post(f"/api/actions/{action_id}/execute").json()["success"] is True
    changed = client.get("/api/tasks").json()["items"][0]
    assert (changed["title"], changed["priority"]) == ("撤销后", "high")

    undone = client.post(f"/api/actions/{action_id}/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["success"] is True
    restored = client.get("/api/tasks").json()["items"][0]
    assert (restored["title"], restored["priority"]) == ("撤销前", "low")


def test_create_undo_succeeds_only_while_created_record_is_unchanged(
    client: TestClient,
) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "立即撤销创建"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)
    executed = client.post(f"/api/actions/{action_id}/execute")
    assert executed.status_code == 200
    assert executed.json()["success"] is True

    undone = client.post(f"/api/actions/{action_id}/undo")
    repeated = client.post(f"/api/actions/{action_id}/undo")

    assert undone.status_code == 200
    assert undone.json()["success"] is True
    assert repeated.json() == undone.json()
    assert client.get("/api/tasks").json()["total"] == 0
    assert _undo_persistence(client, action_id)[:2] == ("undone", "undone")


def test_stale_update_undo_does_not_overwrite_later_task_edit(client: TestClient) -> None:
    task_id = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {"title": "初始版本", "priority": "low"},
    ).json()["record_id"]

    first = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_task",
            "target_id": task_id,
            "payload": {"title": "第一轮编辑", "priority": "medium"},
        },
    ).json()
    confirm_action(client, first["id"])
    assert client.post(f"/api/actions/{first['id']}/execute").json()["success"] is True

    second = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_task",
            "target_id": task_id,
            "payload": {"title": "后续编辑", "priority": "high"},
        },
    ).json()
    confirm_action(client, second["id"])
    assert client.post(f"/api/actions/{second['id']}/execute").json()["success"] is True

    rejected = client.post(f"/api/actions/{first['id']}/undo")

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "undo_version_conflict"
    current = client.get("/api/tasks").json()["items"][0]
    assert (current["title"], current["priority"], current["version"]) == (
        "后续编辑",
        "high",
        3,
    )
    assert _undo_persistence(client, first["id"])[:2] == ("executed", "available")


def test_undo_verifier_exception_retries_without_reapplying(client: TestClient) -> None:
    task_id = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {"title": "撤销恢复原值", "priority": "low"},
    ).json()["record_id"]
    prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_task",
            "target_id": task_id,
            "payload": {"title": "撤销恢复新值", "priority": "high"},
        },
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)
    assert client.post(f"/api/actions/{action_id}/execute").json()["success"] is True

    original_verify = VerificationService.verify_task
    calls = 0

    async def fail_once(self: object, *args: object, **kwargs: object) -> VerificationReport:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient undo verifier failure")
        return await original_verify(self, *args, **kwargs)  # type: ignore[arg-type]

    with patch.object(VerificationService, "verify_task", new=fail_once):
        first = client.post(f"/api/actions/{action_id}/undo")
        after_failure = client.get("/api/tasks").json()["items"][0]
        persisted_after_failure = _undo_persistence(client, action_id)
        second = client.post(f"/api/actions/{action_id}/undo")
        third = client.post(f"/api/actions/{action_id}/undo")

    assert first.status_code == 200
    assert first.json()["success"] is False
    assert first.json()["retryable"] is True
    assert first.json()["error"] == "undo_verification_exception: RuntimeError"
    assert (after_failure["title"], after_failure["priority"], after_failure["version"]) == (
        "撤销恢复原值",
        "low",
        3,
    )
    assert persisted_after_failure[:2] == ("undone", "failed")
    assert persisted_after_failure[2] is not None
    assert persisted_after_failure[2]["_undo_phase"] == "applied"
    assert second.status_code == 200
    assert second.json()["success"] is True
    assert third.json() == second.json()
    current = client.get("/api/tasks").json()["items"][0]
    assert current["version"] == 3
    assert _undo_persistence(client, action_id)[:2] == ("undone", "undone")


def test_legacy_failed_undo_is_verified_without_reapplying(client: TestClient) -> None:
    task_id = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {"title": "旧撤销原值", "priority": "low"},
    ).json()["record_id"]
    prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_task",
            "target_id": task_id,
            "payload": {"title": "旧撤销新值", "priority": "high"},
        },
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)
    assert client.post(f"/api/actions/{action_id}/execute").json()["success"] is True

    async def fail_verification(*_args: object, **_kwargs: object) -> VerificationReport:
        return VerificationReport(False, {"title": False}, ("forced_failure",), None)

    with patch.object(VerificationService, "verify_task", new=fail_verification):
        failed = client.post(f"/api/actions/{action_id}/undo")
    assert failed.json()["success"] is False

    async def convert_to_legacy_shape() -> None:
        factory = client.app.state.session_factory
        async with factory() as session, session.begin():
            action = await session.get(PendingAction, action_id)
            assert action is not None
            action.state = PendingActionState.EXECUTED
            action.result = {
                key: value
                for key, value in failed.json().items()
                if key in ExecutionResult.model_fields
            }

    asyncio.run(convert_to_legacy_shape())
    before_retry = client.get("/api/tasks").json()["items"][0]

    recovered = client.post(f"/api/actions/{action_id}/undo")

    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["success"] is True
    after_retry = client.get("/api/tasks").json()["items"][0]
    assert before_retry["version"] == after_retry["version"] == 3
    assert after_retry["title"] == "旧撤销原值"
    assert _undo_persistence(client, action_id)[:2] == ("undone", "undone")


def test_damaged_undo_recovery_envelope_returns_conflict(client: TestClient) -> None:
    task_id = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {"title": "损坏恢复原值", "priority": "low"},
    ).json()["record_id"]
    prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_task",
            "target_id": task_id,
            "payload": {"title": "损坏恢复新值", "priority": "high"},
        },
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)
    assert client.post(f"/api/actions/{action_id}/execute").json()["success"] is True

    async def fail_verification(*_args: object, **_kwargs: object) -> VerificationReport:
        return VerificationReport(False, {"title": False}, ("forced_failure",), None)

    with patch.object(VerificationService, "verify_task", new=fail_verification):
        assert client.post(f"/api/actions/{action_id}/undo").json()["success"] is False

    async def corrupt_envelope() -> None:
        factory = client.app.state.session_factory
        async with factory() as session, session.begin():
            action = await session.get(PendingAction, action_id)
            assert action is not None and action.result is not None
            result = dict(action.result)
            operation = dict(result["_operation"])
            operation["expected_fields"] = None
            result["_operation"] = operation
            action.result = result

    asyncio.run(corrupt_envelope())

    rejected = client.post(f"/api/actions/{action_id}/undo")

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "undo_recovery_state_invalid"
    current = client.get("/api/tasks").json()["items"][0]
    assert current["version"] == 3
    assert _undo_persistence(client, action_id)[:2] == ("undone", "failed")


def test_stale_undo_verifier_token_cannot_overwrite_newer_attempt(
    client: TestClient,
) -> None:
    task_id = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {"title": "验证代际原值", "priority": "low"},
    ).json()["record_id"]
    prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_task",
            "target_id": task_id,
            "payload": {"title": "验证代际新值", "priority": "high"},
        },
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)
    assert client.post(f"/api/actions/{action_id}/execute").json()["success"] is True

    async def fail_verification(*_args: object, **_kwargs: object) -> VerificationReport:
        return VerificationReport(False, {"title": False}, ("forced_failure",), None)

    with patch.object(VerificationService, "verify_task", new=fail_verification):
        failed = client.post(f"/api/actions/{action_id}/undo")
    assert failed.json()["success"] is False

    async def finalize_out_of_order() -> ExecutionResult:
        factory = client.app.state.session_factory
        service = ActionService()
        async with factory() as session, session.begin():
            action = await session.get(PendingAction, action_id)
            assert action is not None and action.result is not None
            action_type = action.action_type
            prior_result = dict(action.result)
            prior_token = str(prior_result["_undo_verify_token"])
            operation = AppliedOperation(**prior_result["_operation"])
            newer_token = "newer-undo-verification-token"
            claimed = await service.actions.claim_undo_verification(
                session,
                "user_demo",
                action_id,
                expected_token=prior_token,
                result=prior_result | {"_undo_verify_token": newer_token},
            )
            assert claimed is not None

        stale_report = VerificationReport(
            True,
            {field: True for field in operation.expected_fields},
            (),
            None,
        )
        stale_result = ExecutionResult(
            success=True,
            action="undo_update_task",
            record_id=operation.record_id,
            verified_fields=stale_report.verified_fields,
            side_effects=[],
            message="stale success",
            action_id=action_id,
        )
        async with factory() as session:
            with pytest.raises(ConflictError) as superseded:
                await service._finalize_undo_verification(
                    session,
                    "user_demo",
                    action_id,
                    action_type,
                    operation,
                    stale_report,
                    stale_result,
                    verify_token=prior_token,
                    last_error=None,
                )
        assert superseded.value.code == "undo_verification_in_progress"

        newer_report = VerificationReport(
            False,
            {field: False for field in operation.expected_fields},
            ("newer_failure",),
            None,
        )
        newer_result = ExecutionResult.model_validate(
            {
                key: value
                for key, value in failed.json().items()
                if key in ExecutionResult.model_fields
            }
        )
        async with factory() as session:
            finalized = await service._finalize_undo_verification(
                session,
                "user_demo",
                action_id,
                action_type,
                operation,
                newer_report,
                newer_result,
                verify_token=newer_token,
                last_error="undo_verification_failed",
            )
        return finalized

    latest = asyncio.run(finalize_out_of_order())

    assert latest.success is False
    state = _undo_persistence(client, action_id)
    assert state[:2] == ("undone", "failed")
    assert state[2] is not None
    assert state[2]["success"] is False
    assert state[2]["_undo_verify_token"] == "newer-undo-verification-token"


def test_concurrent_undo_applies_business_reversal_once(client: TestClient) -> None:
    task_id = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {"title": "并发撤销原值", "priority": "low"},
    ).json()["record_id"]
    prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_task",
            "target_id": task_id,
            "payload": {"title": "并发撤销新值", "priority": "high"},
        },
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)
    assert client.post(f"/api/actions/{action_id}/execute").json()["success"] is True

    async def undo_both() -> list[UndoResult | BaseException]:
        factory = client.app.state.session_factory
        service = ActionService()
        original_claim = service.actions.claim_undo_application
        gate = asyncio.Event()
        lock = asyncio.Lock()
        arrivals = 0

        async def synchronized_claim(*args: object, **kwargs: object) -> object:
            nonlocal arrivals
            async with lock:
                arrivals += 1
                if arrivals == 2:
                    gate.set()
            await asyncio.wait_for(gate.wait(), timeout=5)
            return await original_claim(*args, **kwargs)  # type: ignore[arg-type]

        async def undo_once() -> UndoResult:
            async with factory() as session:
                return await service.undo(session, "user_demo", action_id)

        with patch.object(service.actions, "claim_undo_application", new=synchronized_claim):
            return list(await asyncio.gather(undo_once(), undo_once(), return_exceptions=True))

    results = asyncio.run(undo_both())

    successful = [result for result in results if isinstance(result, UndoResult)]
    superseded = [result for result in results if isinstance(result, ConflictError)]
    assert successful
    assert all(result.success is True for result in successful)
    assert all(result.code == "undo_verification_in_progress" for result in superseded)
    assert len(successful) + len(superseded) == 2
    current = client.get("/api/tasks").json()["items"][0]
    assert (current["title"], current["priority"], current["version"]) == (
        "并发撤销原值",
        "low",
        3,
    )
    assert _undo_persistence(client, action_id)[:2] == ("undone", "undone")


def test_post_commit_verification_failure_never_reports_success(
    client: TestClient,
) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "验证失败测试"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)

    async def fail_verification(*_args: object, **_kwargs: object) -> VerificationReport:
        return VerificationReport(False, {"title": False}, ("forced_test_failure",), None)

    with patch(
        "app.services.verification.service.VerificationService.verify_task",
        new=fail_verification,
    ):
        first = client.post(f"/api/actions/{action_id}/execute")
        cancelled = client.post(
            f"/api/actions/{action_id}/cancel", json={"reason": "不能隐藏已落地写入"}
        )
        second = client.post(f"/api/actions/{action_id}/execute")

    assert first.status_code == 200
    assert first.json()["success"] is False
    assert first.json()["error"] == "post_commit_verification_failed"
    assert cancelled.status_code == 409
    assert cancelled.json()["error"]["code"] == "invalid_action_state"
    assert second.json()["success"] is False
    exhausted = client.post(f"/api/actions/{action_id}/execute")
    assert exhausted.status_code == 409
    assert exhausted.json()["error"]["code"] == "retry_limit_reached"
    state = client.get(f"/api/actions/{action_id}").json()
    assert state["state"] == "failed"
    assert state["attempt_count"] == 2


def test_database_exception_rolls_back_and_is_logged(client: TestClient) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "事务回滚"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)

    async def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced database failure")

    with patch("app.repositories.tasks.TaskRepository.create", new=explode):
        response = client.post(f"/api/actions/{action_id}/execute")

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "database_write_failed" in response.json()["error"]
    assert client.get("/api/tasks").json()["total"] == 0
    logs = client.get("/api/action-logs", params={"success": "false"}).json()
    assert logs["total"] == 1
    assert logs["items"][0]["success"] is False
    cancelled = client.post(
        f"/api/actions/{action_id}/cancel", json={"reason": "尚未写入，可以取消"}
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"


def test_database_exception_is_durably_retryable_without_duplicate_writes(
    client: TestClient,
) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "瞬时写入失败"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)

    original_create = TaskRepository.create
    calls = 0

    async def fail_once(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient write failure")
        return await original_create(*args, **kwargs)  # type: ignore[arg-type]

    with patch("app.repositories.tasks.TaskRepository.create", new=fail_once):
        first = client.post(f"/api/actions/{action_id}/execute")
        second = client.post(f"/api/actions/{action_id}/execute")

    assert first.status_code == 200
    assert first.json()["success"] is False
    assert first.json()["retryable"] is True
    assert second.status_code == 200
    assert second.json()["success"] is True
    state = client.get(f"/api/actions/{action_id}").json()
    assert state["state"] == "executed"
    assert state["attempt_count"] == 2
    assert _action_side_effect_counts(client) == (1, 2, 1)


def test_database_exception_exhausts_retry_limit_durably(client: TestClient) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "持续写入失败"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)

    async def always_fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("persistent write failure")

    with patch("app.repositories.tasks.TaskRepository.create", new=always_fail):
        first = client.post(f"/api/actions/{action_id}/execute")
        second = client.post(f"/api/actions/{action_id}/execute")
    exhausted = client.post(f"/api/actions/{action_id}/execute")

    assert first.json()["retryable"] is True
    assert second.json()["retryable"] is False
    assert exhausted.status_code == 409
    assert exhausted.json()["error"]["code"] == "retry_limit_reached"
    state = client.get(f"/api/actions/{action_id}").json()
    assert state["state"] == "failed"
    assert state["attempt_count"] == 2
    assert _action_side_effect_counts(client) == (0, 2, 0)


def test_stale_write_failure_cannot_overwrite_completed_action(client: TestClient) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "过期失败回写"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)
    executed = client.post(f"/api/actions/{action_id}/execute").json()
    assert executed["success"] is True

    async def record_stale_failure() -> ExecutionResult:
        factory = client.app.state.session_factory
        async with factory() as session:
            return await ActionService()._record_execution_failure(
                session,
                "user_demo",
                action_id,
                prior_attempt_count=0,
                error="database_write_failed: stale request",
            )

    recovered = asyncio.run(record_stale_failure())

    assert recovered.success is True
    assert recovered.model_dump(mode="json") == {
        key: value for key, value in executed.items() if key in ExecutionResult.model_fields
    }
    assert client.get(f"/api/actions/{action_id}").json()["state"] == "executed"
    assert _action_side_effect_counts(client) == (1, 1, 1)


def test_stale_verifier_cannot_overwrite_newer_retry_attempt(client: TestClient) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "迟到验证结果"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)

    async def fail_verification(*_args: object, **_kwargs: object) -> VerificationReport:
        return VerificationReport(False, {"title": False}, ("forced_test_failure",), None)

    with patch(
        "app.services.verification.service.VerificationService.verify_task",
        new=fail_verification,
    ):
        first = client.post(f"/api/actions/{action_id}/execute")
    assert first.json()["success"] is False

    async def finalize_stale_attempt() -> None:
        factory = client.app.state.session_factory
        service = ActionService()
        async with factory() as session, session.begin():
            claimed = await service.actions.claim_execution(session, "user_demo", action_id)
            assert claimed is not None
            assert claimed.attempt_count == 2
            assert claimed.result is not None
            operation = AppliedOperation(**claimed.result["_operation"])
        stale_report = VerificationReport(False, {"title": False}, ("late_first_attempt",), None)
        stale_result = ExecutionResult.model_validate(first.json())
        async with factory() as session:
            with pytest.raises(ConflictError, match="newer action attempt"):
                await service._finalize_execution_verification(
                    session,
                    "user_demo",
                    action_id,
                    operation,
                    stale_report,
                    stale_result,
                    expected_attempt_count=1,
                    last_error="post_commit_verification_failed",
                )

    asyncio.run(finalize_stale_attempt())

    in_progress = client.get(f"/api/actions/{action_id}").json()
    assert in_progress["state"] == "executing"
    assert in_progress["attempt_count"] == 2
    recovered = client.post(f"/api/actions/{action_id}/execute")
    assert recovered.status_code == 200
    assert recovered.json()["success"] is True
    assert _action_side_effect_counts(client) == (1, 1, 1)


def test_verifier_exception_recovers_without_reapplying_business_write(
    client: TestClient,
) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "验证器异常恢复"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)

    original_verify = VerificationService.verify_task
    calls = 0

    async def fail_once(self: object, *args: object, **kwargs: object) -> VerificationReport:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient verifier failure")
        return await original_verify(self, *args, **kwargs)  # type: ignore[arg-type]

    with patch.object(VerificationService, "verify_task", new=fail_once):
        first = client.post(f"/api/actions/{action_id}/execute")
        second = client.post(f"/api/actions/{action_id}/execute")

    assert first.status_code == 200
    assert first.json()["success"] is False
    assert first.json()["error"] == "post_commit_verification_failed"
    assert first.json()["retryable"] is True
    assert second.status_code == 200
    assert second.json()["success"] is True
    state = client.get(f"/api/actions/{action_id}").json()
    assert state["state"] == "executed"
    assert state["attempt_count"] == 2
    assert _action_side_effect_counts(client) == (1, 1, 1)


def test_executing_action_recovers_after_prepare_ttl_without_reapplying(
    client: TestClient,
) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "Recover after process exit"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)

    async def interrupt_verification(*_args: object, **_kwargs: object) -> VerificationReport:
        raise KeyboardInterrupt("simulated process exit after commit")

    async def crash_after_commit() -> None:
        factory = client.app.state.session_factory
        service = ActionService()
        async with factory() as session:
            with (
                patch.object(service, "_verify", new=interrupt_verification),
                pytest.raises(KeyboardInterrupt, match="simulated process exit"),
            ):
                await service.execute(session, "user_demo", action_id)

    asyncio.run(crash_after_commit())

    interrupted = client.get(f"/api/actions/{action_id}").json()
    assert interrupted["state"] == "executing"
    assert interrupted["result"]["applied"] is True
    assert _action_side_effect_counts(client) == (1, 1, 1)

    future = datetime.now(UTC) + timedelta(days=1)
    with patch("app.services.actions.service.utc_now", return_value=future):
        recovered = client.post(f"/api/actions/{action_id}/execute")

    assert recovered.status_code == 200
    assert recovered.json()["success"] is True
    state = client.get(f"/api/actions/{action_id}").json()
    assert state["state"] == "executed"
    assert state["attempt_count"] == 1
    assert _action_side_effect_counts(client) == (1, 1, 1)


def test_failed_applied_action_recovers_after_prepare_ttl_without_reapplying(
    client: TestClient,
) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "Recover failed verification"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)

    original_verify = VerificationService.verify_task
    calls = 0

    async def fail_once(self: object, *args: object, **kwargs: object) -> VerificationReport:
        nonlocal calls
        calls += 1
        if calls == 1:
            return VerificationReport(False, {"title": False}, ("forced_test_failure",), None)
        return await original_verify(self, *args, **kwargs)  # type: ignore[arg-type]

    with patch.object(VerificationService, "verify_task", new=fail_once):
        first = client.post(f"/api/actions/{action_id}/execute")
        failed = client.get(f"/api/actions/{action_id}").json()
        assert failed["state"] == "failed"
        assert failed["result"]["applied"] is True
        future = datetime.now(UTC) + timedelta(days=1)
        with patch("app.services.actions.service.utc_now", return_value=future):
            recovered = client.post(f"/api/actions/{action_id}/execute")

    assert first.status_code == 200
    assert first.json()["success"] is False
    assert recovered.status_code == 200
    assert recovered.json()["success"] is True
    assert client.get(f"/api/actions/{action_id}").json()["state"] == "executed"
    assert _action_side_effect_counts(client) == (1, 1, 1)


def test_failed_unapplied_action_still_expires_after_prepare_ttl(
    client: TestClient,
) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "Expire failed write"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)

    async def fail_write(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced database failure")

    with patch("app.repositories.tasks.TaskRepository.create", new=fail_write):
        first = client.post(f"/api/actions/{action_id}/execute")
    failed = client.get(f"/api/actions/{action_id}").json()
    assert first.status_code == 200
    assert first.json()["success"] is False
    assert failed["state"] == "failed"
    assert failed["result"]["applied"] is False

    future = datetime.now(UTC) + timedelta(days=1)
    with patch("app.services.actions.service.utc_now", return_value=future):
        expired = client.post(f"/api/actions/{action_id}/execute")

    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "action_expired"
    assert client.get(f"/api/actions/{action_id}").json()["state"] == "expired"
    assert _action_side_effect_counts(client) == (0, 1, 0)


def test_concurrent_execute_claim_creates_business_record_once(client: TestClient) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "并发执行原子认领"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)

    async def execute_both() -> list[ExecutionResult]:
        factory = client.app.state.session_factory
        service = ActionService()
        original_claim = service.actions.claim_execution
        gate = asyncio.Event()
        lock = asyncio.Lock()
        arrivals = 0

        async def synchronized_claim(*args: object, **kwargs: object) -> object:
            nonlocal arrivals
            async with lock:
                arrivals += 1
                if arrivals == 2:
                    gate.set()
            await asyncio.wait_for(gate.wait(), timeout=5)
            return await original_claim(*args, **kwargs)  # type: ignore[arg-type]

        async def execute_once() -> ExecutionResult:
            async with factory() as session:
                return await service.execute(session, "user_demo", action_id)

        with patch.object(service.actions, "claim_execution", new=synchronized_claim):
            return list(await asyncio.gather(execute_once(), execute_once()))

    results = asyncio.run(execute_both())

    assert all(result.success is True for result in results)
    assert {result.record_id for result in results}
    assert len({result.record_id for result in results}) == 1
    assert _action_side_effect_counts(client) == (1, 1, 1)


def test_cancel_and_execute_compete_with_one_atomic_winner(client: TestClient) -> None:
    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "取消执行竞争"}},
    ).json()
    action_id = prepared["id"]
    confirm_action(client, action_id)

    async def compete() -> list[object]:
        factory = client.app.state.session_factory
        service = ActionService()
        original_claim = service.actions.claim_execution
        original_cancel = service.actions.cancel_pending
        gate = asyncio.Event()
        lock = asyncio.Lock()
        arrivals = 0

        async def wait_for_competitor() -> None:
            nonlocal arrivals
            async with lock:
                arrivals += 1
                if arrivals == 2:
                    gate.set()
            await asyncio.wait_for(gate.wait(), timeout=5)

        async def synchronized_claim(*args: object, **kwargs: object) -> object:
            await wait_for_competitor()
            return await original_claim(*args, **kwargs)  # type: ignore[arg-type]

        async def synchronized_cancel(*args: object, **kwargs: object) -> object:
            await wait_for_competitor()
            return await original_cancel(*args, **kwargs)  # type: ignore[arg-type]

        async def execute_once() -> object:
            async with factory() as session:
                return await service.execute(session, "user_demo", action_id)

        async def cancel_once() -> object:
            async with factory() as session:
                return await service.cancel(
                    session,
                    "user_demo",
                    action_id,
                    CancelActionRequest(reason="concurrency test"),
                )

        with (
            patch.object(service.actions, "claim_execution", new=synchronized_claim),
            patch.object(service.actions, "cancel_pending", new=synchronized_cancel),
        ):
            return list(await asyncio.gather(execute_once(), cancel_once(), return_exceptions=True))

    outcomes = asyncio.run(compete())
    state = client.get(f"/api/actions/{action_id}").json()["state"]

    assert sum(isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert state in {"executed", "cancelled"}
    expected_tasks = 1 if state == "executed" else 0
    assert client.get("/api/tasks").json()["total"] == expected_tasks


def test_cancelled_action_cannot_execute(client: TestClient) -> None:
    action_id = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "取消任务"}},
    ).json()["id"]
    cancelled = client.post(f"/api/actions/{action_id}/cancel", json={"reason": "用户取消"})
    assert cancelled.json()["state"] == "cancelled"
    repeated = client.post(f"/api/actions/{action_id}/cancel", json={"reason": "重复取消"})
    assert repeated.status_code == 200
    assert repeated.json()["id"] == action_id
    assert repeated.json()["last_error"] == "用户取消"
    assert client.post(f"/api/actions/{action_id}/execute").status_code == 409


def test_expired_action_is_durably_marked_and_rejected(client: TestClient) -> None:
    action_id = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "过期任务"}},
    ).json()["id"]
    future = datetime.now(UTC) + timedelta(days=1)
    with patch("app.services.actions.service.utc_now", return_value=future):
        response = client.post(f"/api/actions/{action_id}/challenge")
    assert response.status_code == 409
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert response.json()["error"]["code"] == "action_expired"
    assert client.get(f"/api/actions/{action_id}").json()["state"] == "expired"


def test_bulk_expiry_preserves_actions_with_applied_business_writes(
    client: TestClient,
) -> None:
    action_ids = [
        client.post(
            "/api/actions/prepare",
            json={"action": "create_task", "payload": {"title": f"Bulk expiry {index}"}},
        ).json()["id"]
        for index in range(4)
    ]
    pending_id, failed_unapplied_id, failed_applied_id, executing_id = action_ids
    past = datetime.now(UTC) - timedelta(days=1)

    async def expire_rows() -> int:
        factory = client.app.state.session_factory
        async with factory() as session, session.begin():
            rows = list(
                await session.scalars(select(PendingAction).where(PendingAction.id.in_(action_ids)))
            )
            by_id = {row.id: row for row in rows}
            for row in rows:
                row.expires_at = past
            by_id[failed_unapplied_id].state = PendingActionState.FAILED
            by_id[failed_unapplied_id].result = {"applied": False}
            by_id[failed_applied_id].state = PendingActionState.FAILED
            by_id[failed_applied_id].result = {"applied": True}
            by_id[executing_id].state = PendingActionState.EXECUTING
            by_id[executing_id].result = {"applied": True}
            await session.flush()
            return await ActionRepository().expire_old_actions(
                session, "user_demo", datetime.now(UTC)
            )

    expired_count = asyncio.run(expire_rows())

    async def states() -> dict[str, PendingActionState]:
        factory = client.app.state.session_factory
        async with factory() as session:
            rows = await session.execute(
                select(PendingAction.id, PendingAction.state).where(
                    PendingAction.id.in_(action_ids)
                )
            )
            return {action_id: state for action_id, state in rows}

    persisted = asyncio.run(states())
    assert expired_count == 2
    assert persisted[pending_id] == PendingActionState.EXPIRED
    assert persisted[failed_unapplied_id] == PendingActionState.EXPIRED
    assert persisted[failed_applied_id] == PendingActionState.FAILED
    assert persisted[executing_id] == PendingActionState.EXECUTING


def test_expired_undo_window_is_durably_rejected(client: TestClient) -> None:
    action_id = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "撤销过期任务"}},
    ).json()["id"]
    confirm_action(client, action_id)
    assert client.post(f"/api/actions/{action_id}/execute").json()["success"] is True

    future = datetime.now(UTC) + timedelta(days=2)
    with patch("app.services.actions.service.utc_now", return_value=future):
        expired = client.post(f"/api/actions/{action_id}/undo")
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "undo_expired"
    unavailable = client.post(f"/api/actions/{action_id}/undo")
    assert unavailable.status_code == 409
    assert unavailable.json()["error"]["code"] == "undo_unavailable"
