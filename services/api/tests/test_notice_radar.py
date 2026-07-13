from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import confirmed_write


def _challenged_write(client: TestClient, method: str, path: str, body: object) -> Any:
    issued = client.post(
        "/api/auth/write-challenges",
        json={"method": method, "path": path, "body": body},
    )
    assert issued.status_code == 200, issued.text
    challenge = issued.json()
    if challenge["required_stages"] == 2:
        advanced = client.post(
            "/api/auth/write-challenges/advance",
            json={"challenge": challenge["challenge"]},
        )
        assert advanced.status_code == 200, advanced.text
        challenge = advanced.json()
    return client.request(
        method,
        path,
        json=body,
        headers={"X-Write-Challenge": challenge["challenge"]},
    )


def _create_demo_chain(
    client: TestClient, *, applicable: bool = True, uncertain: bool = False
) -> tuple[dict[str, Any], dict[str, Any]]:
    settings = {"major": "人工智能", "grade": "2024"}
    response = confirmed_write(client, "PATCH", "/api/settings", settings)
    assert response.status_code == 200, response.text

    series_body = {
        "canonical_key": "ai-exam-2026",
        "title": "2026 人工智能专业考试安排",
        "department": "计算机学院",
        "source_key": "test/ai-exam",
    }
    response = confirmed_write(client, "POST", "/api/notice-radar/series", series_body)
    assert response.status_code == 201, response.text
    series = response.json()

    v1_body = {
        "title": "2026 人工智能专业考试安排",
        "content": (
            "适用于 2024 级人工智能专业。\n"
            "考试时间：2026-07-18 09:00–11:00。\n"
            "地点：教学楼 A302。\n"
            "要求携带校园卡。提前 1440 分钟提醒。"
        ),
        "revision_number": 1,
        "version_label": "v1",
        "supersedes_document_id": None,
        "applicable_group": "2024 级人工智能专业",
        "ingest_source": "seed",
    }
    path = f"/api/notice-radar/series/{series['id']}/versions"
    response = confirmed_write(client, "POST", path, v1_body)
    assert response.status_code == 201, response.text
    v1 = response.json()
    start_claim = next(item for item in v1["claims"] if item["claim_key"] == "event.start_at")
    source = {
        "source_type": "document",
        "source_document_id": v1["id"],
        "source_chunk_id": start_claim["chunk_id"],
        "source_claim_id": start_claim["id"],
    }
    event = {
        "title": "人工智能专业考试",
        "start_at": "2026-07-18T09:00:00+08:00",
        "end_at": "2026-07-18T11:00:00+08:00",
        "location": "教学楼 A302",
        "reminder_minutes": 1440,
        **source,
    }
    response = confirmed_write(
        client,
        "POST",
        "/api/events",
        event,
        headers={"Idempotency-Key": "notice-radar-event-v1"},
    )
    assert response.status_code == 201, response.text
    for index, due_at in enumerate(
        ("2026-07-17T20:00:00+08:00", "2026-07-18T08:00:00+08:00"), start=1
    ):
        task = {
            "title": f"考试复习任务 {index}",
            "due_at": due_at,
            "reminder_at": "2026-07-17T08:00:00+08:00",
            "priority": "high",
            **source,
        }
        response = confirmed_write(
            client,
            "POST",
            "/api/tasks",
            task,
            headers={"Idempotency-Key": f"notice-radar-task-{index}-v1"},
        )
        assert response.status_code == 201, response.text

    if not applicable:
        response = confirmed_write(
            client,
            "PATCH",
            "/api/settings",
            {"major": "计算机科学", "grade": "2024"},
        )
        assert response.status_code == 200, response.text

    v2_body = {
        "title": "2026 人工智能专业考试安排",
        "content": (
            "适用于 2024 级人工智能专业同学。\n"
            f"考试时间：{'暂定 ' if uncertain else ''}2026-07-18 14:00–16:00。\n"
            f"地点改为：{'教学楼 A302' if uncertain else '教学楼 B205'}。\n"
            "请按时参加，要求携带校园卡。提前 1440 分钟提醒。"
        ),
        "revision_number": 2,
        "version_label": "v2",
        "supersedes_document_id": v1["id"],
        "applicable_group": "2024 级人工智能专业",
        "ingest_source": "seed",
    }
    response = confirmed_write(client, "POST", path, v2_body)
    assert response.status_code == 201, response.text
    return v1, response.json()


def test_notice_change_impact_atomic_execute_verify_and_group_undo(client: TestClient) -> None:
    v1, v2 = _create_demo_chain(client)

    series_rows = client.get("/api/notice-radar/series")
    assert series_rows.status_code == 200
    assert series_rows.json()[0]["version_count"] == 2
    timeline = client.get(f"/api/notice-radar/series/{v2['series_id']}/timeline")
    assert timeline.status_code == 200
    assert [item["revision_number"] for item in timeline.json()["versions"]] == [1, 2]
    claims = client.get(f"/api/notice-radar/documents/{v2['id']}/claims")
    assert claims.status_code == 200
    assert len(claims.json()) == len(v2["claims"])
    reanalyze = _challenged_write(
        client, "POST", f"/api/notice-radar/documents/{v2['id']}/reanalyze", None
    )
    assert reanalyze.status_code == 200
    assert len(reanalyze.json()) == len(v2["claims"])

    radar = client.get("/api/notice-radar")
    assert radar.status_code == 200, radar.text
    card = radar.json()["items"][0]
    assert card["affected_events"] == 1
    assert card["affected_tasks"] == 2

    changes = client.get(f"/api/notice-radar/changes/{card['change_set_id']}")
    assert changes.status_code == 200, changes.text
    payload = changes.json()
    assert {item["claim_key"] for item in payload["items"]} == {
        "event.start_at",
        "event.end_at",
        "event.location",
    }
    assert all(item["before"]["evidence_text"] for item in payload["items"])
    assert all(item["after"]["evidence_text"] for item in payload["items"])

    detected = _challenged_write(
        client,
        "POST",
        f"/api/notice-radar/changes/{card['change_set_id']}/impacts/detect",
        None,
    )
    assert detected.status_code == 200
    assert detected.json()["total"] == 5
    open_impacts = client.get(
        "/api/notice-radar/impacts",
        params={"change_set_id": card["change_set_id"], "status": "open"},
    )
    assert open_impacts.status_code == 200
    assert open_impacts.json()["total"] == 5

    preview_path = f"/api/notice-radar/changes/{card['change_set_id']}/migration-preview"
    preview = _challenged_write(client, "POST", preview_path, None)
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert len(plan["items"]) == 3
    assert plan["conflicts"] == []
    repeated_preview = _challenged_write(client, "POST", preview_path, None)
    assert repeated_preview.status_code == 200
    assert repeated_preview.json()["id"] == plan["id"]
    assert repeated_preview.json()["version"] == plan["version"]

    execute_body = {
        "plan_version": plan["version"],
        "idempotency_key": "execute-ai-exam-v2",
        "allow_conflicts": False,
        "confirmation_stages": plan["required_confirmations"],
    }
    execute_path = f"/api/notice-radar/migrations/{plan['id']}/execute"
    executed = _challenged_write(client, "POST", execute_path, execute_body)
    assert executed.status_code == 200, executed.text
    receipt = executed.json()
    assert receipt["all_verified"] is True, receipt
    assert receipt["verified_count"] == receipt["total_count"] == 3
    repeated_execute = _challenged_write(client, "POST", execute_path, execute_body)
    assert repeated_execute.status_code == 200
    assert repeated_execute.json()["all_verified"] is True
    receipt_read = client.get(
        f"/api/notice-radar/migrations/{plan['id']}/receipt", params={"operation": "execute"}
    )
    assert receipt_read.status_code == 200
    assert receipt_read.json()["verified_count"] == 3

    events = client.get("/api/events").json()["items"]
    assert events[0]["location"] == "教学楼 B205"
    v2_start_claim = next(item for item in v2["claims"] if item["claim_key"] == "event.start_at")
    supporting_claim_ids = {
        item["id"]
        for item in v2["claims"]
        if item["claim_key"] in {"event.end_at", "event.location"}
    }
    assert events[0]["source_claim_id"] == v2_start_claim["id"]
    assert events[0]["source_history"]
    assert supporting_claim_ids <= {
        item["claim_id"] for item in events[0]["source_history"] if item.get("role") == "supporting"
    }

    current_plan = client.get(f"/api/notice-radar/migrations/{plan['id']}").json()
    undo_body = {
        "plan_version": current_plan["version"],
        "idempotency_key": "undo-ai-exam-v2",
        "confirmation_stages": 2,
    }
    undone = _challenged_write(client, "POST", execute_path.replace("/execute", "/undo"), undo_body)
    assert undone.status_code == 200, undone.text
    undo_receipt = undone.json()
    assert undo_receipt["operation"] == "undo"
    assert undo_receipt["all_verified"] is True
    events = client.get("/api/events").json()["items"]
    assert events[0]["location"] == "教学楼 A302"
    assert events[0]["source_document_id"] == v1["id"]
    assert v2["id"] != v1["id"]
    repeated_undo = _challenged_write(
        client, "POST", execute_path.replace("/execute", "/undo"), undo_body
    )
    assert repeated_undo.status_code == 200
    assert repeated_undo.json()["all_verified"] is True


def test_version_chain_requires_explicit_current_predecessor_and_is_idempotent(
    client: TestClient,
) -> None:
    v1, v2 = _create_demo_chain(client)
    series_id = v2["series_id"]
    duplicate_series = {
        "canonical_key": "ai-exam-2026",
        "title": "另一标题不会静默新建版本链",
        "department": "计算机学院",
        "source_key": "other",
    }
    response = confirmed_write(client, "POST", "/api/notice-radar/series", duplicate_series)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "notice_series_exists"
    duplicate = {
        "title": v2["title"],
        "content": (
            "适用于 2024 级人工智能专业同学。\n"
            "考试时间：2026-07-18 14:00–16:00。\n"
            "地点改为：教学楼 B205。\n"
            "请按时参加，要求携带校园卡。提前 1440 分钟提醒。"
        ),
        "revision_number": 2,
        "version_label": "v2",
        "supersedes_document_id": v1["id"],
        "applicable_group": "2024 级人工智能专业",
        "ingest_source": "seed",
    }
    path = f"/api/notice-radar/series/{series_id}/versions"
    response = confirmed_write(client, "POST", path, duplicate)
    assert response.status_code == 201
    assert response.json()["id"] == v2["id"]

    ambiguous = duplicate | {
        "content": duplicate["content"] + "\n另行通知。",
        "revision_number": 3,
        "version_label": "v3",
        "supersedes_document_id": None,
    }
    response = confirmed_write(client, "POST", path, ambiguous)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "version_confirmation_required"

    wrong_predecessor = ambiguous | {
        "content": ambiguous["content"] + "\n错误前驱。",
        "supersedes_document_id": v1["id"],
    }
    response = confirmed_write(client, "POST", path, wrong_predecessor)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ambiguous_version_chain"

    duplicate_content = duplicate | {
        "revision_number": 3,
        "version_label": "v3",
        "supersedes_document_id": v2["id"],
    }
    response = confirmed_write(client, "POST", path, duplicate_content)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_document"

    old_claim = next(item for item in v1["claims"] if item["claim_key"] == "event.start_at")
    invalid_lineage = {
        "title": "错误来源不会写入",
        "due_at": "2026-07-18T12:00:00+08:00",
        "source_type": "document",
        "source_document_id": v2["id"],
        "source_chunk_id": old_claim["chunk_id"],
        "source_claim_id": old_claim["id"],
    }
    response = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        invalid_lineage,
        headers={"Idempotency-Key": "invalid-cross-document-lineage"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_source_lineage"


def test_demo_jwt_cors_allows_exact_origin_with_credentials(client: TestClient) -> None:
    response = client.options(
        "/api/notice-radar",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_demo_jwt_cors_rejects_untrusted_browser_origin(client: TestClient) -> None:
    response = client.options(
        "/api/notice-radar",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_applicability_mismatch_does_not_create_false_impacts(client: TestClient) -> None:
    _create_demo_chain(client, applicable=False)
    assert client.get("/api/notice-radar").json()["items"] == []
    impacts = client.get("/api/notice-radar/impacts")
    assert impacts.status_code == 200
    assert impacts.json()["total"] == 0


def test_low_confidence_change_requires_review_before_propagation(client: TestClient) -> None:
    _create_demo_chain(client, uncertain=True)
    card = client.get("/api/notice-radar").json()["items"][0]
    change_set = client.get(f"/api/notice-radar/changes/{card['change_set_id']}").json()
    assert change_set["items"]
    assert all(item["review_state"] == "pending" for item in change_set["items"])
    assert card["needs_review"] is True
    preview_path = f"/api/notice-radar/changes/{card['change_set_id']}/migration-preview"
    response = _challenged_write(client, "POST", preview_path, None)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "change_review_required"
    for item in change_set["items"]:
        reviewed = _challenged_write(
            client,
            "PATCH",
            f"/api/notice-radar/changes/items/{item['id']}/review",
            {"decision": "approved"},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["review_state"] == "approved"
    detected = _challenged_write(
        client,
        "POST",
        f"/api/notice-radar/changes/{card['change_set_id']}/impacts/detect",
        None,
    )
    assert detected.status_code == 200
    assert detected.json()["total"] > 0
    preview = _challenged_write(client, "POST", preview_path, None)
    assert preview.status_code == 200


def test_stale_entity_rolls_back_entire_bundle(client: TestClient) -> None:
    _create_demo_chain(client)
    card = client.get("/api/notice-radar").json()["items"][0]
    preview_path = f"/api/notice-radar/changes/{card['change_set_id']}/migration-preview"
    plan = _challenged_write(client, "POST", preview_path, None).json()
    event = client.get("/api/events").json()["items"][0]
    changed = confirmed_write(
        client,
        "PATCH",
        f"/api/events/{event['id']}",
        {"location": "用户手动修改的地点", "expected_version": event["version"]},
        headers={"Idempotency-Key": "manual-event-change-after-preview"},
    )
    assert changed.status_code == 200, changed.text
    task_due_before = [item["due_at"] for item in client.get("/api/tasks").json()["items"]]
    execute_body = {
        "plan_version": plan["version"],
        "idempotency_key": "stale-bundle-execution",
        "allow_conflicts": False,
        "confirmation_stages": plan["required_confirmations"],
    }
    response = _challenged_write(
        client,
        "POST",
        f"/api/notice-radar/migrations/{plan['id']}/execute",
        execute_body,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "entity_version_conflict"
    assert [item["due_at"] for item in client.get("/api/tasks").json()["items"]] == task_due_before
    assert client.get(f"/api/notice-radar/migrations/{plan['id']}").json()["status"] == "ready"


def test_conflict_override_requires_bound_two_stage_challenge_and_replay_fails(
    client: TestClient,
) -> None:
    _create_demo_chain(client)
    conflict = {
        "title": "已有课程",
        "start_at": "2026-07-18T14:30:00+08:00",
        "end_at": "2026-07-18T15:30:00+08:00",
        "location": "教学楼 C101",
    }
    response = confirmed_write(
        client,
        "POST",
        "/api/events",
        conflict,
        headers={"Idempotency-Key": "existing-conflicting-event"},
    )
    assert response.status_code == 201, response.text
    card = client.get("/api/notice-radar").json()["items"][0]
    preview_path = f"/api/notice-radar/changes/{card['change_set_id']}/migration-preview"
    plan = _challenged_write(client, "POST", preview_path, None).json()
    assert plan["conflicts"]
    assert plan["required_confirmations"] == 2
    path = f"/api/notice-radar/migrations/{plan['id']}/execute"
    blocked_body = {
        "plan_version": plan["version"],
        "idempotency_key": "conflict-blocked-execute",
        "allow_conflicts": False,
        "confirmation_stages": 2,
    }
    blocked = _challenged_write(client, "POST", path, blocked_body)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "calendar_conflict"
    body = {
        "plan_version": plan["version"],
        "idempotency_key": "conflict-override-execute",
        "allow_conflicts": True,
        "confirmation_stages": 2,
    }
    issued = client.post(
        "/api/auth/write-challenges", json={"method": "POST", "path": path, "body": body}
    ).json()
    advanced = client.post(
        "/api/auth/write-challenges/advance", json={"challenge": issued["challenge"]}
    ).json()
    tampered = client.post(
        path,
        json=body | {"idempotency_key": "tampered-execution-key"},
        headers={"X-Write-Challenge": advanced["challenge"]},
    )
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "invalid_write_challenge"

    issued_ok = client.post(
        "/api/auth/write-challenges", json={"method": "POST", "path": path, "body": body}
    ).json()
    advanced_ok = client.post(
        "/api/auth/write-challenges/advance", json={"challenge": issued_ok["challenge"]}
    ).json()
    executed = client.post(path, json=body, headers={"X-Write-Challenge": advanced_ok["challenge"]})
    assert executed.status_code == 200, executed.text
    assert executed.json()["all_verified"] is True
    replay = client.post(path, json=body, headers={"X-Write-Challenge": advanced_ok["challenge"]})
    assert replay.status_code == 409


def test_migration_state_errors_are_stable_and_idempotency_is_key_bound(
    client: TestClient,
) -> None:
    _create_demo_chain(client)
    card = client.get("/api/notice-radar").json()["items"][0]
    preview_path = f"/api/notice-radar/changes/{card['change_set_id']}/migration-preview"
    plan = _challenged_write(client, "POST", preview_path, None).json()
    execute_path = f"/api/notice-radar/migrations/{plan['id']}/execute"
    undo_path = execute_path.replace("/execute", "/undo")

    undo_before_execute = _challenged_write(
        client,
        "POST",
        undo_path,
        {
            "plan_version": plan["version"],
            "idempotency_key": "undo-before-execute",
            "confirmation_stages": 2,
        },
    )
    assert undo_before_execute.status_code == 409
    assert undo_before_execute.json()["error"]["code"] == "migration_not_undoable"

    stale = {
        "plan_version": plan["version"] + 1,
        "idempotency_key": "stale-plan-version",
        "allow_conflicts": False,
        "confirmation_stages": plan["required_confirmations"],
    }
    response = _challenged_write(client, "POST", execute_path, stale)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "migration_plan_stale"

    wrong_stage = stale | {
        "plan_version": plan["version"],
        "idempotency_key": "wrong-confirmation-stage",
        "confirmation_stages": 2,
    }
    response = _challenged_write(client, "POST", execute_path, wrong_stage)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "confirmation_stage_mismatch"

    execute_body = {
        "plan_version": plan["version"],
        "idempotency_key": "state-machine-execute",
        "allow_conflicts": False,
        "confirmation_stages": plan["required_confirmations"],
    }
    assert _challenged_write(client, "POST", execute_path, execute_body).status_code == 200
    response = _challenged_write(
        client,
        "POST",
        execute_path,
        execute_body | {"idempotency_key": "different-execution-key"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "migration_already_executed"

    current = client.get(f"/api/notice-radar/migrations/{plan['id']}").json()
    stale_undo = {
        "plan_version": current["version"] + 1,
        "idempotency_key": "stale-undo-version",
        "confirmation_stages": 2,
    }
    response = _challenged_write(client, "POST", undo_path, stale_undo)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "migration_plan_stale"
    undo_body = stale_undo | {
        "plan_version": current["version"],
        "idempotency_key": "state-machine-undo",
    }
    assert _challenged_write(client, "POST", undo_path, undo_body).status_code == 200
    response = _challenged_write(
        client,
        "POST",
        undo_path,
        undo_body | {"idempotency_key": "different-undo-key"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "migration_already_undone"


def test_notice_not_found_no_impacts_and_version_validation_paths(client: TestClient) -> None:
    for path in (
        "/api/notice-radar/series/missing/timeline",
        "/api/notice-radar/documents/missing/claims",
        "/api/notice-radar/changes/missing",
        "/api/notice-radar/migrations/missing",
    ):
        assert client.get(path).status_code == 404

    _create_demo_chain(client, applicable=False)
    assert client.get("/api/notice-radar").json()["items"] == []

    series = confirmed_write(
        client,
        "POST",
        "/api/notice-radar/series",
        {"canonical_key": "non-monotonic-demo", "title": "非连续版本演示"},
    ).json()
    path = f"/api/notice-radar/series/{series['id']}/versions"
    first = {
        "title": "非连续版本演示",
        "content": "适用于全体学生。考试时间：2026-08-01 09:00–11:00。",
        "revision_number": 5,
        "version_label": "v5",
        "supersedes_document_id": None,
        "ingest_source": "seed",
    }
    first_response = confirmed_write(client, "POST", path, first)
    assert first_response.status_code == 201
    first_document = first_response.json()
    backwards = first | {
        "content": first["content"] + "地点：A101。",
        "revision_number": 4,
        "version_label": "v4",
        "supersedes_document_id": first_document["id"],
    }
    response = confirmed_write(client, "POST", path, backwards)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "non_monotonic_revision"

    new_series = confirmed_write(
        client,
        "POST",
        "/api/notice-radar/series",
        {"canonical_key": "unexpected-predecessor", "title": "错误首版前驱"},
    ).json()
    response = confirmed_write(
        client,
        "POST",
        f"/api/notice-radar/series/{new_series['id']}/versions",
        first | {"supersedes_document_id": first_document["id"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "unexpected_supersedes_document"
