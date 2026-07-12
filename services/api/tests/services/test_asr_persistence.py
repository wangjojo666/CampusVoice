from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.entities import Transcription, VoiceSession
from app.models.enums import VoiceSessionStatus
from app.schemas.asr import AsrServerEvent
from app.services.asr.persistence import SqlAlchemyAsrPersistence


async def test_asr_persistence_saves_timing_confidence_and_closes_session(client: object) -> None:
    app = client.app  # type: ignore[attr-defined]
    factory: async_sessionmaker[AsyncSession] = app.state.session_factory
    persistence = SqlAlchemyAsrPersistence(
        factory,
        user_id="user_demo",
        model_name="test-model",
    )
    session_id = "voice-persistence-test"
    await persistence.record_event(
        AsrServerEvent(
            type="ready",
            session_id=session_id,
            sequence=0,
            provider="test-only",
        )
    )
    final_event = AsrServerEvent(
        type="final",
        session_id=session_id,
        sequence=1,
        provider="test-only",
        text="机器学习考试",
        confidence=0.91,
        latency_ms=42.4,
        audio_duration_ms=1280.2,
    )
    await persistence.record_event(final_event)
    await persistence.close(session_id)

    async with factory() as session:
        voice_session = await session.get(VoiceSession, session_id)
        transcription = await session.scalar(
            select(Transcription).where(Transcription.voice_session_id == session_id)
        )

    assert voice_session is not None
    assert voice_session.status == VoiceSessionStatus.COMPLETED
    assert voice_session.duration_ms == 1280
    assert voice_session.audio_reference is None
    assert transcription is not None
    assert final_event.transcription_id == transcription.id
    assert transcription.text == "机器学习考试"
    assert transcription.is_final is True
    assert transcription.confidence == 0.91
    assert transcription.latency_ms == 42
