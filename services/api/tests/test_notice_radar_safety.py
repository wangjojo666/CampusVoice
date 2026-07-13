import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.models.entities import ImpactMigrationPlan
from app.schemas.notice_radar import MigrationExecuteRequest, MigrationUndoRequest
from app.services.errors import DomainError, NotFoundError
from app.services.notices import NoticeRadarService
from tests.helpers import confirm_action, confirmed_write
from tests.test_notice_radar import _challenged_write, _create_demo_chain


def _change_set_for_document(client: TestClient, document_id: str) -> dict[str, Any]:
    response = client.get("/api/notice-radar")
    assert response.status_code == 200, response.text
    card = next(
        item
        for item in response.json()["items"]
        if item.get("document_id") == document_id and item.get("change_set_id")
    )
    change_set = client.get(f"/api/notice-radar/changes/{card['change_set_id']}")
    assert change_set.status_code == 200, change_set.text
    return change_set.json()


def _preview(client: TestClient, change_set_id: str) -> dict[str, Any]:
    response = _challenged_write(
        client,
        "POST",
        f"/api/notice-radar/changes/{change_set_id}/migration-preview",
        None,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _execute_body(plan: dict[str, Any], key: str) -> dict[str, Any]:
    return {
        "plan_version": plan["version"],
        "idempotency_key": key,
        "allow_conflicts": False,
        "confirmation_stages": plan["required_confirmations"],
    }


def _entity_state(
    client: TestClient, plan: dict[str, Any]
) -> dict[tuple[str, str], dict[str, Any]]:
    tasks = {item["id"]: item for item in client.get("/api/tasks").json()["items"]}
    events = {item["id"]: item for item in client.get("/api/events").json()["items"]}
    state: dict[tuple[str, str], dict[str, Any]] = {}
    for item in plan["items"]:
        entity_type = item["entity_type"]
        entity = tasks[item["entity_id"]] if entity_type == "task" else events[item["entity_id"]]
        state[(entity_type, item["entity_id"])] = {
            key: entity.get(key)
            for key in (
                "version",
                "due_at",
                "reminder_at",
                "start_at",
                "end_at",
                "location",
                "source_document_id",
                "source_chunk_id",
                "source_claim_id",
                "source_history",
            )
        }
    return state


def _plan_record(client: TestClient, plan_id: str) -> dict[str, Any]:
    factory = client.app.state.session_factory

    async def read() -> dict[str, Any]:
        async with factory() as session:
            plan = await session.get(ImpactMigrationPlan, plan_id)
            assert plan is not None
            return {
                "status": plan.status,
                "version": plan.version,
                "generation": plan.generation,
                "execution_key": plan.execution_idempotency_key,
                "undo_key": plan.undo_idempotency_key,
                "execute_receipt": dict(plan.execute_receipt_json),
                "undo_receipt": dict(plan.undo_receipt_json),
            }

    return asyncio.run(read())


def _create_de_scoped_chain(
    client: TestClient,
) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
    settings = confirmed_write(
        client,
        "PATCH",
        "/api/settings",
        {"major": "人工智能", "grade": "２０２４ 级"},
    )
    assert settings.status_code == 200, settings.text
    series = confirmed_write(
        client,
        "POST",
        "/api/notice-radar/series",
        {
            "canonical_key": "de-scope-safety",
            "title": "适用范围变更通知",
            "department": "计算机学院",
        },
    )
    assert series.status_code == 201, series.text
    path = f"/api/notice-radar/series/{series.json()['id']}/versions"
    v1 = confirmed_write(
        client,
        "POST",
        path,
        {
            "title": "适用范围变更通知",
            "content": (
                "适用于 2024 级人工智能专业。\n"
                "考试时间：2026-08-08 09:00–11:00。\n"
                "地点：教学楼 A302。"
            ),
            "revision_number": 1,
            "version_label": "v1",
            "supersedes_document_id": None,
            "ingest_source": "api",
        },
    )
    assert v1.status_code == 201, v1.text
    v1_json = v1.json()
    source_claim = next(
        claim for claim in v1_json["claims"] if claim["claim_key"] == "event.start_at"
    )
    source = {
        "source_type": "document",
        "source_document_id": v1_json["id"],
        "source_chunk_id": source_claim["chunk_id"],
        "source_claim_id": source_claim["id"],
    }
    event = confirmed_write(
        client,
        "POST",
        "/api/events",
        {
            "title": "适用范围考试",
            "start_at": "2026-08-08T09:00:00+08:00",
            "end_at": "2026-08-08T11:00:00+08:00",
            "location": "教学楼 A302",
            **source,
        },
        headers={"Idempotency-Key": "de-scope-event-v1"},
    )
    assert event.status_code == 201, event.text
    task = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {
            "title": "适用范围复习任务",
            "due_at": "2026-08-08T08:00:00+08:00",
            **source,
        },
        headers={"Idempotency-Key": "de-scope-task-v1"},
    )
    assert task.status_code == 201, task.text
    v2 = confirmed_write(
        client,
        "POST",
        path,
        {
            "title": "适用范围变更通知",
            "content": (
                "适用于 2025 级人工智能专业。\n"
                "考试时间：2026-08-08 09:00–11:00。\n"
                "地点：教学楼 A302。"
            ),
            "revision_number": 2,
            "version_label": "v2",
            "supersedes_document_id": v1_json["id"],
            "ingest_source": "api",
        },
    )
    assert v2.status_code == 201, v2.text
    return (
        v1_json,
        v2.json(),
        {
            event.json()["record"]["id"],
            task.json()["record"]["id"],
        },
    )


def test_impact_detection_requires_exact_claim_or_matching_business_value(
    client: TestClient,
) -> None:
    v1, v2 = _create_demo_chain(client)
    material_claim = next(
        claim for claim in v1["claims"] if claim["claim_key"] == "required_materials"
    )
    location_claim = next(claim for claim in v1["claims"] if claim["claim_key"] == "event.location")
    custom_event = confirmed_write(
        client,
        "POST",
        "/api/events",
        {
            "title": "保留自定义地点的考试",
            "start_at": "2026-07-18T09:00:00+08:00",
            "end_at": "2026-07-18T11:00:00+08:00",
            "location": "用户手工改为 C404",
            "allow_conflict": True,
            "source_type": "document",
            "source_document_id": v1["id"],
            "source_chunk_id": material_claim["chunk_id"],
            "source_claim_id": material_claim["id"],
        },
        headers={"Idempotency-Key": "precise-dependency-event"},
    )
    assert custom_event.status_code == 428, custom_event.text
    pending = custom_event.json()["error"]["details"]["pending_action"]
    assert pending["required_confirmations"] == 2
    for _ in range(2):
        confirm_action(client, pending["id"])
    executed = client.post(f"/api/actions/{pending['id']}/execute")
    assert executed.status_code == 200, executed.text
    custom_event_id = executed.json()["record"]["id"]
    unrelated_task = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        {
            "title": "仅引用旧地点的说明任务",
            "due_at": "2026-07-17T18:00:00+08:00",
            "source_type": "document",
            "source_document_id": v1["id"],
            "source_chunk_id": location_claim["chunk_id"],
            "source_claim_id": location_claim["id"],
        },
        headers={"Idempotency-Key": "precise-dependency-task"},
    )
    assert unrelated_task.status_code == 201, unrelated_task.text

    change_set = _change_set_for_document(client, v2["id"])
    detected = _challenged_write(
        client,
        "POST",
        f"/api/notice-radar/changes/{change_set['id']}/impacts/detect",
        None,
    )
    assert detected.status_code == 200, detected.text
    key_by_change = {item["id"]: item["claim_key"] for item in change_set["items"]}
    keys_by_entity: dict[str, set[str]] = {}
    for impact in detected.json()["items"]:
        keys_by_entity.setdefault(impact["entity_id"], set()).add(
            key_by_change[impact["change_item_id"]]
        )

    event_keys = keys_by_entity[custom_event_id]
    assert event_keys >= {"event.start_at", "event.end_at"}
    assert "event.location" not in event_keys
    assert all(
        "location" not in impact["proposed_patch"]
        for impact in detected.json()["items"]
        if impact["entity_id"] == custom_event_id
    )
    unrelated_task_id = unrelated_task.json()["record"]["id"]
    assert "event.start_at" not in keys_by_entity.get(unrelated_task_id, set())


def test_applicable_v1_to_non_applicable_v2_emits_manual_cancel_suggestions(
    client: TestClient,
) -> None:
    _v1, v2, entity_ids = _create_de_scoped_chain(client)
    change_set = _change_set_for_document(client, v2["id"])
    impacts = client.get("/api/notice-radar/impacts", params={"change_set_id": change_set["id"]})
    assert impacts.status_code == 200, impacts.text
    payload = impacts.json()
    assert {item["entity_id"] for item in payload["items"]} == entity_ids
    assert all(item["recommended_action"] == "cancel" for item in payload["items"])
    assert all(item["requires_manual_review"] is True for item in payload["items"])
    assert all(item["proposed_patch"] == {} for item in payload["items"])
    assert all("no longer applies" in item["reason"] for item in payload["items"])

    radar = client.get("/api/notice-radar").json()["items"]
    card = next(item for item in radar if item.get("document_id") == v2["id"])
    assert card["card_type"] == "needs_review"
    assert card["applicability"] == "not_applicable"
    assert card["needs_review"] is True
    assert card["affected_events"] == 1
    assert card["affected_tasks"] == 1


def test_reject_then_approve_invalidates_old_plan_and_creates_new_generation(
    client: TestClient,
) -> None:
    _v1, v2 = _create_demo_chain(client)
    change_set = _change_set_for_document(client, v2["id"])
    plan1 = _preview(client, change_set["id"])
    start_change = next(
        item for item in change_set["items"] if item["claim_key"] == "event.start_at"
    )

    rejected = _challenged_write(
        client,
        "PATCH",
        f"/api/notice-radar/changes/items/{start_change['id']}/review",
        {"decision": "rejected"},
    )
    assert rejected.status_code == 200, rejected.text
    invalidated = client.get(f"/api/notice-radar/migrations/{plan1['id']}")
    assert invalidated.status_code == 200, invalidated.text
    assert invalidated.json()["status"] == "invalidated"
    assert invalidated.json()["version"] == plan1["version"] + 1

    stale_execute = _challenged_write(
        client,
        "POST",
        f"/api/notice-radar/migrations/{plan1['id']}/execute",
        _execute_body(plan1, "rejected-plan-execute"),
    )
    assert stale_execute.status_code == 409
    assert stale_execute.json()["error"]["code"] == "migration_not_executable"

    approved = _challenged_write(
        client,
        "PATCH",
        f"/api/notice-radar/changes/items/{start_change['id']}/review",
        {"decision": "approved"},
    )
    assert approved.status_code == 200, approved.text
    detected = _challenged_write(
        client,
        "POST",
        f"/api/notice-radar/changes/{change_set['id']}/impacts/detect",
        None,
    )
    assert detected.status_code == 200, detected.text
    start_impacts = [
        item for item in detected.json()["items"] if item["change_item_id"] == start_change["id"]
    ]
    assert start_impacts
    assert all(item["status"] == "open" and item["resolved_at"] is None for item in start_impacts)

    plan2 = _preview(client, change_set["id"])
    assert plan2["id"] != plan1["id"]
    assert plan2["generation"] == plan1["generation"] + 1
    assert plan2["version"] == 1
    assert plan2["execute_receipt"] == {}
    assert plan2["undo_receipt"] == {}
    repeated = _preview(client, change_set["id"])
    assert (repeated["id"], repeated["generation"], repeated["version"]) == (
        plan2["id"],
        plan2["generation"],
        plan2["version"],
    )


def test_conflict_created_after_preview_blocks_old_plan_without_partial_writes(
    client: TestClient,
) -> None:
    _v1, v2 = _create_demo_chain(client)
    change_set = _change_set_for_document(client, v2["id"])
    plan = _preview(client, change_set["id"])
    assert plan["conflicts"] == []
    before = _entity_state(client, plan)

    conflict = confirmed_write(
        client,
        "POST",
        "/api/events",
        {
            "title": "预览后新增的课程冲突",
            "start_at": "2026-07-18T14:30:00+08:00",
            "end_at": "2026-07-18T15:30:00+08:00",
            "location": "教学楼 C101",
        },
        headers={"Idempotency-Key": "conflict-created-after-preview"},
    )
    assert conflict.status_code == 201, conflict.text

    response = _challenged_write(
        client,
        "POST",
        f"/api/notice-radar/migrations/{plan['id']}/execute",
        _execute_body(plan, "old-preview-new-conflict"),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "calendar_conflicts_changed"
    assert _entity_state(client, plan) == before
    record = _plan_record(client, plan["id"])
    assert record["status"] == "ready"
    assert record["execution_key"] is None

    refreshed = _preview(client, change_set["id"])
    assert refreshed["id"] != plan["id"]
    assert refreshed["generation"] == plan["generation"] + 1
    assert refreshed["version"] == 1
    assert refreshed["conflicts"]
    assert refreshed["required_confirmations"] == 2
    stale = _plan_record(client, plan["id"])
    assert stale["status"] == "invalidated"
    assert stale["version"] == plan["version"] + 1


async def _race_execute(
    client: TestClient,
    plan_id: str,
    requests: list[MigrationExecuteRequest],
) -> list[tuple[str, str]]:
    factory = client.app.state.session_factory
    service = NoticeRadarService(factory)
    gate = asyncio.Event()

    async def invoke(request: MigrationExecuteRequest) -> tuple[str, str]:
        await gate.wait()
        async with factory() as session:
            try:
                receipt = await service.execute(session, "user_demo", plan_id, request)
            except DomainError as error:
                return "error", error.code
        return "ok", receipt.status

    tasks = [asyncio.create_task(invoke(request)) for request in requests]
    await asyncio.sleep(0)
    gate.set()
    return list(await asyncio.gather(*tasks))


async def _race_undo(
    client: TestClient,
    plan_id: str,
    requests: list[MigrationUndoRequest],
) -> list[tuple[str, str]]:
    factory = client.app.state.session_factory
    service = NoticeRadarService(factory)
    gate = asyncio.Event()

    async def invoke(request: MigrationUndoRequest) -> tuple[str, str]:
        await gate.wait()
        async with factory() as session:
            try:
                receipt = await service.undo(session, "user_demo", plan_id, request)
            except DomainError as error:
                return "error", error.code
        return "ok", receipt.status

    tasks = [asyncio.create_task(invoke(request)) for request in requests]
    await asyncio.sleep(0)
    gate.set()
    return list(await asyncio.gather(*tasks))


def test_concurrent_execute_and_undo_each_have_exactly_one_winner(
    client: TestClient,
) -> None:
    _v1, v2 = _create_demo_chain(client)
    change_set = _change_set_for_document(client, v2["id"])
    plan = _preview(client, change_set["id"])
    before = _entity_state(client, plan)
    execute_requests = [
        MigrationExecuteRequest(
            plan_version=plan["version"],
            idempotency_key=f"concurrent-execute-{index}",
            allow_conflicts=False,
            confirmation_stages=plan["required_confirmations"],
        )
        for index in (1, 2)
    ]
    execute_outcomes = asyncio.run(_race_execute(client, plan["id"], execute_requests))
    assert sum(status == "ok" for status, _ in execute_outcomes) == 1
    assert sum(status == "error" for status, _ in execute_outcomes) == 1
    assert {detail for status, detail in execute_outcomes if status == "error"} <= {
        "migration_execution_conflict",
        "migration_already_executed",
    }
    after_execute = _entity_state(client, plan)
    assert all(
        int(after_execute[key]["version"]) == int(before[key]["version"]) + 1 for key in before
    )
    execute_winner = next(
        request.idempotency_key
        for request, outcome in zip(execute_requests, execute_outcomes, strict=True)
        if outcome[0] == "ok"
    )
    execute_record = _plan_record(client, plan["id"])
    assert execute_record["status"] == "verified"
    assert execute_record["execution_key"] == execute_winner

    current = client.get(f"/api/notice-radar/migrations/{plan['id']}")
    assert current.status_code == 200, current.text
    undo_requests = [
        MigrationUndoRequest(
            plan_version=current.json()["version"],
            idempotency_key=f"concurrent-undo-{index}",
            confirmation_stages=2,
        )
        for index in (1, 2)
    ]
    undo_outcomes = asyncio.run(_race_undo(client, plan["id"], undo_requests))
    assert sum(status == "ok" for status, _ in undo_outcomes) == 1
    assert sum(status == "error" for status, _ in undo_outcomes) == 1
    assert {detail for status, detail in undo_outcomes if status == "error"} <= {
        "migration_undo_conflict",
        "migration_already_undone",
    }
    after_undo = _entity_state(client, plan)
    assert all(
        int(after_undo[key]["version"]) == int(after_execute[key]["version"]) + 1
        for key in after_execute
    )
    undo_winner = next(
        request.idempotency_key
        for request, outcome in zip(undo_requests, undo_outcomes, strict=True)
        if outcome[0] == "ok"
    )
    undo_record = _plan_record(client, plan["id"])
    assert undo_record["status"] == "undone"
    assert undo_record["undo_key"] == undo_winner


async def _service_execute(
    service: NoticeRadarService,
    client: TestClient,
    plan_id: str,
    request: MigrationExecuteRequest,
) -> Any:
    factory = client.app.state.session_factory
    async with factory() as session:
        return await service.execute(session, "user_demo", plan_id, request)


async def _service_undo(
    service: NoticeRadarService,
    client: TestClient,
    plan_id: str,
    request: MigrationUndoRequest,
) -> Any:
    factory = client.app.state.session_factory
    async with factory() as session:
        return await service.undo(session, "user_demo", plan_id, request)


def test_applied_and_undo_applied_states_recover_after_verifier_crash(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _v1, v2 = _create_demo_chain(client)
    change_set = _change_set_for_document(client, v2["id"])
    plan = _preview(client, change_set["id"])
    factory = client.app.state.session_factory
    service = NoticeRadarService(factory)
    execute_request = MigrationExecuteRequest(**_execute_body(plan, "crash-recovery-execute"))
    original_verify = service._verify

    async def verifier_crash(_user_id: str, _plan_id: str, *, operation: str) -> Any:
        raise RuntimeError(f"simulated crash before {operation} verification")

    monkeypatch.setattr(service, "_verify", verifier_crash)
    with pytest.raises(RuntimeError, match="execute verification"):
        asyncio.run(_service_execute(service, client, plan["id"], execute_request))
    applied = _plan_record(client, plan["id"])
    assert applied["status"] == "applied"
    assert applied["execution_key"] == "crash-recovery-execute"
    assert applied["execute_receipt"] == {}

    monkeypatch.setattr(service, "_verify", original_verify)
    execute_receipt = asyncio.run(_service_execute(service, client, plan["id"], execute_request))
    assert execute_receipt.status == "verified"
    assert execute_receipt.all_verified is True
    verified = client.get(f"/api/notice-radar/migrations/{plan['id']}").json()
    undo_request = MigrationUndoRequest(
        plan_version=verified["version"],
        idempotency_key="crash-recovery-undo",
        confirmation_stages=2,
    )

    monkeypatch.setattr(service, "_verify", verifier_crash)
    with pytest.raises(RuntimeError, match="undo verification"):
        asyncio.run(_service_undo(service, client, plan["id"], undo_request))
    undo_applied = _plan_record(client, plan["id"])
    assert undo_applied["status"] == "undo_applied"
    assert undo_applied["undo_key"] == "crash-recovery-undo"
    assert undo_applied["undo_receipt"] == {}
    assert undo_applied["execute_receipt"]["operation"] == "execute"

    monkeypatch.setattr(service, "_verify", original_verify)
    undo_receipt = asyncio.run(_service_undo(service, client, plan["id"], undo_request))
    assert undo_receipt.status == "undone"
    assert undo_receipt.all_verified is True


def test_execute_and_undo_receipts_are_independent_and_undo_creates_new_generation(
    client: TestClient,
) -> None:
    _v1, v2 = _create_demo_chain(client)
    change_set = _change_set_for_document(client, v2["id"])
    plan1 = _preview(client, change_set["id"])
    execute_path = f"/api/notice-radar/migrations/{plan1['id']}/execute"
    executed = _challenged_write(
        client,
        "POST",
        execute_path,
        _execute_body(plan1, "independent-receipt-execute"),
    )
    assert executed.status_code == 200, executed.text
    initial_execute_receipt = executed.json()
    assert initial_execute_receipt["operation"] == "execute"
    assert all(
        item["verification"]["operation"] == "execute" for item in initial_execute_receipt["items"]
    )

    applied_plan = client.get(f"/api/notice-radar/migrations/{plan1['id']}").json()
    undone = _challenged_write(
        client,
        "POST",
        execute_path.replace("/execute", "/undo"),
        {
            "plan_version": applied_plan["version"],
            "idempotency_key": "independent-receipt-undo",
            "confirmation_stages": 2,
        },
    )
    assert undone.status_code == 200, undone.text

    execute_receipt = client.get(
        f"/api/notice-radar/migrations/{plan1['id']}/receipt",
        params={"operation": "execute"},
    )
    undo_receipt = client.get(
        f"/api/notice-radar/migrations/{plan1['id']}/receipt",
        params={"operation": "undo"},
    )
    assert execute_receipt.status_code == undo_receipt.status_code == 200
    execute_json = execute_receipt.json()
    undo_json = undo_receipt.json()
    assert execute_json["operation"] == "execute"
    assert execute_json["status"] == "verified"
    assert execute_json["verified_at"] == initial_execute_receipt["verified_at"]
    assert undo_json["operation"] == "undo"
    assert undo_json["status"] == "undone"
    initial_items = {item["id"]: item for item in initial_execute_receipt["items"]}
    for item in execute_json["items"]:
        assert item["verification"] == initial_items[item["id"]]["verification"]
        assert item["execute_verification"] == item["verification"]
        assert item["undo_verification"]["operation"] == "undo"
    for item in undo_json["items"]:
        assert item["verification"]["operation"] == "undo"
        assert item["undo_verification"] == item["verification"]
        assert item["execute_verification"]["operation"] == "execute"

    plan_after_undo = client.get(f"/api/notice-radar/migrations/{plan1['id']}").json()
    assert plan_after_undo["verification"]["operation"] == "undo"
    assert plan_after_undo["execute_receipt"]["operation"] == "execute"
    assert plan_after_undo["execute_receipt"]["status"] == "verified"
    assert plan_after_undo["undo_receipt"]["operation"] == "undo"
    assert plan_after_undo["undo_receipt"]["status"] == "undone"

    plan2 = _preview(client, change_set["id"])
    assert plan2["id"] != plan1["id"]
    assert plan2["generation"] == plan1["generation"] + 1
    assert plan2["version"] == 1
    assert plan2["execute_receipt"] == plan2["undo_receipt"] == {}
    current_state = _entity_state(client, plan2)
    assert all(
        item["expected_version"]
        == current_state[(item["entity_type"], item["entity_id"])]["version"]
        for item in plan2["items"]
    )
    repeated = _preview(client, change_set["id"])
    assert (repeated["id"], repeated["generation"]) == (
        plan2["id"],
        plan2["generation"],
    )


def test_stale_group_undo_rolls_back_every_restore_and_releases_claim(
    client: TestClient,
) -> None:
    _v1, v2 = _create_demo_chain(client)
    change_set = _change_set_for_document(client, v2["id"])
    plan = _preview(client, change_set["id"])
    execute_path = f"/api/notice-radar/migrations/{plan['id']}/execute"
    executed = _challenged_write(
        client,
        "POST",
        execute_path,
        _execute_body(plan, "stale-undo-execute"),
    )
    assert executed.status_code == 200, executed.text
    event_item = next(item for item in plan["items"] if item["entity_type"] == "event")
    event = next(
        item
        for item in client.get("/api/events").json()["items"]
        if item["id"] == event_item["entity_id"]
    )
    changed = confirmed_write(
        client,
        "PATCH",
        f"/api/events/{event['id']}",
        {"location": "用户在迁移后手工调整", "expected_version": event["version"]},
        headers={"Idempotency-Key": "manual-change-before-group-undo"},
    )
    assert changed.status_code == 200, changed.text
    before_failed_undo = _entity_state(client, plan)
    current_plan = client.get(f"/api/notice-radar/migrations/{plan['id']}").json()
    undo = _challenged_write(
        client,
        "POST",
        execute_path.replace("/execute", "/undo"),
        {
            "plan_version": current_plan["version"],
            "idempotency_key": "stale-group-undo",
            "confirmation_stages": 2,
        },
    )
    assert undo.status_code == 409, undo.text
    assert undo.json()["error"]["code"] == "entity_version_conflict"
    assert _entity_state(client, plan) == before_failed_undo
    record = _plan_record(client, plan["id"])
    assert record["status"] == "verified"
    assert record["undo_key"] is None
    assert record["undo_receipt"] == {}


def test_notice_plan_impacts_execution_and_receipts_are_user_isolated(
    client: TestClient,
) -> None:
    _v1, v2 = _create_demo_chain(client)
    change_set = _change_set_for_document(client, v2["id"])
    plan = _preview(client, change_set["id"])
    factory = client.app.state.session_factory
    service = NoticeRadarService(factory)
    request = MigrationExecuteRequest(**_execute_body(plan, "cross-user-execute"))

    async def assert_isolated() -> None:
        async with factory() as session:
            with pytest.raises(NotFoundError):
                await service.change_set(session, "other-user", change_set["id"])
            with pytest.raises(NotFoundError):
                await service.plan(session, "other-user", plan["id"])
            with pytest.raises(NotFoundError):
                await service.execute(session, "other-user", plan["id"], request)
            impacts = await service.list_impacts(
                session,
                "other-user",
                change_set_id=change_set["id"],
                status=None,
                limit=100,
                offset=0,
            )
            assert impacts.total == 0
        with pytest.raises(NotFoundError):
            await service.receipt("other-user", plan["id"], operation="execute")

    asyncio.run(assert_isolated())
    record = _plan_record(client, plan["id"])
    assert record["status"] == "ready"
    assert record["execution_key"] is None
