import logging
from collections.abc import Callable

from fastapi import APIRouter, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.metrics import observe_component
from app.models.entities import UserSettings
from app.security.authentication import websocket_ticket
from app.security.websocket_tickets import consume_websocket_ticket
from app.services.asr import AsrAdapter, create_asr_adapter
from app.services.asr.persistence import SqlAlchemyAsrPersistence
from app.services.asr.session import handle_asr_websocket

router = APIRouter(tags=["asr"])
logger = logging.getLogger("campusvoice.asr")


@router.websocket("/ws/asr")
async def asr_websocket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    origin = websocket.headers.get("origin")
    if origin is None or origin not in settings.cors_origins:
        await websocket.close(code=1008, reason="origin_not_allowed")
        return
    session_factory: async_sessionmaker[AsyncSession] = websocket.app.state.session_factory
    raw_ticket = websocket_ticket(websocket.headers.get("sec-websocket-protocol"))
    if raw_ticket is None:
        await websocket.close(code=1008, reason="authentication_required")
        return
    async with session_factory() as ticket_session:
        user_id = await consume_websocket_ticket(
            ticket_session,
            ticket=raw_ticket,
            origin=origin,
        )
    if user_id is None:
        await websocket.close(code=1008, reason="invalid_or_replayed_ticket")
        return
    registry = websocket.app.state.asr_connections
    lease_id = await registry.acquire(user_id, settings.asr_max_connections_per_user)
    if lease_id is None:
        await websocket.accept(subprotocol="campusvoice")
        await websocket.send_json(
            {
                "type": "error",
                "session_id": "unavailable",
                "sequence": 0,
                "protocol_version": 1,
                "code": "connection_limit_reached",
                "message": "当前用户的语音识别连接数已达到上限。",
                "recoverable": True,
            }
        )
        await websocket.close(code=1008, reason="connection_limit_reached")
        return
    factory: Callable[[], AsrAdapter] = getattr(
        websocket.app.state,
        "asr_adapter_factory",
        lambda: create_asr_adapter(settings),
    )
    try:
        async with session_factory() as session:
            user_settings = await session.get(UserSettings, user_id)
        settings_hotwords = _settings_hotwords(user_settings)
        persistence = SqlAlchemyAsrPersistence(
            session_factory,
            user_id=user_id,
            model_name=settings.asr_model,
        )
        with observe_component(websocket.app.state.metrics, "asr", "session"):
            await handle_asr_websocket(
                websocket,
                factory,
                event_hook=persistence.record_event,
                close_hook=persistence.close,
                additional_hotwords=settings_hotwords,
                accepted_subprotocol="campusvoice",
                max_frame_bytes=settings.asr_max_frame_bytes,
                max_control_message_bytes=settings.asr_max_control_message_bytes,
                idle_timeout_seconds=settings.asr_idle_timeout_seconds,
                max_session_seconds=settings.asr_max_session_seconds,
                max_audio_seconds=settings.asr_max_audio_seconds,
            )
    finally:
        try:
            await registry.release(user_id, lease_id)
        except Exception:
            # Redis leases have a bounded TTL, so cleanup remains fail-closed.
            # Do not include the user identifier or lease in logs.
            logger.exception("asr_quota_release_failed")


def _settings_hotwords(settings: UserSettings | None) -> tuple[str, ...]:
    if settings is None:
        return ()
    values: list[str] = []
    for course in settings.current_courses:
        for key in ("name", "code", "teacher"):
            value = course.get(key)
            if isinstance(value, str):
                values.append(value)
    values.extend(settings.teacher_names)
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
