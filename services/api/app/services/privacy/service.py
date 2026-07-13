import hashlib
from datetime import date, datetime, timedelta
from enum import Enum
from secrets import token_urlsafe
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import Delete

from app.core.config import Settings
from app.db.types import utc_now
from app.models.entities import (
    ActionLog,
    CalendarEvent,
    ConfirmationNonce,
    Conversation,
    CorrectionRecord,
    Course,
    Document,
    DocumentChunk,
    Hotword,
    ImpactCase,
    ImpactMigrationItem,
    ImpactMigrationPlan,
    NoticeChangeItem,
    NoticeChangeSet,
    NoticeClaim,
    NoticeSeries,
    OidcSession,
    PendingAction,
    PrivacyDeletionChallenge,
    Task,
    Transcription,
    UndoRecord,
    User,
    UserSettings,
    VoiceSession,
    WebSocketTicket,
    WriteChallenge,
)
from app.models.enums import PendingActionState
from app.schemas.privacy import (
    PrivacyDeletionChallengeResponse,
    PrivacyDeletionResult,
    PrivacyExportResponse,
    RetentionPolicy,
    RetentionRunResponse,
)
from app.services.errors import ConflictError, NotFoundError, VerificationFailedError

_SCOPE = "business_data"
_TERMINAL_ACTION_STATES = (
    PendingActionState.EXECUTED,
    PendingActionState.CANCELLED,
    PendingActionState.UNDONE,
    PendingActionState.EXPIRED,
)
_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "authorization",
    "embedding",
    "nonce",
    "password",
    "secret",
    "token",
)


def _challenge_hash(challenge: str) -> str:
    return hashlib.sha256(challenge.encode()).hexdigest()


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if not any(fragment in str(key).lower() for fragment in _SENSITIVE_KEY_FRAGMENTS)
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _record(entity: object, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: _safe_value(getattr(entity, field)) for field in fields}


def _safe_source_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    safe_netloc = parsed.netloc.rsplit("@", maxsplit=1)[-1]
    return urlunsplit((parsed.scheme, safe_netloc, parsed.path, "", ""))


async def _delete_count(session: AsyncSession, statement: Delete) -> int:
    result = await session.execute(statement)
    return int(getattr(result, "rowcount", 0) or 0)


async def _count(session: AsyncSession, statement: Any) -> int:
    return int(await session.scalar(statement) or 0)


class PrivacyService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    def retention_policy(self) -> RetentionPolicy:
        return RetentionPolicy(
            transcription_days=self._settings.transcription_retention_days,
            correction_days=self._settings.correction_retention_days,
            conversation_days=self._settings.conversation_retention_days,
            pending_action_days=self._settings.pending_action_retention_days,
            audit_log_days=self._settings.audit_retention_days,
            raw_audio_persisted=False,
        )

    async def export_user_data(self, user_id: str) -> PrivacyExportResponse:
        async with self._session_factory() as session:
            user = await session.get(User, user_id)
            if user is None:
                raise NotFoundError("user", user_id)
            user_settings = await session.get(UserSettings, user_id)
            hotwords = list(
                await session.scalars(select(Hotword).where(Hotword.user_id == user_id))
            )
            courses = list(await session.scalars(select(Course).where(Course.user_id == user_id)))
            documents = list(
                await session.scalars(select(Document).where(Document.user_id == user_id))
            )
            document_chunks = list(
                await session.scalars(
                    select(DocumentChunk)
                    .join(Document, Document.id == DocumentChunk.document_id)
                    .where(Document.user_id == user_id)
                )
            )
            notice_series = list(
                await session.scalars(select(NoticeSeries).where(NoticeSeries.user_id == user_id))
            )
            notice_claims = list(
                await session.scalars(select(NoticeClaim).where(NoticeClaim.user_id == user_id))
            )
            notice_change_sets = list(
                await session.scalars(
                    select(NoticeChangeSet).where(NoticeChangeSet.user_id == user_id)
                )
            )
            notice_change_items = list(
                await session.scalars(
                    select(NoticeChangeItem).where(NoticeChangeItem.user_id == user_id)
                )
            )
            impact_cases = list(
                await session.scalars(select(ImpactCase).where(ImpactCase.user_id == user_id))
            )
            impact_migration_plans = list(
                await session.scalars(
                    select(ImpactMigrationPlan).where(ImpactMigrationPlan.user_id == user_id)
                )
            )
            impact_migration_items = list(
                await session.scalars(
                    select(ImpactMigrationItem).where(ImpactMigrationItem.user_id == user_id)
                )
            )
            tasks = list(await session.scalars(select(Task).where(Task.user_id == user_id)))
            events = list(
                await session.scalars(select(CalendarEvent).where(CalendarEvent.user_id == user_id))
            )
            voice_sessions = list(
                await session.scalars(select(VoiceSession).where(VoiceSession.user_id == user_id))
            )
            transcriptions = list(
                await session.scalars(
                    select(Transcription)
                    .join(VoiceSession, VoiceSession.id == Transcription.voice_session_id)
                    .where(VoiceSession.user_id == user_id)
                )
            )
            corrections = list(
                await session.scalars(
                    select(CorrectionRecord).where(CorrectionRecord.user_id == user_id)
                )
            )
            conversations = list(
                await session.scalars(select(Conversation).where(Conversation.user_id == user_id))
            )
            pending_actions = list(
                await session.scalars(select(PendingAction).where(PendingAction.user_id == user_id))
            )
            action_logs = list(
                await session.scalars(select(ActionLog).where(ActionLog.user_id == user_id))
            )
            undo_records = list(
                await session.scalars(select(UndoRecord).where(UndoRecord.user_id == user_id))
            )

        settings_records: list[dict[str, Any]] = []
        if user_settings is not None:
            safe_asr_config = {
                key: user_settings.asr_model_config.get(key)
                for key in ("provider", "model", "device")
                if key in user_settings.asr_model_config
            }
            settings_records.append(
                _record(
                    user_settings,
                    (
                        "major",
                        "grade",
                        "current_courses",
                        "teacher_names",
                        "default_reminder_minutes",
                        "timezone",
                        "created_at",
                        "updated_at",
                    ),
                )
                | {"asr_model_config": _safe_value(safe_asr_config)}
            )

        data = {
            "user_settings": settings_records,
            "hotwords": [
                _record(
                    item,
                    ("id", "term", "category", "source", "weight", "is_active", "created_at"),
                )
                for item in hotwords
            ],
            "courses": [
                _record(
                    item,
                    ("id", "code", "name", "teacher", "term", "is_active", "created_at"),
                )
                for item in courses
            ],
            "documents": [
                _record(
                    item,
                    (
                        "id",
                        "title",
                        "department",
                        "publish_date",
                        "applicable_group",
                        "version",
                        "file_type",
                        "content_sha256",
                        "status",
                        "series_id",
                        "supersedes_document_id",
                        "revision_number",
                        "effective_at",
                        "is_current",
                        "ingest_source",
                        "created_at",
                        "updated_at",
                    ),
                )
                | {"source_url": _safe_source_url(item.source_url)}
                for item in documents
            ],
            "document_chunks": [
                _record(
                    item,
                    (
                        "id",
                        "document_id",
                        "ordinal",
                        "content",
                        "page_number",
                        "metadata_json",
                        "created_at",
                    ),
                )
                for item in document_chunks
            ],
            "notice_series": [
                _record(
                    item,
                    (
                        "id",
                        "canonical_key",
                        "normalized_title",
                        "department",
                        "source_key",
                        "created_at",
                        "updated_at",
                    ),
                )
                for item in notice_series
            ],
            "notice_claims": [
                _record(
                    item,
                    (
                        "id",
                        "document_id",
                        "chunk_id",
                        "claim_key",
                        "claim_type",
                        "value_json",
                        "normalized_value_json",
                        "audience_rule_json",
                        "confidence",
                        "evidence_start",
                        "evidence_end",
                        "extractor_version",
                        "review_state",
                        "created_at",
                    ),
                )
                for item in notice_claims
            ],
            "notice_change_sets": [
                _record(
                    item,
                    (
                        "id",
                        "series_id",
                        "from_document_id",
                        "to_document_id",
                        "algorithm_version",
                        "status",
                        "created_at",
                    ),
                )
                for item in notice_change_sets
            ],
            "notice_change_items": [
                _record(
                    item,
                    (
                        "id",
                        "change_set_id",
                        "claim_key",
                        "change_type",
                        "before_claim_id",
                        "after_claim_id",
                        "severity",
                        "confidence",
                        "review_state",
                        "created_at",
                    ),
                )
                for item in notice_change_items
            ],
            "impact_cases": [
                _record(
                    item,
                    (
                        "id",
                        "change_item_id",
                        "entity_type",
                        "entity_id",
                        "entity_version",
                        "reason",
                        "severity",
                        "current_snapshot",
                        "proposed_patch",
                        "recommended_action",
                        "requires_manual_review",
                        "status",
                        "migration_plan_id",
                        "detected_at",
                        "resolved_at",
                    ),
                )
                for item in impact_cases
            ],
            "impact_migration_plans": [
                _record(
                    item,
                    (
                        "id",
                        "change_set_id",
                        "generation",
                        "status",
                        "risk_level",
                        "conflicts_json",
                        "verification_json",
                        "execute_receipt_json",
                        "undo_receipt_json",
                        "version",
                        "executed_at",
                        "undone_at",
                        "created_at",
                        "updated_at",
                    ),
                )
                for item in impact_migration_plans
            ],
            "impact_migration_items": [
                _record(
                    item,
                    (
                        "id",
                        "plan_id",
                        "entity_type",
                        "entity_id",
                        "expected_version",
                        "before_snapshot",
                        "proposed_patch",
                        "after_snapshot",
                        "source_claim_ids",
                        "verification_json",
                        "execute_verification_json",
                        "undo_verification_json",
                        "created_at",
                    ),
                )
                for item in impact_migration_items
            ],
            "tasks": [
                _record(
                    item,
                    (
                        "id",
                        "title",
                        "description",
                        "course_id",
                        "course",
                        "due_at",
                        "reminder_at",
                        "priority",
                        "status",
                        "source_type",
                        "source_document_id",
                        "source_chunk_id",
                        "source_claim_id",
                        "source_history",
                        "version",
                        "created_at",
                        "updated_at",
                    ),
                )
                for item in tasks
            ],
            "calendar_events": [
                _record(
                    item,
                    (
                        "id",
                        "title",
                        "description",
                        "course_id",
                        "course",
                        "start_at",
                        "end_at",
                        "location",
                        "reminder_minutes",
                        "source_type",
                        "source_document_id",
                        "source_chunk_id",
                        "source_claim_id",
                        "source_history",
                        "version",
                        "created_at",
                        "updated_at",
                    ),
                )
                for item in events
            ],
            "voice_sessions": [
                _record(
                    item,
                    (
                        "id",
                        "status",
                        "asr_provider",
                        "asr_model",
                        "duration_ms",
                        "created_at",
                        "updated_at",
                    ),
                )
                for item in voice_sessions
            ],
            "transcriptions": [
                _record(
                    item,
                    (
                        "id",
                        "voice_session_id",
                        "sequence",
                        "text",
                        "is_final",
                        "confidence",
                        "latency_ms",
                        "created_at",
                    ),
                )
                for item in transcriptions
            ],
            "correction_records": [
                _record(
                    item,
                    (
                        "id",
                        "transcription_id",
                        "original_text",
                        "corrected_text",
                        "modifications",
                        "candidates",
                        "reason",
                        "confidence",
                        "user_confirmed",
                        "created_at",
                    ),
                )
                for item in corrections
            ],
            "conversations": [
                _record(
                    item,
                    ("id", "active_intent", "context", "is_closed", "created_at", "updated_at"),
                )
                for item in conversations
            ],
            "pending_actions": [
                _record(
                    item,
                    (
                        "id",
                        "action_type",
                        "entity_type",
                        "target_id",
                        "payload",
                        "execution_options",
                        "state",
                        "risk_level",
                        "risk_factors",
                        "missing_fields",
                        "ambiguities",
                        "blocking_reasons",
                        "diagnostics",
                        "required_confirmations",
                        "confirmations_received",
                        "confirmed_payload",
                        "attempt_count",
                        "max_attempts",
                        "expires_at",
                        "confirmed_at",
                        "executed_at",
                        "cancelled_at",
                        "result",
                        "created_at",
                        "updated_at",
                    ),
                )
                for item in pending_actions
            ],
            "action_logs": [
                _record(
                    item,
                    (
                        "id",
                        "pending_action_id",
                        "voice_session_id",
                        "transcription_id",
                        "action_type",
                        "entity_type",
                        "target_id",
                        "source_text",
                        "corrected_text",
                        "recognized_intent",
                        "extracted_slots",
                        "risk_level",
                        "user_confirmed",
                        "before_snapshot",
                        "after_snapshot",
                        "verification_result",
                        "success",
                        "created_at",
                    ),
                )
                for item in action_logs
            ],
            "undo_records": [
                _record(
                    item,
                    (
                        "id",
                        "action_log_id",
                        "entity_type",
                        "target_id",
                        "undo_action",
                        "snapshot",
                        "state",
                        "expires_at",
                        "undone_at",
                        "created_at",
                    ),
                )
                for item in undo_records
            ],
        }
        return PrivacyExportResponse(
            generated_at=utc_now(),
            user=_record(
                user,
                ("id", "display_name", "is_active", "created_at", "updated_at"),
            ),
            retention_policy=self.retention_policy(),
            data=data,
        )

    async def run_retention(self, user_id: str) -> RetentionRunResponse:
        now = utc_now()
        transcription_cutoff = now - timedelta(days=self._settings.transcription_retention_days)
        correction_cutoff = now - timedelta(days=self._settings.correction_retention_days)
        conversation_cutoff = now - timedelta(days=self._settings.conversation_retention_days)
        pending_cutoff = now - timedelta(days=self._settings.pending_action_retention_days)
        audit_cutoff = now - timedelta(days=self._settings.audit_retention_days)
        voice_sessions = select(VoiceSession.id).where(VoiceSession.user_id == user_id)

        async with self._session_factory() as session, session.begin():
            deleted_counts = {
                "transcriptions": await _delete_count(
                    session,
                    delete(Transcription).where(
                        Transcription.voice_session_id.in_(voice_sessions),
                        Transcription.created_at < transcription_cutoff,
                    ),
                ),
                "correction_records": await _delete_count(
                    session,
                    delete(CorrectionRecord).where(
                        CorrectionRecord.user_id == user_id,
                        CorrectionRecord.created_at < correction_cutoff,
                    ),
                ),
                "conversations": await _delete_count(
                    session,
                    delete(Conversation).where(
                        Conversation.user_id == user_id,
                        Conversation.updated_at < conversation_cutoff,
                    ),
                ),
                "terminal_pending_actions": await _delete_count(
                    session,
                    delete(PendingAction).where(
                        PendingAction.user_id == user_id,
                        PendingAction.state.in_(_TERMINAL_ACTION_STATES),
                        PendingAction.updated_at < pending_cutoff,
                    ),
                ),
                "action_logs": await _delete_count(
                    session,
                    delete(ActionLog).where(
                        ActionLog.user_id == user_id,
                        ActionLog.created_at < audit_cutoff,
                    ),
                ),
                "expired_deletion_challenges": await _delete_count(
                    session,
                    delete(PrivacyDeletionChallenge).where(
                        PrivacyDeletionChallenge.user_id == user_id,
                        PrivacyDeletionChallenge.expires_at <= now,
                    ),
                ),
                "expired_websocket_tickets": await _delete_count(
                    session,
                    delete(WebSocketTicket).where(
                        WebSocketTicket.user_id == user_id,
                        WebSocketTicket.expires_at <= now,
                    ),
                ),
                "expired_write_challenges": await _delete_count(
                    session,
                    delete(WriteChallenge).where(
                        WriteChallenge.user_id == user_id,
                        WriteChallenge.expires_at <= now,
                    ),
                ),
                "expired_oidc_sessions": await _delete_count(
                    session,
                    delete(OidcSession).where(
                        OidcSession.user_id == user_id,
                        OidcSession.expires_at <= now,
                    ),
                ),
            }
        return RetentionRunResponse(ran_at=now, deleted_counts=deleted_counts)

    async def issue_deletion_challenge(
        self,
        user_id: str,
    ) -> PrivacyDeletionChallengeResponse:
        challenge = token_urlsafe(32)
        now = utc_now()
        expires_at = now + timedelta(seconds=self._settings.privacy_deletion_challenge_ttl_seconds)
        entity = PrivacyDeletionChallenge(
            user_id=user_id,
            scope=_SCOPE,
            nonce_hash=_challenge_hash(challenge),
            expires_at=expires_at,
            created_at=now,
        )
        async with self._session_factory() as session, session.begin():
            session.add(entity)
            await session.flush()
        return PrivacyDeletionChallengeResponse(
            id=entity.id,
            challenge=challenge,
            scope="business_data",
            expires_at=expires_at,
        )

    async def clear_user_data(
        self,
        user_id: str,
        challenge_id: str,
        challenge: str,
        scope: str,
    ) -> PrivacyDeletionResult:
        now = utc_now()
        challenge_hash = _challenge_hash(challenge)
        async with self._session_factory() as session, session.begin():
            consumed = await session.scalar(
                update(PrivacyDeletionChallenge)
                .where(
                    PrivacyDeletionChallenge.id == challenge_id,
                    PrivacyDeletionChallenge.user_id == user_id,
                    PrivacyDeletionChallenge.scope == scope,
                    PrivacyDeletionChallenge.nonce_hash == challenge_hash,
                    PrivacyDeletionChallenge.consumed_at.is_(None),
                    PrivacyDeletionChallenge.expires_at > now,
                )
                .values(consumed_at=now)
                .returning(PrivacyDeletionChallenge.id)
            )
            if consumed is None:
                await self._raise_challenge_failure(
                    session,
                    user_id=user_id,
                    challenge_id=challenge_id,
                    challenge_hash=challenge_hash,
                    scope=scope,
                    now=now,
                )
            deleted_counts = await self._count_business_data(session, user_id)
            await self._delete_business_data(session, user_id, keep_challenge_id=challenge_id)

        async with self._session_factory() as verification_session:
            user = await verification_session.get(User, user_id)
            remaining = await self._count_business_data(verification_session, user_id)
        nonzero = {name: count for name, count in remaining.items() if count}
        if user is None or nonzero:
            raise VerificationFailedError(
                {
                    "operation": "clear_user_business_data",
                    "user_preserved": user is not None,
                    "remaining_counts": nonzero,
                }
            )
        return PrivacyDeletionResult(
            scope="business_data",
            deleted_counts=deleted_counts,
        )

    @staticmethod
    async def _raise_challenge_failure(
        session: AsyncSession,
        *,
        user_id: str,
        challenge_id: str,
        challenge_hash: str,
        scope: str,
        now: datetime,
    ) -> None:
        entity = await session.scalar(
            select(PrivacyDeletionChallenge).where(
                PrivacyDeletionChallenge.id == challenge_id,
                PrivacyDeletionChallenge.user_id == user_id,
            )
        )
        if entity is None:
            raise NotFoundError("privacy_deletion_challenge", challenge_id)
        if entity.consumed_at is not None:
            raise ConflictError(
                "privacy_challenge_replayed",
                "This privacy deletion challenge was already consumed",
            )
        if entity.expires_at <= now:
            raise ConflictError(
                "privacy_challenge_expired",
                "This privacy deletion challenge has expired",
            )
        if entity.scope != scope or entity.nonce_hash != challenge_hash:
            raise ConflictError(
                "privacy_challenge_mismatch",
                "This privacy deletion challenge does not match the user or scope",
            )
        raise ConflictError(
            "privacy_challenge_unavailable",
            "This privacy deletion challenge could not be consumed",
        )

    @staticmethod
    async def _count_business_data(session: AsyncSession, user_id: str) -> dict[str, int]:
        document_ids = select(Document.id).where(Document.user_id == user_id)
        voice_session_ids = select(VoiceSession.id).where(VoiceSession.user_id == user_id)
        return {
            "user_settings": await _count(
                session,
                select(func.count(UserSettings.user_id)).where(UserSettings.user_id == user_id),
            ),
            "hotwords": await _count(
                session, select(func.count(Hotword.id)).where(Hotword.user_id == user_id)
            ),
            "courses": await _count(
                session, select(func.count(Course.id)).where(Course.user_id == user_id)
            ),
            "documents": await _count(
                session, select(func.count(Document.id)).where(Document.user_id == user_id)
            ),
            "document_chunks": await _count(
                session,
                select(func.count(DocumentChunk.id)).where(
                    DocumentChunk.document_id.in_(document_ids)
                ),
            ),
            "notice_series": await _count(
                session,
                select(func.count(NoticeSeries.id)).where(NoticeSeries.user_id == user_id),
            ),
            "notice_claims": await _count(
                session,
                select(func.count(NoticeClaim.id)).where(NoticeClaim.user_id == user_id),
            ),
            "notice_change_sets": await _count(
                session,
                select(func.count(NoticeChangeSet.id)).where(NoticeChangeSet.user_id == user_id),
            ),
            "notice_change_items": await _count(
                session,
                select(func.count(NoticeChangeItem.id)).where(NoticeChangeItem.user_id == user_id),
            ),
            "impact_cases": await _count(
                session,
                select(func.count(ImpactCase.id)).where(ImpactCase.user_id == user_id),
            ),
            "impact_migration_plans": await _count(
                session,
                select(func.count(ImpactMigrationPlan.id)).where(
                    ImpactMigrationPlan.user_id == user_id
                ),
            ),
            "impact_migration_items": await _count(
                session,
                select(func.count(ImpactMigrationItem.id)).where(
                    ImpactMigrationItem.user_id == user_id
                ),
            ),
            "tasks": await _count(
                session, select(func.count(Task.id)).where(Task.user_id == user_id)
            ),
            "calendar_events": await _count(
                session,
                select(func.count(CalendarEvent.id)).where(CalendarEvent.user_id == user_id),
            ),
            "voice_sessions": await _count(
                session,
                select(func.count(VoiceSession.id)).where(VoiceSession.user_id == user_id),
            ),
            "transcriptions": await _count(
                session,
                select(func.count(Transcription.id)).where(
                    Transcription.voice_session_id.in_(voice_session_ids)
                ),
            ),
            "correction_records": await _count(
                session,
                select(func.count(CorrectionRecord.id)).where(CorrectionRecord.user_id == user_id),
            ),
            "conversations": await _count(
                session,
                select(func.count(Conversation.id)).where(Conversation.user_id == user_id),
            ),
            "pending_actions": await _count(
                session,
                select(func.count(PendingAction.id)).where(PendingAction.user_id == user_id),
            ),
            "confirmation_nonces": await _count(
                session,
                select(func.count(ConfirmationNonce.nonce_hash)).where(
                    ConfirmationNonce.user_id == user_id
                ),
            ),
            "websocket_tickets": await _count(
                session,
                select(func.count(WebSocketTicket.ticket_hash)).where(
                    WebSocketTicket.user_id == user_id
                ),
            ),
            "write_challenges": await _count(
                session,
                select(func.count(WriteChallenge.token_hash)).where(
                    WriteChallenge.user_id == user_id
                ),
            ),
            "oidc_sessions": await _count(
                session,
                select(func.count(OidcSession.session_hash)).where(OidcSession.user_id == user_id),
            ),
            "action_logs": await _count(
                session,
                select(func.count(ActionLog.id)).where(ActionLog.user_id == user_id),
            ),
            "undo_records": await _count(
                session,
                select(func.count(UndoRecord.id)).where(UndoRecord.user_id == user_id),
            ),
        }

    @staticmethod
    async def _delete_business_data(
        session: AsyncSession,
        user_id: str,
        *,
        keep_challenge_id: str,
    ) -> None:
        document_ids = select(Document.id).where(Document.user_id == user_id)
        voice_session_ids = select(VoiceSession.id).where(VoiceSession.user_id == user_id)
        statements = (
            # Delete leaf rows before their parents even when a database also enforces
            # ON DELETE actions. This keeps the privacy boundary portable and makes the
            # rows covered by the fresh-session verification explicit.
            delete(UndoRecord).where(UndoRecord.user_id == user_id),
            delete(ActionLog).where(ActionLog.user_id == user_id),
            delete(ConfirmationNonce).where(ConfirmationNonce.user_id == user_id),
            delete(PendingAction).where(PendingAction.user_id == user_id),
            delete(CorrectionRecord).where(CorrectionRecord.user_id == user_id),
            delete(Transcription).where(Transcription.voice_session_id.in_(voice_session_ids)),
            delete(VoiceSession).where(VoiceSession.user_id == user_id),
            delete(ImpactMigrationItem).where(ImpactMigrationItem.user_id == user_id),
            delete(ImpactCase).where(ImpactCase.user_id == user_id),
            delete(ImpactMigrationPlan).where(ImpactMigrationPlan.user_id == user_id),
            delete(NoticeChangeItem).where(NoticeChangeItem.user_id == user_id),
            delete(NoticeChangeSet).where(NoticeChangeSet.user_id == user_id),
            delete(CalendarEvent).where(CalendarEvent.user_id == user_id),
            delete(Task).where(Task.user_id == user_id),
            delete(NoticeClaim).where(NoticeClaim.user_id == user_id),
            delete(DocumentChunk).where(DocumentChunk.document_id.in_(document_ids)),
            delete(Document).where(Document.user_id == user_id),
            delete(NoticeSeries).where(NoticeSeries.user_id == user_id),
            delete(Course).where(Course.user_id == user_id),
            delete(Hotword).where(Hotword.user_id == user_id),
            delete(Conversation).where(Conversation.user_id == user_id),
            delete(WebSocketTicket).where(WebSocketTicket.user_id == user_id),
            delete(WriteChallenge).where(WriteChallenge.user_id == user_id),
            delete(OidcSession).where(OidcSession.user_id == user_id),
            delete(UserSettings).where(UserSettings.user_id == user_id),
            delete(PrivacyDeletionChallenge).where(
                PrivacyDeletionChallenge.user_id == user_id,
                PrivacyDeletionChallenge.id != keep_challenge_id,
            ),
        )
        for statement in statements:
            await session.execute(statement)
