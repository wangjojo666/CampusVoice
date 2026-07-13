from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import InMemoryMetrics, observe_component
from app.db.types import utc_now
from app.models.entities import (
    ActionLog,
    CalendarEvent,
    ConfirmationNonce,
    PendingAction,
    Task,
    Transcription,
    UndoRecord,
    VoiceSession,
)
from app.models.enums import (
    ActionType,
    EntityType,
    PendingActionState,
    SourceType,
    UndoState,
)
from app.repositories.actions import ActionRepository
from app.repositories.events import EventRepository
from app.repositories.tasks import TaskRepository
from app.schemas.actions import (
    ActionPrepareRequest,
    CancelActionRequest,
    ConfirmActionRequest,
    ConfirmationChallenge,
    ExecutionResult,
    UndoResult,
)
from app.schemas.domain import (
    EventCreate,
    EventDraft,
    EventUpdate,
    EventView,
    TaskCreate,
    TaskDraft,
    TaskUpdate,
    TaskView,
)
from app.security.confirmation import ConfirmationChallengeService
from app.services.actions.completeness import (
    entity_for_action,
    missing_required_fields,
    parse_payload,
)
from app.services.errors import ConflictError, DomainError, NotFoundError
from app.services.risk.engine import assess_risk
from app.services.verification.service import VerificationReport, VerificationService

TERMINAL_STATES = {
    PendingActionState.EXECUTED,
    PendingActionState.CANCELLED,
    PendingActionState.UNDONE,
    PendingActionState.EXPIRED,
}


@dataclass(slots=True)
class AppliedOperation:
    record_id: str
    before_snapshot: dict[str, Any] | None
    after_snapshot: dict[str, Any] | None
    expected_fields: dict[str, Any]
    should_exist: bool
    undo_action: str
    undo_snapshot: dict[str, Any] | None
    allow_conflict: bool = False


def _snapshot_task(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "user_id": task.user_id,
        **TaskView.model_validate(task).model_dump(mode="json"),
    }


def _snapshot_event(event: CalendarEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "user_id": event.user_id,
        **EventView.model_validate(event).model_dump(mode="json"),
    }


def _action_label(action: ActionType) -> str:
    labels = {
        ActionType.CREATE_TASK: "待办已创建并通过数据库验证",
        ActionType.UPDATE_TASK: "待办已更新并通过数据库验证",
        ActionType.DELETE_TASK: "待办已删除并通过数据库验证",
        ActionType.CREATE_EVENT: "日历事件已创建并通过数据库验证",
        ActionType.UPDATE_EVENT: "日历事件已更新并通过数据库验证",
        ActionType.DELETE_EVENT: "日历事件已删除并通过数据库验证",
    }
    return labels[action]


class ActionService:
    def __init__(
        self,
        *,
        action_ttl_minutes: int = 30,
        undo_ttl_minutes: int = 1_440,
        confirmation_service: ConfirmationChallengeService | None = None,
        metrics: InMemoryMetrics | None = None,
    ) -> None:
        self.action_ttl_minutes = action_ttl_minutes
        self.undo_ttl_minutes = undo_ttl_minutes
        self.actions = ActionRepository()
        self.tasks = TaskRepository()
        self.events = EventRepository()
        self.verifier = VerificationService()
        self.confirmation_service = confirmation_service
        self.metrics = metrics

    async def prepare(
        self, session: AsyncSession, user_id: str, request: ActionPrepareRequest
    ) -> PendingAction:
        (
            request,
            resolution_missing,
            resolution_ambiguities,
            resolution_blockers,
            resolution_diagnostics,
        ) = await self._resolve_target(session, user_id, request)
        voice_session_id = await self._validate_voice_source(session, user_id, request)
        if (voice_session_id or request.transcription_id) and not (
            request.source_text and request.source_text.strip()
        ):
            raise DomainError(
                "voice_source_text_required",
                "关联语音会话时必须保留原始转写文本。",
                status_code=422,
            )
        audit_options = {
            "source_text": request.source_text,
            "corrected_text": request.corrected_text or request.source_text,
            "voice_session_id": voice_session_id,
            "transcription_id": request.transcription_id,
        }
        if request.idempotency_key:
            existing = await self.actions.by_idempotency_key(
                session, user_id, request.idempotency_key
            )
            if existing is not None:
                parsed = parse_payload(request.action, request.payload)
                canonical = parsed.model_dump(mode="json", exclude_unset=True)
                if (
                    existing.action_type != request.action
                    or existing.payload != canonical
                    or existing.target_id != request.target_id
                    or any(
                        existing.execution_options.get(name) != value
                        for name, value in audit_options.items()
                    )
                ):
                    raise ConflictError(
                        "idempotency_key_reused",
                        "The idempotency key was already used for a different action",
                    )
                await session.commit()
                return existing

        payload_model = parse_payload(request.action, request.payload)
        payload = payload_model.model_dump(mode="json", exclude_unset=True)
        missing = missing_required_fields(
            request.action, request.target_id, payload_model, request.missing_fields
        )
        if resolution_blockers and request.target_title:
            missing = [field for field in missing if field != "target_id"]
        missing = sorted(set(missing) | set(resolution_missing))
        diagnostics: dict[str, Any] = dict(resolution_diagnostics)
        ambiguities = list(dict.fromkeys([*request.ambiguities, *resolution_ambiguities]))
        blocking_reasons: list[str] = list(resolution_blockers)

        if missing and "missing_required_fields" not in blocking_reasons:
            blocking_reasons.append("missing_required_fields")
        if ambiguities:
            blocking_reasons.append("unresolved_ambiguity")

        has_duplicate = False
        has_conflict = False
        if not missing and not resolution_blockers:
            has_duplicate, has_conflict, mutation_diagnostics = await self._diagnose(
                session, user_id, request, payload_model
            )
            diagnostics.update(mutation_diagnostics)
            if has_duplicate:
                blocking_reasons.append("duplicate_record")
            if has_conflict and not request.overwrite_existing:
                blocking_reasons.append("time_conflict_requires_override")

        risk = assess_risk(
            request.action,
            asr_confidence=request.asr_confidence,
            missing_fields=missing,
            has_conflict=has_conflict,
            has_duplicate=has_duplicate,
            batch_size=request.batch_size,
            overwrite_existing=request.overwrite_existing,
            hard_to_undo=request.hard_to_undo,
        )
        state = (
            PendingActionState.NEEDS_INPUT
            if blocking_reasons
            else PendingActionState.AWAITING_CONFIRMATION
            if risk.required_confirmations
            else PendingActionState.READY
        )
        now = utc_now()
        execution_options = {
            "asr_confidence": request.asr_confidence,
            "batch_size": request.batch_size,
            "overwrite_existing": request.overwrite_existing,
            "hard_to_undo": request.hard_to_undo,
            **audit_options,
            "target_title": request.target_title,
        }
        await session.rollback()
        async with session.begin():
            action = PendingAction(
                user_id=user_id,
                action_type=request.action,
                entity_type=entity_for_action(request.action),
                target_id=request.target_id,
                payload=payload,
                execution_options=execution_options,
                state=state,
                risk_level=risk.level,
                risk_factors=list(risk.factors),
                missing_fields=missing,
                ambiguities=ambiguities,
                blocking_reasons=blocking_reasons,
                diagnostics=diagnostics,
                required_confirmations=risk.required_confirmations,
                confirmations_received=0,
                idempotency_key=request.idempotency_key,
                expires_at=now + timedelta(minutes=self.action_ttl_minutes),
            )
            session.add(action)
            await session.flush()
        return action

    async def _validate_voice_source(
        self,
        session: AsyncSession,
        user_id: str,
        request: ActionPrepareRequest,
    ) -> str | None:
        resolved_session_id = request.voice_session_id
        if request.transcription_id:
            transcription = await session.scalar(
                select(Transcription)
                .join(VoiceSession, VoiceSession.id == Transcription.voice_session_id)
                .where(
                    Transcription.id == request.transcription_id,
                    VoiceSession.user_id == user_id,
                )
            )
            if transcription is None:
                raise NotFoundError("transcription", request.transcription_id)
            if resolved_session_id and resolved_session_id != transcription.voice_session_id:
                raise DomainError(
                    "voice_source_mismatch",
                    "转写记录与语音会话不匹配。",
                    status_code=422,
                    details={"voice_session_id": resolved_session_id},
                )
            resolved_session_id = transcription.voice_session_id
        if not resolved_session_id:
            return None
        voice_session = await session.scalar(
            select(VoiceSession).where(
                VoiceSession.id == resolved_session_id,
                VoiceSession.user_id == user_id,
            )
        )
        if voice_session is None:
            raise NotFoundError("voice_session", resolved_session_id)
        return resolved_session_id

    async def _resolve_target(
        self,
        session: AsyncSession,
        user_id: str,
        request: ActionPrepareRequest,
    ) -> tuple[ActionPrepareRequest, list[str], list[str], list[str], dict[str, Any]]:
        target_actions = {
            ActionType.UPDATE_TASK,
            ActionType.DELETE_TASK,
            ActionType.UPDATE_EVENT,
            ActionType.DELETE_EVENT,
        }
        if request.action not in target_actions or request.target_id or not request.target_title:
            return request, [], [], [], {}

        is_task = request.action in {ActionType.UPDATE_TASK, ActionType.DELETE_TASK}
        if is_task:
            candidates: Sequence[Task | CalendarEvent] = await self.tasks.find_by_title(
                session, user_id, request.target_title
            )
            entity_label = "待办"
        else:
            candidates = await self.events.find_by_title(session, user_id, request.target_title)
            entity_label = "日程"

        if len(candidates) == 1:
            return (
                request.model_copy(update={"target_id": candidates[0].id}),
                [],
                [],
                [],
                {
                    "target_resolution": "unique_title_match",
                    "target_title": request.target_title,
                },
            )

        rendered = [self._target_candidate(item) for item in candidates]
        diagnostics: dict[str, Any] = {
            "target_title": request.target_title,
            "target_candidates": rendered,
        }
        if not candidates:
            diagnostics["clarification_question"] = (
                f"没有找到标题为“{request.target_title}”的{entity_label}，请提供更准确的名称。"
            )
            return (
                request,
                ["target_match"],
                [],
                ["target_not_found"],
                diagnostics,
            )

        diagnostics["clarification_question"] = (
            f"找到 {len(candidates)} 个标题为“{request.target_title}”的{entity_label}，"
            "请选择具体记录。"
        )
        return (
            request,
            ["target_selection"],
            ["存在多个同名目标，需要用户选择具体记录"],
            ["ambiguous_target"],
            diagnostics,
        )

    @staticmethod
    def _target_candidate(item: Task | CalendarEvent) -> dict[str, str]:
        if isinstance(item, Task):
            detail = item.due_at.isoformat() if item.due_at else "无截止时间"
        else:
            detail = item.start_at.isoformat()
        return {"id": item.id, "label": f"{item.title} · {detail}"}

    async def _diagnose(
        self,
        session: AsyncSession,
        user_id: str,
        request: ActionPrepareRequest,
        payload: TaskDraft | EventDraft,
    ) -> tuple[bool, bool, dict[str, Any]]:
        if request.action in {ActionType.UPDATE_TASK, ActionType.DELETE_TASK}:
            task = await self.tasks.get(session, user_id, request.target_id or "")
            if task is None:
                raise NotFoundError("task", request.target_id or "")
            if request.action == ActionType.DELETE_TASK:
                return False, False, {}
            assert isinstance(payload, TaskDraft)
            task_candidate = _merge_task(task, payload)
            task_duplicates = await self.tasks.find_duplicates(
                session,
                user_id,
                title=task_candidate.title,
                course=task_candidate.course,
                due_at=task_candidate.due_at,
                exclude_id=task.id,
            )
            return (
                bool(task_duplicates),
                False,
                {"duplicate_ids": [item.id for item in task_duplicates]},
            )

        if request.action == ActionType.CREATE_TASK:
            assert isinstance(payload, TaskDraft)
            new_task_duplicates = await self.tasks.find_duplicates(
                session,
                user_id,
                title=payload.title or "",
                course=payload.course,
                due_at=payload.due_at,
            )
            return (
                bool(new_task_duplicates),
                False,
                {"duplicate_ids": [item.id for item in new_task_duplicates]},
            )

        if request.action in {ActionType.UPDATE_EVENT, ActionType.DELETE_EVENT}:
            event = await self.events.get(session, user_id, request.target_id or "")
            if event is None:
                raise NotFoundError("event", request.target_id or "")
            if request.action == ActionType.DELETE_EVENT:
                return False, False, {}
            assert isinstance(payload, EventDraft)
            event_candidate = _merge_event(event, payload, request.overwrite_existing)
        else:
            assert isinstance(payload, EventDraft)
            event_candidate = _event_create_from_draft(payload, request.overwrite_existing)

        end_at = event_candidate.end_at or event_candidate.start_at + timedelta(hours=1)
        event_duplicates = await self.events.find_duplicates(
            session,
            user_id,
            title=event_candidate.title,
            start_at=event_candidate.start_at,
            end_at=end_at,
            location=event_candidate.location,
            exclude_id=request.target_id,
        )
        event_conflicts = await self.events.conflicts(
            session,
            user_id,
            start_at=event_candidate.start_at,
            end_at=end_at,
            exclude_id=request.target_id,
        )
        return (
            bool(event_duplicates),
            bool(event_conflicts),
            {
                "duplicate_ids": [item.id for item in event_duplicates],
                "conflict_ids": [item.id for item in event_conflicts],
            },
        )

    async def issue_confirmation_challenge(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
    ) -> ConfirmationChallenge:
        await self._reject_if_expired(session, user_id, action_id)
        action = await self._pending_or_404(session, user_id, action_id)
        if action.state not in {
            PendingActionState.AWAITING_CONFIRMATION,
            PendingActionState.AWAITING_SECOND_CONFIRMATION,
        }:
            raise ConflictError(
                "invalid_action_state",
                "A confirmation challenge cannot be issued in the current state",
                {"state": action.state.value},
            )
        if self.confirmation_service is None:
            raise DomainError(
                "confirmation_service_unavailable",
                "The confirmation service is not configured",
                status_code=503,
            )
        challenge, stage, expires_at = self.confirmation_service.issue(action, user_id)
        return ConfirmationChallenge(
            challenge=challenge,
            stage=stage,
            expires_at=expires_at,
        )

    async def confirm_direct(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
    ) -> PendingAction:
        """Record one explicit form submission for non-high-risk direct mutations."""
        await self._reject_if_expired(session, user_id, action_id)
        async with session.begin():
            action = await self._pending_or_404(session, user_id, action_id, lock=True)
            if (
                action.required_confirmations != 1
                or action.state != PendingActionState.AWAITING_CONFIRMATION
            ):
                raise ConflictError(
                    "challenge_confirmation_required",
                    "This action must use the challenge confirmation workflow",
                )
            now = utc_now()
            action.confirmation_history.append(
                {"method": "authenticated_form", "stage": 1, "confirmed_at": now.isoformat()}
            )
            action.confirmations_received = 1
            action.state = PendingActionState.READY
            action.confirmed_at = now
            action.confirmed_payload = dict(action.payload)
        return action

    async def confirm(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
        request: ConfirmActionRequest,
    ) -> PendingAction:
        await self._reject_if_expired(session, user_id, action_id)
        try:
            async with session.begin():
                action = await self._pending_or_404(session, user_id, action_id, lock=True)
                if (
                    action.state in TERMINAL_STATES
                    or action.state == PendingActionState.NEEDS_INPUT
                ):
                    raise ConflictError(
                        "invalid_action_state",
                        f"Action in state '{action.state.value}' cannot be confirmed",
                        {"state": action.state.value},
                    )
                if action.state not in {
                    PendingActionState.AWAITING_CONFIRMATION,
                    PendingActionState.AWAITING_SECOND_CONFIRMATION,
                }:
                    raise ConflictError(
                        "invalid_action_state", "Action cannot be confirmed in its current state"
                    )
                if not request.confirmed:
                    action.state = PendingActionState.CANCELLED
                    action.cancelled_at = utc_now()
                    action.last_error = "user_declined_confirmation"
                    return action
                if self.confirmation_service is None:
                    raise DomainError(
                        "confirmation_service_unavailable",
                        "The confirmation service is not configured",
                        status_code=503,
                    )
                verified = self.confirmation_service.verify(
                    request.challenge,
                    action=action,
                    user_id=user_id,
                )
                now = utc_now()
                session.add(
                    ConfirmationNonce(
                        nonce_hash=verified.nonce_hash,
                        pending_action_id=action.id,
                        user_id=user_id,
                        stage=verified.stage,
                        payload_hash=verified.payload_hash,
                        expires_at=verified.expires_at,
                        consumed_at=now,
                    )
                )
                await session.flush()
                action.confirmation_history.append(
                    {
                        "nonce_hash": verified.nonce_hash,
                        "stage": verified.stage,
                        "payload_hash": verified.payload_hash,
                        "confirmed_at": now.isoformat(),
                    }
                )
                action.confirmations_received += 1
                if action.confirmations_received >= action.required_confirmations:
                    action.state = PendingActionState.READY
                    action.confirmed_at = now
                    action.confirmed_payload = dict(action.payload)
                else:
                    action.state = PendingActionState.AWAITING_SECOND_CONFIRMATION
        except IntegrityError as exc:
            await session.rollback()
            raise ConflictError(
                "confirmation_replayed",
                "This confirmation stage was already consumed",
            ) from exc
        return action

    async def cancel(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
        request: CancelActionRequest,
    ) -> PendingAction:
        await self._reject_if_expired(session, user_id, action_id)
        async with session.begin():
            action = await self._pending_or_404(session, user_id, action_id, lock=True)
            if action.state in {
                PendingActionState.EXECUTED,
                PendingActionState.UNDONE,
                PendingActionState.EXECUTING,
            }:
                raise ConflictError(
                    "invalid_action_state", "An executing or executed action cannot be cancelled"
                )
            if action.state == PendingActionState.CANCELLED:
                return action
            action.state = PendingActionState.CANCELLED
            action.cancelled_at = utc_now()
            action.last_error = request.reason or "cancelled_by_user"
        return action

    async def execute(self, session: AsyncSession, user_id: str, action_id: str) -> ExecutionResult:
        with observe_component(self.metrics, "action", "execute") as observation:
            result = await self._execute(session, user_id, action_id)
            observation.error = not result.success
            return result

    async def _execute(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
    ) -> ExecutionResult:
        await self._reject_if_expired(session, user_id, action_id)
        operation: AppliedOperation | None = None
        action_type: ActionType | None = None
        try:
            async with session.begin():
                action = await self._pending_or_404(session, user_id, action_id, lock=True)
                if action.state == PendingActionState.EXECUTED and action.result:
                    return _public_result(action.result)
                if action.state == PendingActionState.FAILED and action.result:
                    if action.attempt_count >= action.max_attempts:
                        raise ConflictError(
                            "retry_limit_reached", "The action retry limit has been reached"
                        )
                    operation = _operation_from_result(action.result)
                    action.attempt_count += 1
                    action.state = PendingActionState.EXECUTING
                else:
                    if action.state != PendingActionState.READY:
                        raise ConflictError(
                            "invalid_action_state",
                            "Action must receive all required confirmations before execution",
                            {"state": action.state.value},
                        )
                    if action.confirmed_payload != action.payload:
                        raise ConflictError(
                            "confirmation_payload_changed",
                            "The action payload changed after confirmation; prepare it again",
                        )
                    if action.attempt_count >= action.max_attempts:
                        raise ConflictError(
                            "retry_limit_reached", "The action retry limit has been reached"
                        )
                    action.attempt_count += 1
                    action.state = PendingActionState.EXECUTING
                    operation = await self._apply(session, action)
                    action.target_id = operation.record_id
                    created_log = ActionLog(
                        user_id=user_id,
                        pending_action_id=action.id,
                        voice_session_id=action.execution_options.get("voice_session_id"),
                        transcription_id=action.execution_options.get("transcription_id"),
                        action_type=action.action_type,
                        entity_type=action.entity_type,
                        target_id=operation.record_id,
                        source_text=action.execution_options.get("source_text"),
                        corrected_text=action.execution_options.get("corrected_text"),
                        recognized_intent=action.action_type.value,
                        extracted_slots=dict(action.payload),
                        risk_level=action.risk_level,
                        user_confirmed=action.confirmations_received
                        >= action.required_confirmations,
                        before_snapshot=operation.before_snapshot,
                        after_snapshot=operation.after_snapshot,
                    )
                    session.add(created_log)
                    await session.flush()
                    session.add(
                        UndoRecord(
                            user_id=user_id,
                            action_log_id=created_log.id,
                            entity_type=action.entity_type,
                            target_id=operation.record_id,
                            undo_action=operation.undo_action,
                            snapshot=operation.undo_snapshot,
                            expires_at=utc_now() + timedelta(minutes=self.undo_ttl_minutes),
                        )
                    )
                    action.result = _provisional_result(action.id, action.action_type, operation)
                action_type = action.action_type
        except DomainError:
            raise
        except Exception as exc:
            await session.rollback()
            return await self._record_execution_failure(
                session, user_id, action_id, f"database_write_failed: {exc}"
            )

        assert operation is not None and action_type is not None
        report = await self._verify(session, user_id, action_type, operation)
        action = await self._pending_or_404(session, user_id, action_id)
        verification_log = await self.actions.log_for_action(session, user_id, action_id)
        result = _execution_result(action, operation, report)
        if report.success:
            action.state = PendingActionState.EXECUTED
            action.executed_at = utc_now()
            action.last_error = None
        else:
            action.state = PendingActionState.FAILED
            action.last_error = "post_commit_verification_failed"
        action.result = result.model_dump(mode="json") | {
            "_operation": _operation_to_dict(operation),
            "applied": True,
        }
        if verification_log is not None:
            verification_log.success = report.success
            verification_log.verification_result = report.as_dict()
            verification_log.error_message = None if report.success else action.last_error
        await session.commit()
        return result

    async def _record_execution_failure(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
        error: str,
    ) -> ExecutionResult:
        async with session.begin():
            action = await self._pending_or_404(session, user_id, action_id, lock=True)
            action.state = PendingActionState.FAILED
            action.last_error = error[:1_000]
            result = ExecutionResult(
                success=False,
                action=action.action_type.value,
                record_id=action.target_id,
                verified_fields={},
                side_effects=[],
                message="数据库写入失败，未报告为成功",
                error=action.last_error,
                retryable=action.attempt_count < action.max_attempts,
                action_id=action.id,
            )
            session.add(
                ActionLog(
                    user_id=user_id,
                    pending_action_id=action.id,
                    voice_session_id=action.execution_options.get("voice_session_id"),
                    transcription_id=action.execution_options.get("transcription_id"),
                    action_type=action.action_type,
                    entity_type=action.entity_type,
                    target_id=action.target_id,
                    source_text=action.execution_options.get("source_text"),
                    corrected_text=action.execution_options.get("corrected_text"),
                    recognized_intent=action.action_type.value,
                    extracted_slots=dict(action.payload),
                    risk_level=action.risk_level,
                    user_confirmed=action.confirmations_received >= action.required_confirmations,
                    success=False,
                    error_message=action.last_error,
                )
            )
        return result

    async def _apply(self, session: AsyncSession, action: PendingAction) -> AppliedOperation:
        if action.action_type == ActionType.CREATE_TASK:
            new_task_data = _task_create_from_payload(action.payload)
            create_task_duplicates = await self.tasks.find_duplicates(
                session,
                action.user_id,
                title=new_task_data.title,
                course=new_task_data.course,
                due_at=new_task_data.due_at,
            )
            if create_task_duplicates:
                raise ConflictError(
                    "duplicate_task",
                    "A duplicate task already exists",
                    {"ids": [item.id for item in create_task_duplicates]},
                )
            created_task = await self.tasks.create(session, action.user_id, new_task_data)
            created_task_snapshot = _snapshot_task(created_task)
            return AppliedOperation(
                created_task.id,
                None,
                created_task_snapshot,
                new_task_data.model_dump(mode="json"),
                True,
                "delete",
                None,
            )
        if action.action_type == ActionType.UPDATE_TASK:
            task_to_update = await self._task_or_404(
                session, action.user_id, action.target_id or "", True
            )
            task_before = _snapshot_task(task_to_update)
            task_update = TaskUpdate.model_validate(action.payload)
            self._check_version(task_to_update.version, task_update.expected_version)
            updated_task_candidate = _merge_task(
                task_to_update, TaskDraft.model_validate(action.payload)
            )
            update_task_duplicates = await self.tasks.find_duplicates(
                session,
                action.user_id,
                title=updated_task_candidate.title,
                course=updated_task_candidate.course,
                due_at=updated_task_candidate.due_at,
                exclude_id=task_to_update.id,
            )
            if update_task_duplicates:
                raise ConflictError("duplicate_task", "The update would create a duplicate task")
            await self.tasks.update(session, task_to_update, task_update)
            task_after = _snapshot_task(task_to_update)
            task_expected = task_update.model_dump(
                mode="json", exclude_unset=True, exclude={"expected_version"}
            )
            return AppliedOperation(
                task_to_update.id,
                task_before,
                task_after,
                task_expected,
                True,
                "restore",
                task_before,
            )
        if action.action_type == ActionType.DELETE_TASK:
            task_to_delete = await self._task_or_404(
                session, action.user_id, action.target_id or "", True
            )
            deleted_task_snapshot = _snapshot_task(task_to_delete)
            await self.tasks.delete(session, task_to_delete)
            return AppliedOperation(
                task_to_delete.id,
                deleted_task_snapshot,
                None,
                {},
                False,
                "restore",
                deleted_task_snapshot,
            )

        if action.action_type == ActionType.CREATE_EVENT:
            new_event_data = _event_create_from_payload(
                action.payload, bool(action.execution_options.get("overwrite_existing"))
            )
            await self._guard_event_collisions(session, action.user_id, new_event_data)
            created_event = await self.events.create(session, action.user_id, new_event_data)
            created_event_snapshot = _snapshot_event(created_event)
            create_event_expected = new_event_data.model_dump(
                mode="json", exclude={"allow_conflict"}
            )
            create_event_expected["end_at"] = created_event.end_at.isoformat()
            return AppliedOperation(
                created_event.id,
                None,
                created_event_snapshot,
                create_event_expected,
                True,
                "delete",
                None,
                new_event_data.allow_conflict,
            )
        if action.action_type == ActionType.UPDATE_EVENT:
            event_to_update = await self._event_or_404(
                session, action.user_id, action.target_id or "", True
            )
            event_before = _snapshot_event(event_to_update)
            event_update = EventUpdate.model_validate(
                action.payload
                | {"allow_conflict": bool(action.execution_options.get("overwrite_existing"))}
            )
            self._check_version(event_to_update.version, event_update.expected_version)
            updated_event_candidate = _merge_event(
                event_to_update,
                EventDraft.model_validate(action.payload),
                bool(action.execution_options.get("overwrite_existing")),
            )
            await self._guard_event_collisions(
                session,
                action.user_id,
                updated_event_candidate,
                exclude_id=event_to_update.id,
            )
            await self.events.update(session, event_to_update, event_update)
            event_after = _snapshot_event(event_to_update)
            update_event_expected = event_update.model_dump(
                mode="json",
                exclude_unset=True,
                exclude={"allow_conflict", "expected_version"},
            )
            return AppliedOperation(
                event_to_update.id,
                event_before,
                event_after,
                update_event_expected,
                True,
                "restore",
                event_before,
                event_update.allow_conflict,
            )
        event_to_delete = await self._event_or_404(
            session, action.user_id, action.target_id or "", True
        )
        deleted_event_snapshot = _snapshot_event(event_to_delete)
        await self.events.delete(session, event_to_delete)
        return AppliedOperation(
            event_to_delete.id,
            deleted_event_snapshot,
            None,
            {},
            False,
            "restore",
            deleted_event_snapshot,
        )

    async def _guard_event_collisions(
        self,
        session: AsyncSession,
        user_id: str,
        data: EventCreate,
        *,
        exclude_id: str | None = None,
    ) -> None:
        end_at = data.end_at or data.start_at + timedelta(hours=1)
        duplicates = await self.events.find_duplicates(
            session,
            user_id,
            title=data.title,
            start_at=data.start_at,
            end_at=end_at,
            location=data.location,
            exclude_id=exclude_id,
        )
        if duplicates:
            raise ConflictError(
                "duplicate_event",
                "A duplicate event already exists",
                {"ids": [item.id for item in duplicates]},
            )
        conflicts = await self.events.conflicts(
            session,
            user_id,
            start_at=data.start_at,
            end_at=end_at,
            exclude_id=exclude_id,
        )
        if conflicts and not data.allow_conflict:
            raise ConflictError(
                "event_time_conflict",
                "The event overlaps an existing event",
                {"ids": [item.id for item in conflicts]},
            )

    async def _verify(
        self,
        session: AsyncSession,
        user_id: str,
        action_type: ActionType,
        operation: AppliedOperation,
    ) -> VerificationReport:
        with observe_component(self.metrics, "verification", "verify") as observation:
            report = await self._verify_operation(session, user_id, action_type, operation)
            observation.error = not report.success
            return report

    async def _verify_operation(
        self,
        session: AsyncSession,
        user_id: str,
        action_type: ActionType,
        operation: AppliedOperation,
    ) -> VerificationReport:
        if action_type in {
            ActionType.CREATE_TASK,
            ActionType.UPDATE_TASK,
            ActionType.DELETE_TASK,
        }:
            return await self.verifier.verify_task(
                session,
                user_id,
                operation.record_id,
                operation.expected_fields,
                should_exist=operation.should_exist,
            )
        return await self.verifier.verify_event(
            session,
            user_id,
            operation.record_id,
            operation.expected_fields,
            should_exist=operation.should_exist,
            allow_conflict=operation.allow_conflict,
        )

    async def undo(self, session: AsyncSession, user_id: str, action_id: str) -> UndoResult:
        await self._reject_if_undo_expired(session, user_id, action_id)
        async with session.begin():
            action = await self._pending_or_404(session, user_id, action_id, lock=True)
            if action.state == PendingActionState.UNDONE and action.result:
                previous = _public_result(action.result)
                return UndoResult(**previous.model_dump(), original_action=action.action_type)
            if action.state != PendingActionState.EXECUTED:
                raise ConflictError(
                    "invalid_action_state", "Only a successfully executed action can be undone"
                )
            log = await self.actions.log_for_action(session, user_id, action_id)
            if log is None:
                raise ConflictError("undo_unavailable", "No operation log is available for undo")
            undo = await self.actions.undo_for_log(session, user_id, log.id)
            if undo is None or undo.state != UndoState.AVAILABLE:
                raise ConflictError("undo_unavailable", "This operation cannot be undone")
            operation = await self._apply_undo(session, action, undo)

        report = await self._verify_undo(session, user_id, action, operation)
        action = await self._pending_or_404(session, user_id, action_id)
        log = await self.actions.log_for_action(session, user_id, action_id)
        assert log is not None
        undo = await self.actions.undo_for_log(session, user_id, log.id)
        assert undo is not None
        base = _execution_result(
            action, operation, report, action_name=f"undo_{action.action_type.value}"
        )
        if report.success:
            action.state = PendingActionState.UNDONE
            undo.state = UndoState.UNDONE
            undo.undone_at = utc_now()
            log.verification_result = dict(log.verification_result) | {
                "undone": True,
                "undo_result": base.model_dump(mode="json"),
            }
        else:
            undo.state = UndoState.FAILED
            undo.error_message = "undo_verification_failed"
        action.result = base.model_dump(mode="json")
        await session.commit()
        return UndoResult(**base.model_dump(), original_action=action.action_type)

    async def _apply_undo(
        self, session: AsyncSession, action: PendingAction, undo: UndoRecord
    ) -> AppliedOperation:
        if action.entity_type == EntityType.TASK:
            current = await self.tasks.get_for_update(session, action.user_id, undo.target_id)
            if undo.undo_action == "delete":
                if current is None:
                    raise ConflictError("undo_target_missing", "The created task no longer exists")
                await self.tasks.delete(session, current)
                return AppliedOperation(
                    current.id, _snapshot_task(current), None, {}, False, "", None
                )
            if undo.snapshot is None:
                raise ConflictError("undo_snapshot_missing", "The task snapshot is unavailable")
            restored = await self._restore_task(session, action.user_id, current, undo.snapshot)
            expected = _task_business_fields(undo.snapshot)
            return AppliedOperation(
                restored.id, None, _snapshot_task(restored), expected, True, "", None
            )

        current_event = await self.events.get_for_update(session, action.user_id, undo.target_id)
        if undo.undo_action == "delete":
            if current_event is None:
                raise ConflictError("undo_target_missing", "The created event no longer exists")
            await self.events.delete(session, current_event)
            return AppliedOperation(
                current_event.id, _snapshot_event(current_event), None, {}, False, "", None
            )
        if undo.snapshot is None:
            raise ConflictError("undo_snapshot_missing", "The event snapshot is unavailable")
        restored_event = await self._restore_event(
            session, action.user_id, current_event, undo.snapshot
        )
        return AppliedOperation(
            restored_event.id,
            None,
            _snapshot_event(restored_event),
            _event_business_fields(undo.snapshot),
            True,
            "",
            None,
            True,
        )

    async def _restore_task(
        self,
        session: AsyncSession,
        user_id: str,
        current: Task | None,
        snapshot: dict[str, Any],
    ) -> Task:
        data = TaskCreate.model_validate(_task_business_fields(snapshot))
        if current is None:
            current = Task(
                id=snapshot["id"],
                user_id=user_id,
                **data.model_dump(),
                version=int(snapshot["version"]),
            )
            session.add(current)
        else:
            for key, value in data.model_dump().items():
                setattr(current, key, value)
            current.version += 1
        await session.flush()
        return current

    async def _restore_event(
        self,
        session: AsyncSession,
        user_id: str,
        current: CalendarEvent | None,
        snapshot: dict[str, Any],
    ) -> CalendarEvent:
        data = EventCreate.model_validate(
            _event_business_fields(snapshot) | {"allow_conflict": True}
        )
        values = data.model_dump(exclude={"allow_conflict"})
        if current is None:
            current = CalendarEvent(
                id=snapshot["id"],
                user_id=user_id,
                **values,
                version=int(snapshot["version"]),
            )
            session.add(current)
        else:
            for key, value in values.items():
                setattr(current, key, value)
            current.version += 1
        await session.flush()
        return current

    async def _verify_undo(
        self,
        session: AsyncSession,
        user_id: str,
        action: PendingAction,
        operation: AppliedOperation,
    ) -> VerificationReport:
        with observe_component(self.metrics, "verification", "verify") as observation:
            report = await self._verify_undo_operation(session, user_id, action, operation)
            observation.error = not report.success
            return report

    async def _verify_undo_operation(
        self,
        session: AsyncSession,
        user_id: str,
        action: PendingAction,
        operation: AppliedOperation,
    ) -> VerificationReport:
        if action.entity_type == EntityType.TASK:
            return await self.verifier.verify_task(
                session,
                user_id,
                operation.record_id,
                operation.expected_fields,
                should_exist=operation.should_exist,
            )
        return await self.verifier.verify_event(
            session,
            user_id,
            operation.record_id,
            operation.expected_fields,
            should_exist=operation.should_exist,
            allow_conflict=True,
        )

    async def _pending_or_404(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
        lock: bool = False,
    ) -> PendingAction:
        action = await self.actions.get_pending(session, user_id, action_id, lock=lock)
        if action is None:
            raise NotFoundError("pending_action", action_id)
        return action

    async def _task_or_404(
        self, session: AsyncSession, user_id: str, task_id: str, lock: bool = False
    ) -> Task:
        task = (
            await self.tasks.get_for_update(session, user_id, task_id)
            if lock
            else await self.tasks.get(session, user_id, task_id)
        )
        if task is None:
            raise NotFoundError("task", task_id)
        return task

    async def _event_or_404(
        self, session: AsyncSession, user_id: str, event_id: str, lock: bool = False
    ) -> CalendarEvent:
        event = (
            await self.events.get_for_update(session, user_id, event_id)
            if lock
            else await self.events.get(session, user_id, event_id)
        )
        if event is None:
            raise NotFoundError("event", event_id)
        return event

    async def _reject_if_expired(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
    ) -> None:
        expired = False
        async with session.begin():
            action = await self._pending_or_404(session, user_id, action_id, lock=True)
            if action.state == PendingActionState.EXPIRED:
                expired = True
            elif action.expires_at <= utc_now() and action.state not in TERMINAL_STATES:
                action.state = PendingActionState.EXPIRED
                expired = True
        if expired:
            raise ConflictError("action_expired", "The pending action has expired")

    async def _reject_if_undo_expired(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
    ) -> None:
        expired = False
        async with session.begin():
            action = await self._pending_or_404(session, user_id, action_id, lock=True)
            if action.state == PendingActionState.EXECUTED:
                log = await self.actions.log_for_action(session, user_id, action_id)
                if log is not None:
                    undo = await self.actions.undo_for_log(session, user_id, log.id)
                    if (
                        undo is not None
                        and undo.state == UndoState.AVAILABLE
                        and undo.expires_at <= utc_now()
                    ):
                        undo.state = UndoState.EXPIRED
                        expired = True
        if expired:
            raise ConflictError("undo_expired", "The undo window has expired")

    @staticmethod
    def _check_version(current: int, expected: int | None) -> None:
        if expected is not None and current != expected:
            raise ConflictError(
                "version_conflict",
                "The record changed after it was reviewed",
                {"expected_version": expected, "current_version": current},
            )


def _task_create_from_payload(payload: dict[str, Any]) -> TaskCreate:
    return TaskCreate.model_validate(
        payload | {"source_type": payload.get("source_type", SourceType.VOICE)}
    )


def _event_create_from_payload(payload: dict[str, Any], allow_conflict: bool) -> EventCreate:
    return EventCreate.model_validate(
        payload
        | {
            "source_type": payload.get("source_type", SourceType.VOICE),
            "allow_conflict": allow_conflict,
        }
    )


def _event_create_from_draft(payload: EventDraft, allow_conflict: bool) -> EventCreate:
    return _event_create_from_payload(
        payload.model_dump(mode="json", exclude_unset=True), allow_conflict
    )


def _merge_task(task: Task, patch: TaskDraft) -> TaskCreate:
    current = _task_business_fields(_snapshot_task(task))
    current.update(patch.model_dump(mode="json", exclude_unset=True, exclude={"expected_version"}))
    return TaskCreate.model_validate(current)


def _merge_event(event: CalendarEvent, patch: EventDraft, allow_conflict: bool) -> EventCreate:
    current = _event_business_fields(_snapshot_event(event))
    current.update(patch.model_dump(mode="json", exclude_unset=True, exclude={"expected_version"}))
    current["allow_conflict"] = allow_conflict
    return EventCreate.model_validate(current)


def _task_business_fields(snapshot: dict[str, Any]) -> dict[str, Any]:
    keys = {
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
    }
    return {key: snapshot.get(key) for key in keys}


def _event_business_fields(snapshot: dict[str, Any]) -> dict[str, Any]:
    keys = {
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
    }
    return {key: snapshot.get(key) for key in keys}


def _operation_to_dict(operation: AppliedOperation) -> dict[str, Any]:
    return {
        "record_id": operation.record_id,
        "before_snapshot": operation.before_snapshot,
        "after_snapshot": operation.after_snapshot,
        "expected_fields": operation.expected_fields,
        "should_exist": operation.should_exist,
        "undo_action": operation.undo_action,
        "undo_snapshot": operation.undo_snapshot,
        "allow_conflict": operation.allow_conflict,
    }


def _operation_from_result(result: dict[str, Any]) -> AppliedOperation:
    try:
        data = result["_operation"]
        return AppliedOperation(**data)
    except (KeyError, TypeError, ValidationError) as exc:
        raise ConflictError(
            "retry_state_invalid", "The prior execution cannot be retried safely"
        ) from exc


def _public_result(result: dict[str, Any]) -> ExecutionResult:
    public = {key: value for key, value in result.items() if key in ExecutionResult.model_fields}
    return ExecutionResult.model_validate(public)


def _provisional_result(
    action_id: str, action_type: ActionType, operation: AppliedOperation
) -> dict[str, Any]:
    return {
        "success": False,
        "action": action_type.value,
        "record_id": operation.record_id,
        "verified_fields": {},
        "side_effects": [],
        "message": "数据库写入已提交，正在验证",
        "error": None,
        "retryable": True,
        "action_id": action_id,
        "record": None,
        "applied": True,
        "_operation": _operation_to_dict(operation),
    }


def _execution_result(
    action: PendingAction,
    operation: AppliedOperation,
    report: VerificationReport,
    *,
    action_name: str | None = None,
) -> ExecutionResult:
    record: TaskView | EventView | None = None
    if isinstance(report.record, Task):
        record = TaskView.model_validate(report.record)
    elif isinstance(report.record, CalendarEvent):
        record = EventView.model_validate(report.record)
    return ExecutionResult(
        success=report.success,
        action=action_name or action.action_type.value,
        record_id=operation.record_id,
        verified_fields=report.verified_fields,
        side_effects=list(report.side_effects),
        message=(
            _action_label(action.action_type)
            if report.success and action_name is None
            else "撤销已完成并通过数据库验证"
            if report.success
            else "数据库最终状态验证失败，未报告为成功"
        ),
        error=None if report.success else "post_commit_verification_failed",
        retryable=not report.success and action.attempt_count < action.max_attempts,
        action_id=action.id,
        record=record,
    )
