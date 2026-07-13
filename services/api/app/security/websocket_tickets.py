import hashlib
from datetime import timedelta
from secrets import token_urlsafe

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.types import utc_now
from app.models.entities import WebSocketTicket


def ticket_hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


def issue_websocket_ticket(
    *,
    user_id: str,
    origin: str,
    ttl_seconds: int,
) -> tuple[str, WebSocketTicket]:
    raw = token_urlsafe(32)
    now = utc_now()
    return raw, WebSocketTicket(
        ticket_hash=ticket_hash(raw),
        user_id=user_id,
        origin=origin,
        expires_at=now + timedelta(seconds=ttl_seconds),
        created_at=now,
    )


async def consume_websocket_ticket(
    session: AsyncSession,
    *,
    ticket: str,
    origin: str,
) -> str | None:
    now = utc_now()
    statement = (
        update(WebSocketTicket)
        .where(
            WebSocketTicket.ticket_hash == ticket_hash(ticket),
            WebSocketTicket.origin == origin,
            WebSocketTicket.expires_at > now,
            WebSocketTicket.consumed_at.is_(None),
        )
        .values(consumed_at=now)
        .returning(WebSocketTicket.user_id)
    )
    user_id = await session.scalar(statement)
    if user_id is None:
        await session.rollback()
        return None
    await session.commit()
    return user_id
