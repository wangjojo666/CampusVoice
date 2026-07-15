import asyncio
import hashlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.entities import (
    ActionLog,
    Document,
    DocumentChunk,
    NoticeClaim,
    PendingAction,
    Task,
    UndoRecord,
    User,
)
from app.models.enums import DocumentStatus
from app.schemas.actions import ActionPrepareRequest
from app.services.actions.service import ActionService
from app.services.errors import ConflictError
from tests.helpers import confirm_action, confirmed_write


async def _seed_source_chain(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: str,
    prefix: str,
    extra_claim: bool = False,
) -> dict[str, str]:
    async with factory() as session, session.begin():
        if await session.get(User, user_id) is None:
            session.add(User(id=user_id, display_name=f"{prefix} user"))
            await session.flush()
        document = Document(
            user_id=user_id,
            title=f"{prefix} source",
            file_type="txt",
            storage_path=f"/{prefix}.txt",
            content_sha256=hashlib.sha256(prefix.encode()).hexdigest(),
            status=DocumentStatus.READY,
        )
        session.add(document)
        await session.flush()
        chunk = DocumentChunk(
            document_id=document.id,
            ordinal=0,
            content=f"{prefix} source evidence",
        )
        session.add(chunk)
        await session.flush()
        first_claim = NoticeClaim(
            user_id=user_id,
            document_id=document.id,
            chunk_id=chunk.id,
            claim_key=f"{prefix}.primary",
            claim_type="text",
            value_json={"value": prefix},
            normalized_value_json={"value": prefix},
            audience_rule_json={},
            confidence=0.99,
            evidence_start=0,
            evidence_end=1,
            extractor_version="prepare-safety-test",
        )
        session.add(first_claim)
        await session.flush()
        result = {
            "document_id": document.id,
            "chunk_id": chunk.id,
            "claim_id": first_claim.id,
        }
        if extra_claim:
            second_claim = NoticeClaim(
                user_id=user_id,
                document_id=document.id,
                chunk_id=chunk.id,
                claim_key=f"{prefix}.secondary",
                claim_type="text",
                value_json={"value": f"{prefix}-secondary"},
                normalized_value_json={"value": f"{prefix}-secondary"},
                audience_rule_json={},
                confidence=0.98,
                evidence_start=1,
                evidence_end=2,
                extractor_version="prepare-safety-test",
            )
            session.add(second_claim)
            await session.flush()
            result["second_claim_id"] = second_claim.id
        return result


def _lineage_payload(title: str, source: dict[str, str]) -> dict[str, object]:
    return {
        "title": title,
        "source_type": "document",
        "source_document_id": source["document_id"],
        "source_chunk_id": source["chunk_id"],
        "source_claim_id": source["claim_id"],
    }


@pytest.mark.parametrize("foreign_part", ["document", "chunk", "claim"])
def test_prepare_rejects_cross_tenant_source_components(
    client: TestClient,
    foreign_part: str,
) -> None:
    factory = client.app.state.session_factory

    async def seed() -> tuple[dict[str, str], dict[str, str]]:
        own = await _seed_source_chain(factory, user_id="user_demo", prefix="own")
        foreign = await _seed_source_chain(factory, user_id="user_other", prefix="foreign")
        return own, foreign

    own, foreign = asyncio.run(seed())
    if foreign_part == "document":
        lineage = {"source_document_id": foreign["document_id"]}
    elif foreign_part == "chunk":
        lineage = {
            "source_document_id": own["document_id"],
            "source_chunk_id": foreign["chunk_id"],
        }
    else:
        lineage = {
            "source_document_id": own["document_id"],
            "source_chunk_id": own["chunk_id"],
            "source_claim_id": foreign["claim_id"],
        }

    response = client.post(
        "/api/actions/prepare",
        json={
            "action": "create_task",
            "payload": {
                "title": f"cross-tenant {foreign_part}",
                "source_type": "document",
                **lineage,
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_source_lineage"


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("asr_confidence", 0.4),
        ("batch_size", 2),
        ("overwrite_existing", True),
        ("hard_to_undo", True),
        ("missing_fields", ["due_at"]),
        ("ambiguities", ["ambiguous_target"]),
    ],
)
def test_prepare_idempotency_key_binds_all_request_semantics(
    client: TestClient,
    field: str,
    changed_value: object,
) -> None:
    request: dict[str, object] = {
        "action": "create_task",
        "payload": {"title": "canonical request"},
        "idempotency_key": f"canonical-{field}",
    }
    first = client.post("/api/actions/prepare", json=request)
    reused = client.post(
        "/api/actions/prepare",
        json=request | {field: changed_value},
    )

    assert first.status_code == 201, first.text
    assert reused.status_code == 409, reused.text
    assert reused.json()["error"]["code"] == "idempotency_key_reused"


def test_update_prepare_validates_merged_lineage_and_allows_same_chunk_claim_swap(
    client: TestClient,
) -> None:
    factory = client.app.state.session_factory

    async def seed() -> tuple[dict[str, str], dict[str, str]]:
        original = await _seed_source_chain(
            factory,
            user_id="user_demo",
            prefix="update-original",
            extra_claim=True,
        )
        other = await _seed_source_chain(
            factory,
            user_id="user_demo",
            prefix="update-other",
        )
        return original, other

    original, other = asyncio.run(seed())
    created = confirmed_write(
        client,
        "POST",
        "/api/tasks",
        _lineage_payload("lineage update target", original),
    )
    assert created.status_code == 201, created.text
    task_id = created.json()["record_id"]

    mixed = client.post(
        "/api/actions/prepare",
        json={
            "action": "update_task",
            "target_id": task_id,
            "payload": {"source_claim_id": other["claim_id"]},
        },
    )
    legal = confirmed_write(
        client,
        "PATCH",
        f"/api/tasks/{task_id}",
        {
            "source_claim_id": original["second_claim_id"],
            "expected_version": 1,
        },
    )

    assert mixed.status_code == 422, mixed.text
    assert mixed.json()["error"]["code"] == "invalid_source_lineage"
    assert legal.status_code == 200, legal.text
    assert legal.json()["record"]["source_claim_id"] == original["second_claim_id"]


def test_damaged_canonical_request_fails_closed(client: TestClient) -> None:
    request = {
        "action": "create_task",
        "payload": {"title": "damaged canonical"},
        "idempotency_key": "damaged-canonical-request",
    }
    first = client.post("/api/actions/prepare", json=request)
    assert first.status_code == 201, first.text

    async def damage() -> None:
        factory = client.app.state.session_factory
        async with factory() as session, session.begin():
            action = await session.get(PendingAction, first.json()["id"])
            assert action is not None
            action.execution_options = action.execution_options | {
                "canonical_request": "not-an-object"
            }

    asyncio.run(damage())
    replay = client.post("/api/actions/prepare", json=request)

    assert replay.status_code == 409, replay.text
    assert replay.json()["error"]["code"] == "idempotency_key_reused"


def test_legacy_simple_idempotent_request_remains_compatible(client: TestClient) -> None:
    request = {
        "action": "create_task",
        "payload": {"title": "legacy canonical"},
        "idempotency_key": "legacy-simple-request",
    }
    first = client.post("/api/actions/prepare", json=request)
    assert first.status_code == 201, first.text

    async def make_legacy() -> None:
        factory = client.app.state.session_factory
        async with factory() as session, session.begin():
            action = await session.get(PendingAction, first.json()["id"])
            assert action is not None
            action.execution_options = {
                key: value
                for key, value in action.execution_options.items()
                if key != "canonical_request"
            }

    asyncio.run(make_legacy())
    replay = client.post("/api/actions/prepare", json=request)

    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]


@pytest.mark.parametrize("uncertain_field", ["missing_fields", "ambiguities"])
def test_legacy_uncertain_request_fails_closed(
    client: TestClient,
    uncertain_field: str,
) -> None:
    request = {
        "action": "create_task",
        "payload": {"title": "legacy uncertain"},
        "idempotency_key": f"legacy-uncertain-{uncertain_field}",
    }
    first = client.post("/api/actions/prepare", json=request)
    assert first.status_code == 201, first.text

    async def make_uncertain_legacy() -> None:
        factory = client.app.state.session_factory
        async with factory() as session, session.begin():
            action = await session.get(PendingAction, first.json()["id"])
            assert action is not None
            action.execution_options = {
                key: value
                for key, value in action.execution_options.items()
                if key != "canonical_request"
            }
            setattr(action, uncertain_field, ["legacy_request_semantics_unknown"])

    asyncio.run(make_uncertain_legacy())
    replay = client.post("/api/actions/prepare", json=request)

    assert replay.status_code == 409, replay.text
    assert replay.json()["error"]["code"] == "idempotency_key_reused"


def test_idempotent_prepare_revalidates_source_before_return(client: TestClient) -> None:
    factory = client.app.state.session_factory
    source = asyncio.run(
        _seed_source_chain(factory, user_id="user_demo", prefix="idempotent-source")
    )
    request = {
        "action": "create_task",
        "payload": _lineage_payload("idempotent source", source),
        "idempotency_key": "idempotent-source-recheck",
    }
    first = client.post("/api/actions/prepare", json=request)
    assert first.status_code == 201, first.text

    async def delete_source() -> None:
        async with factory() as session, session.begin():
            document = await session.get(Document, source["document_id"])
            assert document is not None
            await session.delete(document)

    asyncio.run(delete_source())
    replay = client.post("/api/actions/prepare", json=request)

    assert replay.status_code == 422, replay.text
    assert replay.json()["error"]["code"] == "invalid_source_lineage"


async def _race_prepare_requests(
    factory: async_sessionmaker[AsyncSession],
    first_request: ActionPrepareRequest,
    second_request: ActionPrepareRequest,
) -> tuple[list[PendingAction | BaseException], list[PendingAction]]:
    service = ActionService()
    original_lookup = service.actions.by_idempotency_key
    gate = asyncio.Event()
    lock = asyncio.Lock()
    arrivals = 0

    async def synchronized_first_empty_lookup(
        session: AsyncSession,
        user_id: str,
        key: str,
    ) -> PendingAction | None:
        nonlocal arrivals
        existing = await original_lookup(session, user_id, key)
        should_wait = False
        if existing is None:
            async with lock:
                if arrivals < 2:
                    arrivals += 1
                    should_wait = True
                    if arrivals == 2:
                        gate.set()
        if should_wait:
            await asyncio.wait_for(gate.wait(), timeout=5)
        return existing

    async def prepare_once(request: ActionPrepareRequest) -> PendingAction:
        async with factory() as session:
            return await service.prepare(session, "user_demo", request)

    with patch.object(
        service.actions,
        "by_idempotency_key",
        new=synchronized_first_empty_lookup,
    ):
        outcomes = list(
            await asyncio.gather(
                prepare_once(first_request),
                prepare_once(second_request),
                return_exceptions=True,
            )
        )

    async with factory() as session:
        rows = list(
            await session.scalars(
                select(PendingAction).where(
                    PendingAction.user_id == "user_demo",
                    PendingAction.idempotency_key == first_request.idempotency_key,
                )
            )
        )
    return outcomes, rows


def test_concurrent_prepare_same_canonical_request_returns_one_action(
    client: TestClient,
) -> None:
    request = ActionPrepareRequest(
        action="create_task",
        payload={"title": "same concurrent request"},
        idempotency_key="concurrent-same-canonical",
    )

    outcomes, rows = asyncio.run(
        _race_prepare_requests(client.app.state.session_factory, request, request)
    )

    assert all(isinstance(outcome, PendingAction) for outcome in outcomes)
    assert len(rows) == 1
    assert {outcome.id for outcome in outcomes if isinstance(outcome, PendingAction)} == {
        rows[0].id
    }


def test_concurrent_prepare_different_canonical_requests_conflict(
    client: TestClient,
) -> None:
    first = ActionPrepareRequest(
        action="create_task",
        payload={"title": "first concurrent request"},
        idempotency_key="concurrent-different-canonical",
    )
    second = first.model_copy(update={"asr_confidence": 0.4})

    outcomes, rows = asyncio.run(
        _race_prepare_requests(client.app.state.session_factory, first, second)
    )

    successes = [outcome for outcome in outcomes if isinstance(outcome, PendingAction)]
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, ConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].code == "idempotency_key_reused"
    assert len(rows) == 1


@pytest.mark.parametrize("invalidation", ["deleted", "foreign_owner"])
def test_execute_revalidates_prepared_source_without_business_writes(
    client: TestClient,
    invalidation: str,
) -> None:
    factory = client.app.state.session_factory
    source = asyncio.run(
        _seed_source_chain(factory, user_id="user_demo", prefix=f"toctou-{invalidation}")
    )
    prepared = client.post(
        "/api/actions/prepare",
        json={
            "action": "create_task",
            "payload": _lineage_payload("source disappears", source),
        },
    )
    assert prepared.status_code == 201, prepared.text
    action_id = prepared.json()["id"]
    confirm_action(client, action_id)

    async def invalidate() -> None:
        async with factory() as session, session.begin():
            document = await session.get(Document, source["document_id"])
            assert document is not None
            if invalidation == "deleted":
                await session.delete(document)
            else:
                session.add(User(id="source_new_owner", display_name="new owner"))
                await session.flush()
                document.user_id = "source_new_owner"

    async def business_counts() -> tuple[int, int, int]:
        async with factory() as session:
            return (
                int(await session.scalar(select(func.count(Task.id))) or 0),
                int(await session.scalar(select(func.count(ActionLog.id))) or 0),
                int(await session.scalar(select(func.count(UndoRecord.id))) or 0),
            )

    asyncio.run(invalidate())
    assert asyncio.run(business_counts()) == (0, 0, 0)
    executed = client.post(f"/api/actions/{action_id}/execute")

    assert executed.status_code == 422, executed.text
    assert executed.json()["error"]["code"] == "invalid_source_lineage"
    assert client.get(f"/api/actions/{action_id}").json()["state"] == "ready"
    assert asyncio.run(business_counts()) == (0, 0, 0)
