from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.entities import Transcription, VoiceSession
from app.models.enums import VoiceSessionStatus
from app.schemas.asr import AsrServerEvent


class SqlAlchemyAsrPersistence:
    """Persist ASR session metadata and transcript timing without storing raw audio."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        user_id: str,
        model_name: str,
    ) -> None:
        self._session_factory = session_factory
        self._user_id = user_id
        self._model_name = model_name

    async def record_event(self, event: AsrServerEvent) -> None:
        async with self._session_factory() as session, session.begin():
            voice_session = await session.get(VoiceSession, event.session_id)
            if voice_session is None:
                voice_session = VoiceSession(
                    id=event.session_id,
                    user_id=self._user_id,
                    status=VoiceSessionStatus.STREAMING,
                    asr_provider=event.provider,
                    asr_model=self._model_name,
                    audio_reference=None,
                )
                session.add(voice_session)
            if event.audio_duration_ms is not None:
                duration_ms = max(0, round(event.audio_duration_ms))
                voice_session.duration_ms = max(voice_session.duration_ms or 0, duration_ms)
            if event.type in {"interim", "final"} and event.text is not None:
                transcription = Transcription(
                    voice_session_id=event.session_id,
                    sequence=event.sequence,
                    text=event.text,
                    is_final=event.type == "final",
                    confidence=event.confidence,
                    latency_ms=(
                        max(0, round(event.latency_ms)) if event.latency_ms is not None else None
                    ),
                )
                session.add(transcription)
                await session.flush()
                # The hook runs before the event is serialized, so the browser
                # receives the exact durable row id that backs this transcript.
                event.transcription_id = transcription.id
            if event.type == "error" and event.recoverable is False:
                voice_session.status = VoiceSessionStatus.FAILED
                voice_session.error_message = event.message or event.code

    async def close(self, session_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            voice_session = await session.get(VoiceSession, session_id)
            if voice_session is not None and voice_session.status != VoiceSessionStatus.FAILED:
                voice_session.status = VoiceSessionStatus.COMPLETED
