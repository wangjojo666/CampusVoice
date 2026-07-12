from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Conversation
from app.schemas.intent import IntentResult
from app.services.errors import NotFoundError


class ConversationService:
    """Persist short clarification context without turning the product into a chat archive."""

    async def context_for(
        self,
        session: AsyncSession,
        user_id: str,
        conversation_id: str | None,
    ) -> list[str]:
        if conversation_id is None:
            return []
        conversation = await self._get(session, user_id, conversation_id)
        turns = conversation.context.get("turns", [])
        return [
            str(turn["source_text"])
            for turn in turns[-5:]
            if isinstance(turn, dict) and isinstance(turn.get("source_text"), str)
        ]

    async def record(
        self,
        session: AsyncSession,
        user_id: str,
        conversation_id: str | None,
        result: IntentResult,
    ) -> str:
        if conversation_id is None:
            await session.execute(
                update(Conversation)
                .where(Conversation.user_id == user_id, Conversation.is_closed.is_(False))
                .values(is_closed=True)
            )
            conversation = Conversation(user_id=user_id, context={"turns": []})
            session.add(conversation)
            await session.flush()
        else:
            conversation = await self._get(session, user_id, conversation_id, lock=True)

        turns = list(conversation.context.get("turns", []))
        turn: dict[str, Any] = {
            "source_text": result.source_text,
            "intent": result.intent.value,
            "slots": result.slots.model_dump(mode="json", exclude_none=True),
            "missing_fields": list(result.missing_fields),
            "ambiguities": list(result.ambiguities),
        }
        turns.append(turn)
        conversation.context = {"turns": turns[-10:]}
        conversation.active_intent = result.intent.value
        conversation.is_closed = False
        await session.flush()
        return conversation.id

    @staticmethod
    async def _get(
        session: AsyncSession,
        user_id: str,
        conversation_id: str,
        *,
        lock: bool = False,
    ) -> Conversation:
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
        if lock:
            statement = statement.with_for_update()
        conversation = await session.scalar(statement)
        if conversation is None:
            raise NotFoundError("conversation", conversation_id)
        return conversation
