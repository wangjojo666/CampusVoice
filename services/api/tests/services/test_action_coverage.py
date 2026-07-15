import asyncio
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.metrics import InMemoryMetrics
from app.db.base import Base
from app.db.session import create_database_engine, create_session_factory
from app.main import create_app
from app.models.entities import PendingAction, User
from app.models.enums import PendingActionState
from app.schemas.actions import ActionPrepareRequest, ConfirmActionRequest
from app.security.authentication import AuthenticationError, AuthPrincipal
from app.security.confirmation import ConfirmationChallengeService
from app.services.actions.service import ActionService
from app.services.errors import ConflictError
from app.services.verification.service import VerificationReport


def _prepare(
    client: TestClient,
    action: str,
    payload: dict[str, object],
    *,
    target_id: str | None = None,
    headers: dict[str, str] | None = None,
    **options: object,
) -> dict[str, object]:
    body: dict[str, object] = {"action": action, "payload": payload, **options}
    if target_id is not None:
        body["target_id"] = target_id
    response = client.post("/api/actions/prepare", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def _confirm_all(client: TestClient, prepared: dict[str, object], **headers: str) -> None:
    action_id = prepared["id"]
    for expected_stage in range(1, int(prepared["required_confirmations"]) + 1):
        issued = client.post(f"/api/actions/{action_id}/challenge", headers=headers)
        assert issued.status_code == 200, issued.text
        assert issued.json()["stage"] == expected_stage
        confirmed = client.post(
            f"/api/actions/{action_id}/confirm",
            headers=headers,
            json={"confirmed": True, "challenge": issued.json()["challenge"]},
        )
        assert confirmed.status_code == 200, confirmed.text
    assert client.get(f"/api/actions/{action_id}", headers=headers).json()["state"] == "ready"


def _execute(client: TestClient, prepared: dict[str, object], **headers: str) -> dict[str, object]:
    _confirm_all(client, prepared, **headers)
    response = client.post(f"/api/actions/{prepared['id']}/execute", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True, response.text
    return response.json()  # type: ignore[no-any-return]


@pytest.fixture
async def action_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path / 'action-service.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    async with factory() as session:
        session.add(User(id="action-user", display_name="Action User"))
        await session.commit()
    yield factory
    await engine.dispose()


def _direct_service(metrics: InMemoryMetrics | None = None) -> ActionService:
    return ActionService(
        confirmation_service=ConfirmationChallengeService(
            "action-coverage-confirmation-secret-at-least-32-bytes"
        ),
        metrics=metrics,
    )


async def _direct_prepare(
    factory: async_sessionmaker[AsyncSession],
    service: ActionService,
    action: str,
    payload: dict[str, object],
    *,
    target_id: str | None = None,
    **options: object,
) -> PendingAction:
    request_body: dict[str, object] = {
        "action": action,
        "payload": payload,
        **options,
    }
    if target_id is not None:
        request_body["target_id"] = target_id
    request = ActionPrepareRequest.model_validate(request_body)
    async with factory() as session:
        return await service.prepare(session, "action-user", request)


async def _direct_confirm_all(
    factory: async_sessionmaker[AsyncSession],
    service: ActionService,
    action: PendingAction,
) -> PendingAction:
    for _stage in range(action.required_confirmations):
        async with factory() as session:
            challenge = await service.issue_confirmation_challenge(
                session, "action-user", action.id
            )
        async with factory() as session:
            action = await service.confirm(
                session,
                "action-user",
                action.id,
                ConfirmActionRequest(confirmed=True, challenge=challenge.challenge),
            )
    assert action.state.value == "ready"
    return action


async def _direct_execute(
    factory: async_sessionmaker[AsyncSession],
    service: ActionService,
    action: PendingAction,
) -> dict[str, object]:
    action = await _direct_confirm_all(factory, service, action)
    async with factory() as session:
        result = await service.execute(session, "action-user", action.id)
    assert result.success is True
    return result.model_dump(mode="json")


@pytest.mark.asyncio
async def test_action_service_task_crud_repeat_and_undo_transactions(
    action_factory: async_sessionmaker[AsyncSession],
) -> None:
    metrics = InMemoryMetrics()
    service = _direct_service(metrics)
    created_action = await _direct_prepare(
        action_factory,
        service,
        "create_task",
        {
            "title": "Direct task",
            "course": "Reliable Systems",
            "priority": "low",
            "due_at": "2026-08-01T08:00:00Z",
        },
    )
    created = await _direct_execute(action_factory, service, created_action)
    task_id = str(created["record_id"])
    assert created["verified_fields"]["title"] is True  # type: ignore[index]

    updated_action = await _direct_prepare(
        action_factory,
        service,
        "update_task",
        {"title": "Direct task updated", "priority": "high", "expected_version": 1},
        target_id=task_id,
    )
    updated = await _direct_execute(action_factory, service, updated_action)
    assert updated["success"] is True
    async with action_factory() as session:
        repeated = await service.execute(session, "action-user", updated_action.id)
    assert repeated.model_dump(mode="json") == updated

    async with action_factory() as session:
        undo_update = await service.undo(session, "action-user", updated_action.id)
    assert undo_update.success is True
    assert undo_update.record is not None
    assert undo_update.record.title == "Direct task"
    async with action_factory() as session:
        repeated_undo = await service.undo(session, "action-user", updated_action.id)
    assert repeated_undo.success is True

    deleted_action = await _direct_prepare(
        action_factory, service, "delete_task", {}, target_id=task_id
    )
    deleted = await _direct_execute(action_factory, service, deleted_action)
    assert deleted["record"] is None

    async with action_factory() as session:
        restored = await service.undo(session, "action-user", deleted_action.id)
    assert restored.success is True
    assert restored.record is not None
    assert restored.record.title == "Direct task"

    async with action_factory() as session:
        with pytest.raises(ConflictError) as stale_create:
            await service.undo(session, "action-user", created_action.id)
    assert stale_create.value.code == "undo_version_conflict"
    async with action_factory() as session:
        current_task = await service.tasks.get(session, "action-user", task_id)
    assert current_task is not None
    assert current_task.title == "Direct task"

    immediate_create = await _direct_prepare(
        action_factory,
        service,
        "create_task",
        {"title": "Direct task removed immediately"},
    )
    await _direct_execute(action_factory, service, immediate_create)
    async with action_factory() as session:
        immediate_undo = await service.undo(session, "action-user", immediate_create.id)
    assert immediate_undo.success is True
    assert immediate_undo.record is None

    component_metrics = metrics.snapshot()["components"]
    assert any(
        item["component"] == "action"
        and item["operation"] == "execute"
        and item["outcome"] == "ok"
        and int(item["count"]) >= 4
        for item in component_metrics
    )
    assert any(
        item["component"] == "verification"
        and item["operation"] == "verify"
        and item["outcome"] == "ok"
        and int(item["count"]) >= 5
        for item in component_metrics
    )


@pytest.mark.asyncio
async def test_action_service_event_crud_and_undo_transactions(
    action_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = _direct_service()
    created_action = await _direct_prepare(
        action_factory,
        service,
        "create_event",
        {
            "title": "Direct event",
            "start_at": "2026-08-02T09:00:00Z",
            "location": "Room 401",
        },
    )
    created = await _direct_execute(action_factory, service, created_action)
    event_id = str(created["record_id"])
    assert created["verified_fields"]["end_at"] is True  # type: ignore[index]

    updated_action = await _direct_prepare(
        action_factory,
        service,
        "update_event",
        {
            "title": "Direct event updated",
            "location": "Room 402",
            "expected_version": 1,
        },
        target_id=event_id,
    )
    updated = await _direct_execute(action_factory, service, updated_action)
    assert updated["success"] is True

    async with action_factory() as session:
        undo_update = await service.undo(session, "action-user", updated_action.id)
    assert undo_update.success is True
    assert undo_update.record is not None
    assert undo_update.record.title == "Direct event"

    deleted_action = await _direct_prepare(
        action_factory, service, "delete_event", {}, target_id=event_id
    )
    deleted = await _direct_execute(action_factory, service, deleted_action)
    assert deleted["record"] is None

    async with action_factory() as session:
        restored = await service.undo(session, "action-user", deleted_action.id)
    assert restored.success is True
    assert restored.record is not None
    assert restored.record.title == "Direct event"

    async with action_factory() as session:
        with pytest.raises(ConflictError) as stale_create:
            await service.undo(session, "action-user", created_action.id)
    assert stale_create.value.code == "undo_version_conflict"
    async with action_factory() as session:
        current_event = await service.events.get(session, "action-user", event_id)
    assert current_event is not None
    assert current_event.title == "Direct event"

    immediate_create = await _direct_prepare(
        action_factory,
        service,
        "create_event",
        {"title": "Direct event removed immediately", "start_at": "2026-08-03T09:00:00Z"},
    )
    await _direct_execute(action_factory, service, immediate_create)
    async with action_factory() as session:
        immediate_undo = await service.undo(session, "action-user", immediate_create.id)
    assert immediate_undo.success is True
    assert immediate_undo.record is None


@pytest.mark.asyncio
async def test_action_service_rejects_legacy_undo_without_recovery_marker(
    action_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = _direct_service()
    created_action = await _direct_prepare(
        action_factory,
        service,
        "create_task",
        {"title": "Invalid legacy undo state"},
    )
    await _direct_execute(action_factory, service, created_action)
    async with action_factory() as session, session.begin():
        persisted = await service.actions.get_pending(session, "action-user", created_action.id)
        assert persisted is not None
        persisted.state = PendingActionState.UNDONE
        persisted.result = {"success": False, "applied": True}

    async with action_factory() as session:
        with pytest.raises(ConflictError) as rejected:
            await service.undo(session, "action-user", created_action.id)

    assert rejected.value.code == "undo_recovery_state_invalid"


@pytest.mark.asyncio
async def test_action_service_retries_failed_undo_verification_without_reapplying(
    action_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = _direct_service()
    created_action = await _direct_prepare(
        action_factory,
        service,
        "create_task",
        {"title": "Undo report original", "priority": "low"},
    )
    created = await _direct_execute(action_factory, service, created_action)
    task_id = str(created["record_id"])
    updated_action = await _direct_prepare(
        action_factory,
        service,
        "update_task",
        {"title": "Undo report changed", "priority": "high", "expected_version": 1},
        target_id=task_id,
    )
    await _direct_execute(action_factory, service, updated_action)

    original_verify = service.verifier.verify_task
    calls = 0

    async def fail_once(*args: object, **kwargs: object) -> VerificationReport:
        nonlocal calls
        calls += 1
        if calls == 1:
            return VerificationReport(False, {"title": False}, ("forced_failure",), None)
        return await original_verify(*args, **kwargs)  # type: ignore[arg-type]

    with patch.object(service.verifier, "verify_task", new=fail_once):
        async with action_factory() as session:
            first = await service.undo(session, "action-user", updated_action.id)
        async with action_factory() as session, session.begin():
            legacy = await service.actions.get_pending(session, "action-user", updated_action.id)
            assert legacy is not None
            legacy.state = PendingActionState.EXECUTED
            legacy.result = first.model_dump(mode="json")
        async with action_factory() as session:
            second = await service.undo(session, "action-user", updated_action.id)

    assert first.success is False
    assert first.retryable is True
    assert first.error == "undo_verification_failed"
    assert second.success is True
    async with action_factory() as session:
        current = await service.tasks.get(session, "action-user", task_id)
    assert current is not None
    assert current.title == "Undo report original"
    assert current.version == 3


@pytest.mark.asyncio
async def test_action_service_rotates_token_before_retrying_create_undo_verification(
    action_factory: async_sessionmaker[AsyncSession],
) -> None:
    service = _direct_service()
    created_action = await _direct_prepare(
        action_factory,
        service,
        "create_task",
        {"title": "Undo token retry"},
    )
    await _direct_execute(action_factory, service, created_action)
    original_verify = service.verifier.verify_task
    calls = 0

    async def fail_once(*args: object, **kwargs: object) -> VerificationReport:
        nonlocal calls
        calls += 1
        if calls == 1:
            return VerificationReport(False, {"absent": False}, ("forced_failure",), None)
        return await original_verify(*args, **kwargs)  # type: ignore[arg-type]

    with patch.object(service.verifier, "verify_task", new=fail_once):
        async with action_factory() as session:
            first = await service.undo(session, "action-user", created_action.id)
        async with action_factory() as session:
            second = await service.undo(session, "action-user", created_action.id)

    assert first.success is False
    assert first.retryable is True
    assert second.success is True
    async with action_factory() as session:
        removed = await service.tasks.get(session, "action-user", str(second.record_id))
    assert removed is None


def test_task_create_update_delete_execute_and_undo_lifecycle(client: TestClient) -> None:
    created_action = _prepare(
        client,
        "create_task",
        {
            "title": "Coverage task",
            "course": "Software Testing",
            "priority": "low",
            "due_at": "2026-07-25T09:00:00Z",
        },
    )
    created = _execute(client, created_action)
    task_id = str(created["record_id"])
    assert created["record"]["title"] == "Coverage task"  # type: ignore[index]

    updated_action = _prepare(
        client,
        "update_task",
        {"title": "Coverage task updated", "priority": "high", "expected_version": 1},
        target_id=task_id,
    )
    updated = _execute(client, updated_action)
    assert updated["record"]["title"] == "Coverage task updated"  # type: ignore[index]
    repeated = client.post(f"/api/actions/{updated_action['id']}/execute")
    assert repeated.status_code == 200
    assert repeated.json() == updated

    undo_update = client.post(f"/api/actions/{updated_action['id']}/undo")
    assert undo_update.status_code == 200, undo_update.text
    assert undo_update.json()["success"] is True
    assert undo_update.json()["record"]["title"] == "Coverage task"
    repeated_undo = client.post(f"/api/actions/{updated_action['id']}/undo")
    assert repeated_undo.status_code == 200
    assert repeated_undo.json() == undo_update.json()

    deleted_action = _prepare(client, "delete_task", {}, target_id=task_id)
    assert deleted_action["required_confirmations"] == 2
    deleted = _execute(client, deleted_action)
    assert deleted["record"] is None
    assert client.get("/api/tasks").json()["total"] == 0

    restored = client.post(f"/api/actions/{deleted_action['id']}/undo")
    assert restored.status_code == 200, restored.text
    assert restored.json()["success"] is True
    assert restored.json()["record"]["title"] == "Coverage task"

    removed_again = client.post(f"/api/actions/{created_action['id']}/undo")
    assert removed_again.status_code == 409, removed_again.text
    assert removed_again.json()["error"]["code"] == "undo_version_conflict"
    current_tasks = client.get("/api/tasks").json()
    assert current_tasks["total"] == 1
    assert current_tasks["items"][0]["title"] == "Coverage task"


def test_event_create_update_delete_execute_and_undo_lifecycle(client: TestClient) -> None:
    created_action = _prepare(
        client,
        "create_event",
        {
            "title": "Coverage review",
            "start_at": "2026-07-26T09:00:00Z",
            "location": "Room 101",
        },
    )
    created = _execute(client, created_action)
    event_id = str(created["record_id"])
    assert created["record"]["end_at"] == "2026-07-26T10:00:00Z"  # type: ignore[index]

    updated_action = _prepare(
        client,
        "update_event",
        {
            "title": "Coverage review updated",
            "location": "Room 202",
            "expected_version": 1,
        },
        target_id=event_id,
    )
    updated = _execute(client, updated_action)
    assert updated["record"]["location"] == "Room 202"  # type: ignore[index]

    undo_update = client.post(f"/api/actions/{updated_action['id']}/undo")
    assert undo_update.status_code == 200, undo_update.text
    assert undo_update.json()["success"] is True
    assert undo_update.json()["record"]["title"] == "Coverage review"

    deleted_action = _prepare(client, "delete_event", {}, target_id=event_id)
    assert deleted_action["required_confirmations"] == 2
    deleted = _execute(client, deleted_action)
    assert deleted["record"] is None
    assert client.get("/api/events").json()["total"] == 0

    restored = client.post(f"/api/actions/{deleted_action['id']}/undo")
    assert restored.status_code == 200, restored.text
    assert restored.json()["success"] is True
    assert restored.json()["record"]["title"] == "Coverage review"

    removed_again = client.post(f"/api/actions/{created_action['id']}/undo")
    assert removed_again.status_code == 409, removed_again.text
    assert removed_again.json()["error"]["code"] == "undo_version_conflict"
    current_events = client.get("/api/events").json()
    assert current_events["total"] == 1
    assert current_events["items"][0]["title"] == "Coverage review"


def test_confirmation_challenge_is_single_use_under_concurrency(client: TestClient) -> None:
    prepared = _prepare(
        client,
        "create_task",
        {"title": "Concurrent confirmation"},
        asr_confidence=0.4,
    )
    assert prepared["required_confirmations"] == 2
    action_id = prepared["id"]
    issued = client.post(f"/api/actions/{action_id}/challenge")
    assert issued.status_code == 200
    request_body = {"confirmed": True, "challenge": issued.json()["challenge"]}

    def confirm() -> tuple[int, str | None]:
        response = client.post(f"/api/actions/{action_id}/confirm", json=request_body)
        error = response.json().get("error", {}).get("code")
        return response.status_code, error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: confirm(), range(2)))

    assert sorted(status for status, _code in outcomes) == [200, 409]
    assert {code for status, code in outcomes if status == 409} <= {
        "confirmation_replayed",
        "confirmation_challenge_mismatch",
    }
    state = client.get(f"/api/actions/{action_id}").json()
    assert state["state"] == "awaiting_second_confirmation"
    assert state["confirmations_received"] == 1

    replayed = client.post(f"/api/actions/{action_id}/confirm", json=request_body)
    assert replayed.status_code == 409
    assert replayed.json()["error"]["code"] == "confirmation_challenge_mismatch"

    second = client.post(f"/api/actions/{action_id}/challenge")
    completed = client.post(
        f"/api/actions/{action_id}/confirm",
        json={"confirmed": True, "challenge": second.json()["challenge"]},
    )
    assert completed.status_code == 200
    assert completed.json()["state"] == "ready"
    assert client.post(f"/api/actions/{action_id}/execute").json()["success"] is True


async def _replace_action_payload(
    factory: async_sessionmaker[AsyncSession], action_id: str
) -> None:
    async with factory() as session:
        action = await session.get(PendingAction, action_id)
        assert action is not None
        action.payload = {"title": "Tampered after challenge"}
        await session.commit()


def test_confirmation_challenge_binds_payload_and_expiration(client: TestClient) -> None:
    prepared = _prepare(client, "create_task", {"title": "Payload binding"})
    action_id = str(prepared["id"])
    issued = client.post(f"/api/actions/{action_id}/challenge")
    assert issued.status_code == 200

    app = client.app
    assert isinstance(app, FastAPI)
    factory: async_sessionmaker[AsyncSession] = app.state.session_factory
    asyncio.run(_replace_action_payload(factory, action_id))
    tampered = client.post(
        f"/api/actions/{action_id}/confirm",
        json={"confirmed": True, "challenge": issued.json()["challenge"]},
    )
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "confirmation_challenge_mismatch"

    expiring = _prepare(client, "create_task", {"title": "Expiring challenge"})
    expiring_id = expiring["id"]
    expiring_challenge = client.post(f"/api/actions/{expiring_id}/challenge").json()["challenge"]
    future = datetime.now(UTC) + timedelta(hours=1)
    with patch("app.security.confirmation.datetime") as mocked_datetime:
        mocked_datetime.now.return_value = future
        mocked_datetime.fromtimestamp.side_effect = datetime.fromtimestamp
        expired = client.post(
            f"/api/actions/{expiring_id}/confirm",
            json={"confirmed": True, "challenge": expiring_challenge},
        )
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "confirmation_challenge_expired"

    declined = _prepare(client, "create_task", {"title": "Declined confirmation"})
    decline_challenge = client.post(f"/api/actions/{declined['id']}/challenge").json()["challenge"]
    response = client.post(
        f"/api/actions/{declined['id']}/confirm",
        json={"confirmed": False, "challenge": decline_challenge},
    )
    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    assert response.json()["last_error"] == "user_declined_confirmation"


class _TwoUserAuthenticator:
    _principals = {
        "alice": AuthPrincipal(
            user_id="coverage-alice",
            subject="alice",
            issuer="https://identity.test",
            display_name="Alice",
        ),
        "bob": AuthPrincipal(
            user_id="coverage-bob",
            subject="bob",
            issuer="https://identity.test",
            display_name="Bob",
        ),
    }

    async def authenticate(self, token: str | None) -> AuthPrincipal:
        principal = self._principals.get(token or "")
        if principal is None:
            raise AuthenticationError("invalid_access_token", "Invalid token")
        return principal


@pytest.fixture
def two_user_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    settings = Settings(
        env="test",
        auth_mode="jwt",
        jwt_issuer="https://identity.test",
        jwt_audience="campusvoice",
        jwt_jwks_url="https://identity.test/jwks.json",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'action-users.db'}",
        database_auto_create=True,
        confirmation_secret=SecretStr("coverage-confirmation-secret-at-least-32-bytes"),
    )
    app = create_app(settings)
    app.state.authenticator = _TwoUserAuthenticator()
    with TestClient(app) as test_client:
        yield app, test_client


def test_action_and_challenge_are_bound_to_authenticated_user(
    two_user_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = two_user_client
    alice_headers = {"Authorization": "Bearer alice"}
    bob_headers = {"Authorization": "Bearer bob"}
    prepared = _prepare(
        client,
        "create_task",
        {"title": "Alice bound action"},
        headers=alice_headers,
    )
    action_id = prepared["id"]
    issued = client.post(f"/api/actions/{action_id}/challenge", headers=alice_headers)
    assert issued.status_code == 200

    assert client.get(f"/api/actions/{action_id}", headers=bob_headers).status_code == 404
    cross_user = client.post(
        f"/api/actions/{action_id}/confirm",
        headers=bob_headers,
        json={"confirmed": True, "challenge": issued.json()["challenge"]},
    )
    assert cross_user.status_code == 404

    bob_action = client.post(
        "/api/actions/prepare",
        headers=bob_headers,
        json={"action": "create_task", "payload": {"title": "Bob bound action"}},
    )
    assert bob_action.status_code == 201
    mismatch = client.post(
        f"/api/actions/{bob_action.json()['id']}/confirm",
        headers=bob_headers,
        json={"confirmed": True, "challenge": issued.json()["challenge"]},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "confirmation_challenge_mismatch"
