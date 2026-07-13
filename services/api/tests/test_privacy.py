import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.types import utc_now
from app.models.entities import (
    ActionLog,
    CalendarEvent,
    Conversation,
    CorrectionRecord,
    Document,
    DocumentChunk,
    ImpactCase,
    ImpactMigrationItem,
    ImpactMigrationPlan,
    NoticeChangeItem,
    NoticeChangeSet,
    NoticeClaim,
    NoticeSeries,
    PendingAction,
    PrivacyDeletionChallenge,
    Task,
    Transcription,
    User,
    UserSettings,
    VoiceSession,
    WebSocketTicket,
    WriteChallenge,
)
from app.models.enums import ActionType, EntityType, PendingActionState, RiskLevel, SourceType
from app.services.errors import NotFoundError
from app.services.privacy import PrivacyService


def _factory(client: TestClient) -> async_sessionmaker[AsyncSession]:
    return client.app.state.session_factory


def _portal_call(client: TestClient, function: Any, *args: Any) -> Any:
    assert client.portal is not None
    return client.portal.call(function, *args)


async def _seed_notice_graph(
    factory: async_sessionmaker[AsyncSession],
    user_id: str,
    prefix: str,
    *,
    create_user: bool = False,
    task_title: str = "Notice-backed task",
) -> None:
    async with factory() as session, session.begin():
        if create_user:
            session.add(User(id=user_id, display_name=f"{prefix.title()} User"))
            session.add(UserSettings(user_id=user_id, timezone="Asia/Shanghai"))
            await session.flush()

        series = NoticeSeries(
            id=f"nss_{prefix}",
            user_id=user_id,
            canonical_key=f"{prefix}-exam-notice",
            normalized_title="考试安排",
            department="教务处",
            source_key=f"source-{prefix}",
        )
        session.add(series)
        await session.flush()

        first_document = Document(
            id=f"doc_{prefix}_v1",
            user_id=user_id,
            title="考试安排 v1",
            department="教务处",
            source_url=("https://student:source-token@example.edu/notice?access_token=query-token"),
            version="v1",
            file_type="text/plain",
            storage_path=f"/private/storage-secret/{prefix}/v1.txt",
            content_sha256=sha256(f"{prefix}-v1".encode()).hexdigest(),
            series_id=series.id,
            revision_number=1,
            is_current=False,
            ingest_source="privacy-test",
        )
        second_document = Document(
            id=f"doc_{prefix}_v2",
            user_id=user_id,
            title="考试安排 v2",
            department="教务处",
            source_url="https://example.edu/notice?token=second-query-token",
            version="v2",
            file_type="text/plain",
            storage_path=f"/private/storage-secret/{prefix}/v2.txt",
            content_sha256=sha256(f"{prefix}-v2".encode()).hexdigest(),
            series_id=series.id,
            supersedes_document_id=first_document.id,
            revision_number=2,
            is_current=True,
            ingest_source="privacy-test",
        )
        session.add_all([first_document, second_document])
        await session.flush()

        first_chunk = DocumentChunk(
            id=f"chk_{prefix}_v1",
            document_id=first_document.id,
            ordinal=0,
            content="考试时间为 9 月 11 日 09:00，地点 A302。",
            embedding=[0.1, 0.2],
            metadata_json={"page": 1, "api_key": "chunk-secret"},
        )
        second_chunk = DocumentChunk(
            id=f"chk_{prefix}_v2",
            document_id=second_document.id,
            ordinal=0,
            content="考试时间为 9 月 11 日 14:00，地点 B205。",
            embedding=[0.3, 0.4],
            metadata_json={"page": 1, "authorization": "chunk-authorization"},
        )
        session.add_all([first_chunk, second_chunk])
        await session.flush()

        first_claim = NoticeClaim(
            id=f"ncl_{prefix}_v1",
            user_id=user_id,
            document_id=first_document.id,
            chunk_id=first_chunk.id,
            claim_key="event.start_at",
            claim_type="start_at",
            value_json={"value": "2026-09-11T09:00:00+08:00", "api_key": "claim-secret"},
            normalized_value_json={
                "value": "2026-09-11T01:00:00Z",
                "secret": "normalized-secret",
            },
            audience_rule_json={"major": ["人工智能"], "token": "audience-token"},
            confidence=0.99,
            evidence_start=0,
            evidence_end=20,
            extractor_version="deterministic-v1",
            review_state="approved",
        )
        second_claim = NoticeClaim(
            id=f"ncl_{prefix}_v2",
            user_id=user_id,
            document_id=second_document.id,
            chunk_id=second_chunk.id,
            claim_key="event.start_at",
            claim_type="start_at",
            value_json={"value": "2026-09-11T14:00:00+08:00", "password": "claim-password"},
            normalized_value_json={"value": "2026-09-11T06:00:00Z"},
            audience_rule_json={"major": ["人工智能"]},
            confidence=0.99,
            evidence_start=0,
            evidence_end=20,
            extractor_version="deterministic-v1",
            review_state="approved",
        )
        session.add_all([first_claim, second_claim])
        await session.flush()

        change_set = NoticeChangeSet(
            id=f"ncs_{prefix}",
            user_id=user_id,
            series_id=series.id,
            from_document_id=first_document.id,
            to_document_id=second_document.id,
            algorithm_version="semantic-diff-v1",
            status="ready",
        )
        session.add(change_set)
        await session.flush()
        change_item = NoticeChangeItem(
            id=f"nci_{prefix}",
            user_id=user_id,
            change_set_id=change_set.id,
            claim_key="event.start_at",
            change_type="modified",
            before_claim_id=first_claim.id,
            after_claim_id=second_claim.id,
            severity="high",
            confidence=0.99,
            review_state="approved",
        )
        session.add(change_item)
        await session.flush()

        task = Task(
            id=f"tsk_{prefix}_notice",
            user_id=user_id,
            title=task_title,
            due_at=datetime(2026, 9, 11, 1, tzinfo=UTC),
            source_type=SourceType.DOCUMENT,
            source_document_id=first_document.id,
            source_chunk_id=first_chunk.id,
            source_claim_id=first_claim.id,
            source_history=[
                {
                    "document_id": first_document.id,
                    "claim_id": first_claim.id,
                    "token": "history-secret",
                }
            ],
        )
        event = CalendarEvent(
            id=f"evt_{prefix}_notice",
            user_id=user_id,
            title="考试",
            start_at=datetime(2026, 9, 11, 1, tzinfo=UTC),
            end_at=datetime(2026, 9, 11, 3, tzinfo=UTC),
            location="A302",
            source_type=SourceType.DOCUMENT,
            source_document_id=first_document.id,
            source_chunk_id=first_chunk.id,
            source_claim_id=first_claim.id,
            source_history=[{"document_id": first_document.id, "nonce": "event-history-secret"}],
        )
        plan = ImpactMigrationPlan(
            id=f"mpl_{prefix}",
            user_id=user_id,
            change_set_id=change_set.id,
            generation=3,
            status="ready",
            risk_level="high",
            conflicts_json=[{"kind": "overlap", "authorization": "plan-secret"}],
            verification_json={"status": "pending", "token_hash": "verification-secret"},
            execute_receipt_json={
                "operation": "execute",
                "verified": True,
                "token": "execute-receipt-secret",
            },
            undo_receipt_json={
                "operation": "undo",
                "verified": True,
                "authorization": "undo-receipt-secret",
            },
            execution_idempotency_key=f"execution-secret-{prefix}",
            undo_idempotency_key=f"undo-secret-{prefix}",
        )
        session.add_all([task, event, plan])
        await session.flush()

        session.add_all(
            [
                ImpactCase(
                    id=f"imp_{prefix}_task",
                    user_id=user_id,
                    change_item_id=change_item.id,
                    entity_type="task",
                    entity_id=task.id,
                    entity_version=task.version,
                    reason="任务截止时间依赖旧通知",
                    severity="high",
                    current_snapshot={"due_at": "09:00", "api_key": "impact-secret"},
                    proposed_patch={"due_at": "14:00", "password": "impact-password"},
                    recommended_action="apply",
                    requires_manual_review=False,
                    migration_plan_id=plan.id,
                ),
                ImpactCase(
                    id=f"imp_{prefix}_event",
                    user_id=user_id,
                    change_item_id=change_item.id,
                    entity_type="event",
                    entity_id=event.id,
                    entity_version=event.version,
                    reason="日程时间依赖旧通知",
                    severity="high",
                    current_snapshot={"start_at": "09:00"},
                    proposed_patch={"start_at": "14:00"},
                    recommended_action="manual_review",
                    requires_manual_review=True,
                    migration_plan_id=plan.id,
                ),
                ImpactMigrationItem(
                    id=f"mpi_{prefix}_task",
                    plan_id=plan.id,
                    user_id=user_id,
                    entity_type="task",
                    entity_id=task.id,
                    expected_version=task.version,
                    before_snapshot={"due_at": "09:00", "secret": "migration-secret"},
                    proposed_patch={"due_at": "14:00"},
                    after_snapshot={"due_at": "14:00", "api_key": "after-secret"},
                    source_claim_ids=[second_claim.id],
                    verification_json={"status": "pending", "nonce": "item-secret"},
                    execute_verification_json={
                        "verified": True,
                        "token_hash": "execute-verification-secret",
                    },
                    undo_verification_json={
                        "verified": False,
                        "nonce": "undo-verification-secret",
                    },
                ),
                ImpactMigrationItem(
                    id=f"mpi_{prefix}_event",
                    plan_id=plan.id,
                    user_id=user_id,
                    entity_type="event",
                    entity_id=event.id,
                    expected_version=event.version,
                    before_snapshot={"start_at": "09:00"},
                    proposed_patch={"start_at": "14:00"},
                    after_snapshot={"start_at": "14:00"},
                    source_claim_ids=[second_claim.id],
                    verification_json={"status": "pending"},
                    execute_verification_json={"verified": True},
                    undo_verification_json={"verified": True},
                ),
            ]
        )


async def _seed_other_user(factory: async_sessionmaker[AsyncSession]) -> None:
    await _seed_notice_graph(
        factory,
        "user_other",
        "other",
        create_user=True,
        task_title="Other user's private task",
    )


async def _v03_graph_counts(
    factory: async_sessionmaker[AsyncSession], user_id: str
) -> dict[str, int]:
    models = {
        "notice_series": NoticeSeries,
        "notice_claims": NoticeClaim,
        "notice_change_sets": NoticeChangeSet,
        "notice_change_items": NoticeChangeItem,
        "impact_cases": ImpactCase,
        "impact_migration_plans": ImpactMigrationPlan,
        "impact_migration_items": ImpactMigrationItem,
    }
    async with factory() as session:
        return {
            name: int(
                await session.scalar(
                    select(func.count()).select_from(model).where(model.user_id == user_id)
                )
                or 0
            )
            for name, model in models.items()
        }


async def _privacy_counts(
    factory: async_sessionmaker[AsyncSession], user_id: str
) -> dict[str, int]:
    async with factory() as session:
        return await PrivacyService._count_business_data(session, user_id)


async def _seed_retention_records(
    factory: async_sessionmaker[AsyncSession],
    user_id: str,
    old: datetime,
    recent: datetime,
) -> None:
    async with factory() as session, session.begin():
        session.add(
            VoiceSession(
                id="voi_retention",
                user_id=user_id,
                asr_provider="test",
                asr_model="synthetic",
            )
        )
        await session.flush()
        session.add_all(
            [
                Transcription(
                    id="trn_old",
                    voice_session_id="voi_retention",
                    sequence=1,
                    text="old transcript",
                    created_at=old,
                ),
                Transcription(
                    id="trn_recent",
                    voice_session_id="voi_retention",
                    sequence=2,
                    text="recent transcript",
                    created_at=recent,
                ),
                CorrectionRecord(
                    id="cor_old",
                    user_id=user_id,
                    original_text="old",
                    corrected_text="old corrected",
                    reason="test",
                    confidence=1,
                    created_at=old,
                ),
                CorrectionRecord(
                    id="cor_recent",
                    user_id=user_id,
                    original_text="recent",
                    corrected_text="recent corrected",
                    reason="test",
                    confidence=1,
                    created_at=recent,
                ),
                Conversation(
                    id="cnv_old",
                    user_id=user_id,
                    context={"turns": [{"source_text": "old"}]},
                    created_at=old,
                    updated_at=old,
                ),
                Conversation(
                    id="cnv_recent",
                    user_id=user_id,
                    context={"turns": [{"source_text": "recent"}]},
                    created_at=recent,
                    updated_at=recent,
                ),
                PendingAction(
                    id="act_old_terminal",
                    user_id=user_id,
                    action_type=ActionType.CREATE_TASK,
                    entity_type=EntityType.TASK,
                    payload={"title": "old terminal"},
                    state=PendingActionState.EXECUTED,
                    risk_level=RiskLevel.MEDIUM,
                    required_confirmations=1,
                    expires_at=old,
                    created_at=old,
                    updated_at=old,
                ),
                PendingAction(
                    id="act_old_active",
                    user_id=user_id,
                    action_type=ActionType.CREATE_TASK,
                    entity_type=EntityType.TASK,
                    payload={"title": "old but active"},
                    state=PendingActionState.READY,
                    risk_level=RiskLevel.MEDIUM,
                    required_confirmations=1,
                    expires_at=recent + timedelta(days=1),
                    created_at=old,
                    updated_at=old,
                ),
                ActionLog(
                    id="log_old",
                    user_id=user_id,
                    action_type=ActionType.CREATE_TASK,
                    entity_type=EntityType.TASK,
                    risk_level=RiskLevel.MEDIUM,
                    source_text="old log",
                    created_at=old,
                ),
                ActionLog(
                    id="log_recent",
                    user_id=user_id,
                    action_type=ActionType.CREATE_TASK,
                    entity_type=EntityType.TASK,
                    risk_level=RiskLevel.MEDIUM,
                    source_text="recent log",
                    created_at=recent,
                ),
            ]
        )


async def _challenge_row(
    factory: async_sessionmaker[AsyncSession], challenge_id: str
) -> PrivacyDeletionChallenge | None:
    async with factory() as session:
        return await session.get(PrivacyDeletionChallenge, challenge_id)


async def _expire_challenge(factory: async_sessionmaker[AsyncSession], challenge_id: str) -> None:
    async with factory() as session, session.begin():
        entity = await session.get(PrivacyDeletionChallenge, challenge_id)
        assert entity is not None
        entity.expires_at = utc_now() - timedelta(seconds=1)


async def _expire_ephemeral_credentials(
    factory: async_sessionmaker[AsyncSession], user_id: str
) -> None:
    expired = utc_now() - timedelta(seconds=1)
    async with factory() as session, session.begin():
        await session.execute(
            update(WebSocketTicket)
            .where(WebSocketTicket.user_id == user_id)
            .values(expires_at=expired)
        )
        await session.execute(
            update(WriteChallenge)
            .where(WriteChallenge.user_id == user_id)
            .values(expires_at=expired)
        )


async def _user_exists(factory: async_sessionmaker[AsyncSession], user_id: str) -> bool:
    async with factory() as session:
        return await session.get(User, user_id) is not None


def _create_task(client: TestClient, title: str) -> None:
    body = {"title": title}
    challenge = client.post(
        "/api/auth/write-challenges",
        json={"method": "POST", "path": "/api/tasks", "body": body},
    )
    assert challenge.status_code == 200, challenge.text
    response = client.post(
        "/api/tasks",
        json=body,
        headers={"X-Write-Challenge": challenge.json()["challenge"]},
    )
    assert response.status_code == 201, response.text


def _upload_document(
    client: TestClient,
    title: str,
    *,
    source_url: str | None = None,
) -> None:
    data = {"title": title}
    if source_url is not None:
        data["source_url"] = source_url
    response = client.post(
        "/api/documents",
        files={"file": ("notice.txt", "synthetic notice content", "text/plain")},
        data=data,
    )
    assert response.status_code == 201, response.text


def test_export_is_user_scoped_no_store_and_excludes_security_material(
    client: TestClient,
) -> None:
    _create_task(client, "Current user's task")
    _upload_document(
        client,
        "Synthetic notice",
        source_url="https://student:source-token@example.edu/notice?access_token=query-token",
    )
    _portal_call(client, _seed_notice_graph, _factory(client), "user_demo", "current")
    _portal_call(client, _seed_other_user, _factory(client))

    prepared = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "Pending export task"}},
    )
    assert prepared.status_code == 201
    action_id = prepared.json()["id"]
    issued = client.post(f"/api/actions/{action_id}/challenge")
    action_challenge = issued.json()["challenge"]
    confirmed = client.post(
        f"/api/actions/{action_id}/confirm",
        json={"confirmed": True, "challenge": action_challenge},
    )
    assert confirmed.status_code == 200, confirmed.text
    ticket = client.post(
        "/api/auth/ws-ticket",
        headers={"Origin": "http://localhost:3000"},
    )
    assert ticket.status_code == 200, ticket.text
    raw_ticket = ticket.json()["ticket"]

    response = client.get("/api/privacy/export")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].endswith('campusvoice-data-export.json"')
    body = response.json()
    assert body["user"]["id"] == "user_demo"
    assert {item["title"] for item in body["data"]["tasks"]} == {
        "Current user's task",
        "Notice-backed task",
    }
    assert all(item["title"] != "Other user's private task" for item in body["data"]["tasks"])
    assert body["data"]["document_chunks"]
    assert {item["source_url"] for item in body["data"]["documents"] if item["source_url"]} == {
        "https://example.edu/notice"
    }
    assert all("embedding" not in item for item in body["data"]["document_chunks"])
    assert "confirmation_history" not in body["data"]["pending_actions"][0]

    expected_allowlists = {
        "notice_series": {
            "id",
            "canonical_key",
            "normalized_title",
            "department",
            "source_key",
            "created_at",
            "updated_at",
        },
        "notice_claims": {
            "id",
            "document_id",
            "chunk_id",
            "claim_key",
            "claim_type",
            "value_json",
            "normalized_value_json",
            "audience_rule_json",
            "confidence",
            "evidence_start",
            "evidence_end",
            "extractor_version",
            "review_state",
            "created_at",
        },
        "notice_change_sets": {
            "id",
            "series_id",
            "from_document_id",
            "to_document_id",
            "algorithm_version",
            "status",
            "created_at",
        },
        "notice_change_items": {
            "id",
            "change_set_id",
            "claim_key",
            "change_type",
            "before_claim_id",
            "after_claim_id",
            "severity",
            "confidence",
            "review_state",
            "created_at",
        },
        "impact_cases": {
            "id",
            "change_item_id",
            "entity_type",
            "entity_id",
            "entity_version",
            "reason",
            "severity",
            "current_snapshot",
            "proposed_patch",
            "recommended_action",
            "requires_manual_review",
            "status",
            "migration_plan_id",
            "detected_at",
            "resolved_at",
        },
        "impact_migration_plans": {
            "id",
            "change_set_id",
            "generation",
            "status",
            "risk_level",
            "conflicts_json",
            "verification_json",
            "execute_receipt_json",
            "undo_receipt_json",
            "version",
            "executed_at",
            "undone_at",
            "created_at",
            "updated_at",
        },
        "impact_migration_items": {
            "id",
            "plan_id",
            "entity_type",
            "entity_id",
            "expected_version",
            "before_snapshot",
            "proposed_patch",
            "after_snapshot",
            "source_claim_ids",
            "verification_json",
            "execute_verification_json",
            "undo_verification_json",
            "created_at",
        },
    }
    for category, allowlist in expected_allowlists.items():
        assert body["data"][category]
        assert all(set(item) == allowlist for item in body["data"][category])

    assert {item["id"] for item in body["data"]["notice_series"]} == {"nss_current"}
    assert {item["id"] for item in body["data"]["notice_claims"]} == {
        "ncl_current_v1",
        "ncl_current_v2",
    }
    assert {item["id"] for item in body["data"]["notice_change_sets"]} == {"ncs_current"}
    assert {item["id"] for item in body["data"]["notice_change_items"]} == {"nci_current"}
    assert {item["id"] for item in body["data"]["impact_cases"]} == {
        "imp_current_task",
        "imp_current_event",
    }
    assert {item["id"] for item in body["data"]["impact_migration_plans"]} == {"mpl_current"}
    assert {item["id"] for item in body["data"]["impact_migration_items"]} == {
        "mpi_current_task",
        "mpi_current_event",
    }

    exported_impacts = {item["id"]: item for item in body["data"]["impact_cases"]}
    assert exported_impacts["imp_current_task"]["recommended_action"] == "apply"
    assert exported_impacts["imp_current_task"]["requires_manual_review"] is False
    assert exported_impacts["imp_current_event"]["recommended_action"] == "manual_review"
    assert exported_impacts["imp_current_event"]["requires_manual_review"] is True

    exported_plan = body["data"]["impact_migration_plans"][0]
    assert exported_plan["generation"] == 3
    assert exported_plan["execute_receipt_json"] == {
        "operation": "execute",
        "verified": True,
    }
    assert exported_plan["undo_receipt_json"] == {
        "operation": "undo",
        "verified": True,
    }
    assert "execution_idempotency_key" not in exported_plan
    assert "undo_idempotency_key" not in exported_plan

    exported_items = {item["id"]: item for item in body["data"]["impact_migration_items"]}
    assert exported_items["mpi_current_task"]["execute_verification_json"] == {"verified": True}
    assert exported_items["mpi_current_task"]["undo_verification_json"] == {"verified": False}

    serialized = json.dumps(body, ensure_ascii=False)
    for forbidden in (
        action_challenge,
        raw_ticket,
        "nonce_hash",
        "ticket_hash",
        "token_hash",
        "api_key",
        "storage_path",
        "audio_reference",
        "source-token",
        "query-token",
        "second-query-token",
        "chunk-secret",
        "chunk-authorization",
        "claim-secret",
        "claim-password",
        "normalized-secret",
        "audience-token",
        "history-secret",
        "event-history-secret",
        "plan-secret",
        "verification-secret",
        "execution-secret-current",
        "undo-secret-current",
        "impact-secret",
        "impact-password",
        "migration-secret",
        "after-secret",
        "item-secret",
        "execute-receipt-secret",
        "undo-receipt-secret",
        "execute-verification-secret",
        "undo-verification-secret",
        "doc_other_v1",
    ):
        assert forbidden not in serialized


def test_retention_deletes_only_expired_current_user_records(client: TestClient) -> None:
    now = datetime.now(UTC)
    _portal_call(
        client,
        _seed_retention_records,
        _factory(client),
        "user_demo",
        now - timedelta(days=200),
        now,
    )
    ticket = client.post(
        "/api/auth/ws-ticket",
        headers={"Origin": "http://localhost:3000"},
    )
    assert ticket.status_code == 200, ticket.text
    write_challenge = client.post(
        "/api/auth/write-challenges",
        json={"method": "POST", "path": "/api/tasks", "body": {"title": "expires"}},
    )
    assert write_challenge.status_code == 200, write_challenge.text
    _portal_call(client, _expire_ephemeral_credentials, _factory(client), "user_demo")

    response = client.post("/api/privacy/retention/run")

    assert response.status_code == 200, response.text
    counts = response.json()["deleted_counts"]
    assert counts["transcriptions"] == 1
    assert counts["correction_records"] == 1
    assert counts["conversations"] == 1
    assert counts["terminal_pending_actions"] == 1
    assert counts["action_logs"] == 1
    assert counts["expired_websocket_tickets"] == 1
    assert counts["expired_write_challenges"] == 1
    exported = client.get("/api/privacy/export").json()["data"]
    assert {item["id"] for item in exported["transcriptions"]} == {"trn_recent"}
    assert {item["id"] for item in exported["correction_records"]} == {"cor_recent"}
    assert {item["id"] for item in exported["conversations"]} == {"cnv_recent"}
    assert {item["id"] for item in exported["pending_actions"]} == {"act_old_active"}
    assert {item["id"] for item in exported["action_logs"]} == {"log_recent"}


def test_clear_data_consumes_one_time_challenge_and_preserves_user(client: TestClient) -> None:
    _create_task(client, "Delete me")
    _upload_document(client, "Delete this notice")
    _portal_call(client, _seed_notice_graph, _factory(client), "user_demo", "current")
    _portal_call(client, _seed_other_user, _factory(client))
    expected_v03_counts = {
        "notice_series": 1,
        "notice_claims": 2,
        "notice_change_sets": 1,
        "notice_change_items": 1,
        "impact_cases": 2,
        "impact_migration_plans": 1,
        "impact_migration_items": 2,
    }
    assert (
        _portal_call(client, _v03_graph_counts, _factory(client), "user_demo")
        == expected_v03_counts
    )
    assert (
        _portal_call(client, _v03_graph_counts, _factory(client), "user_other")
        == expected_v03_counts
    )
    pending = client.post(
        "/api/actions/prepare",
        json={"action": "create_task", "payload": {"title": "Revoke pending action"}},
    ).json()
    action_challenge = client.post(f"/api/actions/{pending['id']}/challenge").json()
    assert (
        client.post(
            f"/api/actions/{pending['id']}/confirm",
            json={"confirmed": True, "challenge": action_challenge["challenge"]},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/auth/ws-ticket",
            headers={"Origin": "http://localhost:3000"},
        ).status_code
        == 200
    )
    issued = client.post("/api/privacy/deletion-challenges")
    assert issued.status_code == 201, issued.text
    challenge = issued.json()
    stored = _portal_call(client, _challenge_row, _factory(client), challenge["id"])
    assert stored is not None
    assert stored.nonce_hash != challenge["challenge"]

    response = client.post(
        f"/api/privacy/deletion-challenges/{challenge['id']}/confirm",
        json={
            "challenge": challenge["challenge"],
            "scope": "business_data",
            "confirmation": "DELETE_MY_DATA",
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["verified"] is True
    assert result["deleted_counts"]["tasks"] == 2
    assert result["deleted_counts"]["documents"] == 3
    assert result["deleted_counts"]["user_settings"] == 1
    assert result["deleted_counts"]["pending_actions"] >= 2
    assert result["deleted_counts"]["confirmation_nonces"] == 1
    assert result["deleted_counts"]["websocket_tickets"] == 1
    assert result["deleted_counts"]["write_challenges"] >= 1
    for table, expected in expected_v03_counts.items():
        assert result["deleted_counts"][table] == expected
    assert _portal_call(client, _user_exists, _factory(client), "user_demo") is True
    assert _portal_call(client, _user_exists, _factory(client), "user_other") is True
    remaining = _portal_call(client, _privacy_counts, _factory(client), "user_demo")
    assert all(count == 0 for count in remaining.values()), remaining
    assert _portal_call(client, _v03_graph_counts, _factory(client), "user_demo") == {
        table: 0 for table in expected_v03_counts
    }
    assert (
        _portal_call(client, _v03_graph_counts, _factory(client), "user_other")
        == expected_v03_counts
    )
    assert client.get("/api/tasks").json()["total"] == 0
    assert client.get("/api/documents").json() == []

    replay = client.post(
        f"/api/privacy/deletion-challenges/{challenge['id']}/confirm",
        json={
            "challenge": challenge["challenge"],
            "scope": "business_data",
            "confirmation": "DELETE_MY_DATA",
        },
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "privacy_challenge_replayed"


def test_deletion_challenge_rejects_mismatch_cross_user_expiry_and_scope(
    client: TestClient,
) -> None:
    _create_task(client, "Cross-user protected task")
    mismatch = client.post("/api/privacy/deletion-challenges").json()
    rejected = client.post(
        f"/api/privacy/deletion-challenges/{mismatch['id']}/confirm",
        json={
            "challenge": "x" * 43,
            "scope": "business_data",
            "confirmation": "DELETE_MY_DATA",
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "privacy_challenge_mismatch"

    service = PrivacyService(_factory(client), client.app.state.settings)
    with pytest.raises(NotFoundError):
        _portal_call(
            client,
            service.clear_user_data,
            "user_other",
            mismatch["id"],
            mismatch["challenge"],
            "business_data",
        )
    assert client.get("/api/tasks").json()["total"] == 1

    expired = client.post("/api/privacy/deletion-challenges").json()
    _portal_call(client, _expire_challenge, _factory(client), expired["id"])
    expired_response = client.post(
        f"/api/privacy/deletion-challenges/{expired['id']}/confirm",
        json={
            "challenge": expired["challenge"],
            "scope": "business_data",
            "confirmation": "DELETE_MY_DATA",
        },
    )
    assert expired_response.status_code == 409
    assert expired_response.json()["error"]["code"] == "privacy_challenge_expired"

    wrong_scope = client.post(
        f"/api/privacy/deletion-challenges/{mismatch['id']}/confirm",
        json={
            "challenge": mismatch["challenge"],
            "scope": "all_data",
            "confirmation": "DELETE_MY_DATA",
        },
    )
    assert wrong_scope.status_code == 422


def test_raw_audio_persistence_cannot_be_enabled() -> None:
    with pytest.raises(ValidationError, match="raw audio persistence"):
        Settings(env="test", store_raw_audio=True)
