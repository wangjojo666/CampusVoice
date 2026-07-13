import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.types import utc_now
from app.models.entities import (
    ActionLog,
    Conversation,
    CorrectionRecord,
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
from app.models.enums import ActionType, EntityType, PendingActionState, RiskLevel
from app.services.errors import NotFoundError
from app.services.privacy import PrivacyService


def _factory(client: TestClient) -> async_sessionmaker[AsyncSession]:
    return client.app.state.session_factory


def _portal_call(client: TestClient, function: Any, *args: Any) -> Any:
    assert client.portal is not None
    return client.portal.call(function, *args)


async def _seed_other_user(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session, session.begin():
        session.add(User(id="user_other", display_name="Other User"))
        session.add(UserSettings(user_id="user_other", timezone="Asia/Shanghai"))
        await session.flush()
        session.add(Task(user_id="user_other", title="Other user's private task"))


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
    assert {item["title"] for item in body["data"]["tasks"]} == {"Current user's task"}
    assert all(item["title"] != "Other user's private task" for item in body["data"]["tasks"])
    assert body["data"]["document_chunks"]
    assert body["data"]["documents"][0]["source_url"] == "https://example.edu/notice"
    assert "embedding" not in body["data"]["document_chunks"][0]
    assert "confirmation_history" not in body["data"]["pending_actions"][0]
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
    assert result["deleted_counts"]["tasks"] == 1
    assert result["deleted_counts"]["documents"] == 1
    assert result["deleted_counts"]["user_settings"] == 1
    assert result["deleted_counts"]["pending_actions"] >= 2
    assert result["deleted_counts"]["confirmation_nonces"] == 1
    assert result["deleted_counts"]["websocket_tickets"] == 1
    assert result["deleted_counts"]["write_challenges"] >= 1
    assert _portal_call(client, _user_exists, _factory(client), "user_demo") is True
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
