from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.entities import CorrectionRecord as CorrectionRecordEntity
from app.models.entities import Transcription, UserSettings, VoiceSession
from app.schemas.correction import (
    CorrectionDecisionRequest,
    CorrectionDecisionResponse,
    CorrectionRequest,
    CorrectionResponse,
    CorrectionTerm,
    HotwordSource,
)
from app.services.correction.engine import CorrectionEngine
from app.services.errors import DomainError, NotFoundError, VerificationFailedError


class CorrectionService:
    """Persist every correction preview and user decision with post-commit verification."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: CorrectionEngine,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine

    async def preview(self, user_id: str, request: CorrectionRequest) -> CorrectionResponse:
        if request.transcription_id:
            await self._verify_transcription_owner(user_id, request.transcription_id)
        async with self._session_factory() as settings_session:
            settings = await settings_session.get(UserSettings, user_id)
        request = _merge_settings_context(request, settings)
        response = self._engine.correct(request)
        record = response.record
        entity = CorrectionRecordEntity(
            id=record.id,
            user_id=user_id,
            transcription_id=request.transcription_id,
            original_text=record.original_text,
            corrected_text=record.corrected_text,
            modifications=[item.model_dump(mode="json") for item in record.modifications],
            candidates=[item.model_dump(mode="json") for item in record.candidates],
            reason=record.reason,
            confidence=record.confidence,
            user_confirmed=record.user_confirmed,
        )
        async with self._session_factory() as session, session.begin():
            session.add(entity)
        async with self._session_factory() as verification_session:
            verified = await verification_session.get(CorrectionRecordEntity, record.id)
        if (
            verified is None
            or verified.user_id != user_id
            or verified.original_text != record.original_text
            or verified.corrected_text != record.corrected_text
        ):
            raise VerificationFailedError(
                {"entity": "correction_record", "id": record.id, "operation": "create"}
            )
        return response

    async def decide(
        self,
        user_id: str,
        record_id: str,
        request: CorrectionDecisionRequest,
    ) -> CorrectionDecisionResponse:
        async with self._session_factory() as session, session.begin():
            entity = await session.scalar(
                select(CorrectionRecordEntity).where(
                    CorrectionRecordEntity.id == record_id,
                    CorrectionRecordEntity.user_id == user_id,
                )
            )
            if entity is None:
                raise NotFoundError("correction_record", record_id)
            entity.corrected_text = request.corrected_text
            entity.user_confirmed = request.confirmed
        async with self._session_factory() as verification_session:
            verified = await verification_session.scalar(
                select(CorrectionRecordEntity).where(
                    CorrectionRecordEntity.id == record_id,
                    CorrectionRecordEntity.user_id == user_id,
                )
            )
        if (
            verified is None
            or verified.corrected_text != request.corrected_text
            or verified.user_confirmed != request.confirmed
        ):
            raise VerificationFailedError(
                {"entity": "correction_record", "id": record_id, "operation": "decision"}
            )
        return CorrectionDecisionResponse(
            id=verified.id,
            corrected_text=verified.corrected_text,
            user_confirmed=bool(verified.user_confirmed),
        )

    async def _verify_transcription_owner(self, user_id: str, transcription_id: str) -> None:
        async with self._session_factory() as session:
            owner = await session.scalar(
                select(VoiceSession.user_id)
                .join(Transcription, Transcription.voice_session_id == VoiceSession.id)
                .where(Transcription.id == transcription_id)
            )
        if owner is None:
            raise NotFoundError("transcription", transcription_id)
        if owner != user_id:
            raise DomainError(
                "transcription_owner_mismatch",
                "该转写不属于当前用户。",
                status_code=403,
            )


def _merge_settings_context(
    request: CorrectionRequest,
    settings: UserSettings | None,
) -> CorrectionRequest:
    """Merge durable course/teacher preferences without overriding richer client terms."""

    if settings is None:
        return request
    terms = list(request.terms)
    seen_terms = {item.term for item in terms}
    courses = list(request.current_courses)
    seen_courses = set(courses)

    def add_term(raw: object, source: HotwordSource, *, course_context: bool = False) -> None:
        if not isinstance(raw, str):
            return
        term = raw.strip()
        if not term or len(term) > 200:
            return
        if term not in seen_terms and len(terms) < 2_000:
            terms.append(CorrectionTerm(term=term, source=source))
            seen_terms.add(term)
        if course_context and term not in seen_courses and len(courses) < 100:
            courses.append(term)
            seen_courses.add(term)

    for course in settings.current_courses:
        add_term(course.get("name"), HotwordSource.COURSE, course_context=True)
        add_term(course.get("code"), HotwordSource.COURSE_CODE, course_context=True)
        add_term(course.get("teacher"), HotwordSource.TEACHER)
    for teacher in settings.teacher_names:
        add_term(teacher, HotwordSource.TEACHER)

    return CorrectionRequest.model_validate(
        request.model_dump(mode="python") | {"terms": terms, "current_courses": courses}
    )
