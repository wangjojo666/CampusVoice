from collections.abc import Callable

from fastapi import APIRouter, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.entities import UserSettings
from app.services.asr import AsrAdapter, create_asr_adapter
from app.services.asr.persistence import SqlAlchemyAsrPersistence
from app.services.asr.session import handle_asr_websocket

router = APIRouter(tags=["asr"])


@router.websocket("/ws/asr")
async def asr_websocket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    factory: Callable[[], AsrAdapter] = getattr(
        websocket.app.state,
        "asr_adapter_factory",
        lambda: create_asr_adapter(settings),
    )
    session_factory: async_sessionmaker[AsyncSession] = websocket.app.state.session_factory
    async with session_factory() as session:
        user_settings = await session.get(UserSettings, settings.single_user_id)
    settings_hotwords = _settings_hotwords(user_settings)
    persistence = SqlAlchemyAsrPersistence(
        session_factory,
        user_id=settings.single_user_id,
        model_name=settings.asr_model,
    )
    await handle_asr_websocket(
        websocket,
        factory,
        event_hook=persistence.record_event,
        close_hook=persistence.close,
        additional_hotwords=settings_hotwords,
    )


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
