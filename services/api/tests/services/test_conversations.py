import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.base import Base
from app.db.session import create_database_engine, create_session_factory
from app.models.entities import Conversation, User
from app.schemas.intent import IntentName, IntentResult
from app.services.errors import NotFoundError
from app.services.intent.conversation import ConversationService


@pytest.fixture
async def conversation_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path / 'conversations.db'}")
    factory = create_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session, session.begin():
        session.add_all(
            [
                User(id="conversation-owner", display_name="Owner"),
                User(id="conversation-other", display_name="Other"),
            ]
        )
    try:
        yield factory
    finally:
        await engine.dispose()


def _intent_result(source_text: str) -> IntentResult:
    return IntentResult(
        intent=IntentName.UNKNOWN,
        confidence=1,
        source_text=source_text,
        requires_confirmation=False,
    )


def _stored_turn(source_text: str) -> dict[str, object]:
    return {
        "source_text": source_text,
        "intent": IntentName.UNKNOWN.value,
        "slots": {},
        "missing_fields": [],
        "ambiguities": [],
    }


async def _seed_conversation(
    factory: async_sessionmaker[AsyncSession],
    *,
    conversation_id: str,
    turns: list[dict[str, object]],
) -> None:
    async with factory() as session, session.begin():
        session.add(
            Conversation(
                id=conversation_id,
                user_id="conversation-owner",
                context={"turns": turns},
            )
        )


async def _concurrent_record(
    factory: async_sessionmaker[AsyncSession],
    barrier: asyncio.Barrier,
    *,
    conversation_id: str,
    source_text: str,
) -> str:
    service = ConversationService()
    async with factory() as session:
        # Match the real route lifecycle: parse_intent reads context in one
        # transaction and records the result later with the same session.
        async with session.begin():
            await service.context_for(session, "conversation-owner", conversation_id)
        await barrier.wait()
        async with session.begin():
            return await service.record(
                session,
                "conversation-owner",
                conversation_id,
                _intent_result(source_text),
            )


async def _conversation(
    factory: async_sessionmaker[AsyncSession], conversation_id: str
) -> Conversation:
    async with factory() as session:
        conversation = await session.scalar(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        assert conversation is not None
        return conversation


def test_intent_clarification_is_persisted_and_reused(client: TestClient) -> None:
    first = client.post(
        "/api/intent/parse",
        json={"text": "创建日程：项目答辩"},
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["missing_fields"] == ["date", "start_time"]
    assert first_body["conversation_id"].startswith("cnv_")

    second = client.post(
        "/api/intent/parse",
        json={
            "text": "明天下午三点",
            "conversation_id": first_body["conversation_id"],
        },
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["conversation_id"] == first_body["conversation_id"]
    assert second_body["intent"] == "create_event"
    assert second_body["slots"]["title"] == "项目答辩"
    assert second_body["slots"]["start_time"] == "15:00"
    assert second_body["missing_fields"] == []


def test_conversation_ids_are_user_scoped_and_not_client_invented(client: TestClient) -> None:
    response = client.post(
        "/api/intent/parse",
        json={"text": "明天下午三点", "conversation_id": "cnv_not_owned"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_concurrent_records_preserve_both_turns_with_independent_sqlite_sessions(
    conversation_factory: async_sessionmaker[AsyncSession],
) -> None:
    conversation_id = "cnv_concurrent_empty"
    await _seed_conversation(conversation_factory, conversation_id=conversation_id, turns=[])
    barrier = asyncio.Barrier(2)

    recorded_ids = await asyncio.gather(
        _concurrent_record(
            conversation_factory,
            barrier,
            conversation_id=conversation_id,
            source_text="first concurrent turn",
        ),
        _concurrent_record(
            conversation_factory,
            barrier,
            conversation_id=conversation_id,
            source_text="second concurrent turn",
        ),
    )

    assert recorded_ids == [conversation_id, conversation_id]
    stored = await _conversation(conversation_factory, conversation_id)
    source_texts = [turn["source_text"] for turn in stored.context["turns"]]
    assert len(source_texts) == 2
    assert source_texts.count("first concurrent turn") == 1
    assert source_texts.count("second concurrent turn") == 1


async def test_concurrent_records_keep_turn_cap_and_reject_foreign_owner_without_mutation(
    conversation_factory: async_sessionmaker[AsyncSession],
) -> None:
    conversation_id = "cnv_concurrent_capped"
    seeded_turns = [_stored_turn(f"seed-{index}") for index in range(9)]
    await _seed_conversation(
        conversation_factory,
        conversation_id=conversation_id,
        turns=seeded_turns,
    )
    barrier = asyncio.Barrier(2)

    recorded_ids = await asyncio.gather(
        _concurrent_record(
            conversation_factory,
            barrier,
            conversation_id=conversation_id,
            source_text="cap concurrent one",
        ),
        _concurrent_record(
            conversation_factory,
            barrier,
            conversation_id=conversation_id,
            source_text="cap concurrent two",
        ),
    )

    assert recorded_ids == [conversation_id, conversation_id]
    stored = await _conversation(conversation_factory, conversation_id)
    source_texts = [turn["source_text"] for turn in stored.context["turns"]]
    assert len(source_texts) == 10
    assert "seed-0" not in source_texts
    assert source_texts.count("cap concurrent one") == 1
    assert source_texts.count("cap concurrent two") == 1
    context_before_rejection = deepcopy(stored.context)
    updated_at_before_rejection = stored.updated_at

    service = ConversationService()
    async with conversation_factory() as foreign_session:
        with pytest.raises(NotFoundError):
            async with foreign_session.begin():
                await service.record(
                    foreign_session,
                    "conversation-other",
                    conversation_id,
                    _intent_result("must not be stored"),
                )
        assert foreign_session.in_transaction() is False

    after_rejection = await _conversation(conversation_factory, conversation_id)
    assert after_rejection.context == context_before_rejection
    assert after_rejection.updated_at == updated_at_before_rejection
