from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import ActionLog, PendingAction, UndoRecord
from app.models.enums import PendingActionState, UndoState


class ActionRepository:
    async def finalize_execution(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
        *,
        expected_attempt_count: int,
        success: bool,
        result: dict[str, Any],
        last_error: str | None,
        executed_at: datetime | None,
    ) -> PendingAction | None:
        allowed_states = (
            [PendingActionState.EXECUTING, PendingActionState.FAILED]
            if success
            else [PendingActionState.EXECUTING]
        )
        values: dict[str, Any] = {
            "state": PendingActionState.EXECUTED if success else PendingActionState.FAILED,
            "last_error": last_error,
            "result": result,
        }
        if success:
            values["executed_at"] = executed_at
        finalized: PendingAction | None = await session.scalar(
            update(PendingAction)
            .where(
                PendingAction.id == action_id,
                PendingAction.user_id == user_id,
                PendingAction.attempt_count == expected_attempt_count,
                PendingAction.state.in_(allowed_states),
            )
            .values(**values)
            .returning(PendingAction)
            .execution_options(populate_existing=True)
        )
        return finalized

    async def cancel_pending(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
        *,
        cancelled_at: datetime,
        reason: str,
    ) -> PendingAction | None:
        cancelled: PendingAction | None = await session.scalar(
            update(PendingAction)
            .where(
                PendingAction.id == action_id,
                PendingAction.user_id == user_id,
                or_(
                    PendingAction.state.in_(
                        [
                            PendingActionState.NEEDS_INPUT,
                            PendingActionState.AWAITING_CONFIRMATION,
                            PendingActionState.AWAITING_SECOND_CONFIRMATION,
                            PendingActionState.READY,
                        ]
                    ),
                    and_(
                        PendingAction.state == PendingActionState.FAILED,
                        PendingAction.result["applied"].as_boolean().is_not(True),
                    ),
                ),
            )
            .values(
                state=PendingActionState.CANCELLED,
                cancelled_at=cancelled_at,
                last_error=reason,
            )
            .returning(PendingAction)
            .execution_options(populate_existing=True)
        )
        return cancelled

    async def claim_execution(
        self, session: AsyncSession, user_id: str, action_id: str
    ) -> PendingAction | None:
        claimed: PendingAction | None = await session.scalar(
            update(PendingAction)
            .where(
                PendingAction.id == action_id,
                PendingAction.user_id == user_id,
                PendingAction.state.in_([PendingActionState.READY, PendingActionState.FAILED]),
                PendingAction.attempt_count < PendingAction.max_attempts,
            )
            .values(
                state=PendingActionState.EXECUTING,
                attempt_count=PendingAction.attempt_count + 1,
            )
            .returning(PendingAction)
            .execution_options(populate_existing=True)
        )
        return claimed

    async def claim_undo_application(
        self, session: AsyncSession, user_id: str, action_id: str
    ) -> PendingAction | None:
        claimed: PendingAction | None = await session.scalar(
            update(PendingAction)
            .where(
                PendingAction.id == action_id,
                PendingAction.user_id == user_id,
                PendingAction.state == PendingActionState.EXECUTED,
            )
            .values(state=PendingActionState.UNDONE)
            .returning(PendingAction)
            .execution_options(populate_existing=True)
        )
        return claimed

    async def claim_undo_verification(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
        *,
        expected_token: str,
        result: dict[str, Any],
    ) -> PendingAction | None:
        claimed: PendingAction | None = await session.scalar(
            update(PendingAction)
            .where(
                PendingAction.id == action_id,
                PendingAction.user_id == user_id,
                PendingAction.state == PendingActionState.UNDONE,
                PendingAction.result["_undo_phase"].as_string() == "applied",
                PendingAction.result["_undo_verify_token"].as_string() == expected_token,
            )
            .values(result=result)
            .returning(PendingAction)
            .execution_options(populate_existing=True)
        )
        return claimed

    async def finalize_undo_result(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
        *,
        expected_token: str,
        result: dict[str, Any],
    ) -> PendingAction | None:
        finalized: PendingAction | None = await session.scalar(
            update(PendingAction)
            .where(
                PendingAction.id == action_id,
                PendingAction.user_id == user_id,
                PendingAction.state == PendingActionState.UNDONE,
                PendingAction.result["_undo_phase"].as_string() == "applied",
                PendingAction.result["_undo_verify_token"].as_string() == expected_token,
            )
            .values(result=result)
            .returning(PendingAction)
            .execution_options(populate_existing=True)
        )
        return finalized

    async def finalize_undo_record(
        self,
        session: AsyncSession,
        user_id: str,
        action_log_id: str,
        *,
        success: bool,
        error_message: str | None,
        undone_at: datetime | None,
    ) -> UndoRecord | None:
        values: dict[str, Any] = {"error_message": error_message}
        if success:
            values.update(state=UndoState.UNDONE, undone_at=undone_at)
        finalized: UndoRecord | None = await session.scalar(
            update(UndoRecord)
            .where(
                UndoRecord.action_log_id == action_log_id,
                UndoRecord.user_id == user_id,
                UndoRecord.state == UndoState.FAILED,
            )
            .values(**values)
            .returning(UndoRecord)
            .execution_options(populate_existing=True)
        )
        return finalized

    async def record_failed_execution_attempt(
        self,
        session: AsyncSession,
        user_id: str,
        action_id: str,
        *,
        prior_attempt_count: int,
        error: str,
    ) -> PendingAction | None:
        failed: PendingAction | None = await session.scalar(
            update(PendingAction)
            .where(
                PendingAction.id == action_id,
                PendingAction.user_id == user_id,
                PendingAction.state.in_([PendingActionState.READY, PendingActionState.FAILED]),
                PendingAction.attempt_count == prior_attempt_count,
            )
            .values(
                state=PendingActionState.FAILED,
                attempt_count=prior_attempt_count + 1,
                last_error=error[:1_000],
            )
            .returning(PendingAction)
            .execution_options(populate_existing=True)
        )
        return failed

    async def get_pending(
        self, session: AsyncSession, user_id: str, action_id: str, *, lock: bool = False
    ) -> PendingAction | None:
        statement = select(PendingAction).where(
            PendingAction.id == action_id, PendingAction.user_id == user_id
        )
        if lock:
            statement = statement.with_for_update()
        action: PendingAction | None = await session.scalar(statement)
        return action

    async def by_idempotency_key(
        self, session: AsyncSession, user_id: str, key: str
    ) -> PendingAction | None:
        action: PendingAction | None = await session.scalar(
            select(PendingAction).where(
                PendingAction.user_id == user_id, PendingAction.idempotency_key == key
            )
        )
        return action

    async def list_logs(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        success: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ActionLog], int]:
        filters = [ActionLog.user_id == user_id]
        if success is not None:
            filters.append(ActionLog.success.is_(success))
        count = await session.scalar(select(func.count(ActionLog.id)).where(*filters))
        logs = await session.scalars(
            select(ActionLog)
            .where(*filters)
            .order_by(ActionLog.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(logs), int(count or 0)

    async def log_for_action(
        self, session: AsyncSession, user_id: str, action_id: str
    ) -> ActionLog | None:
        log: ActionLog | None = await session.scalar(
            select(ActionLog)
            .where(ActionLog.pending_action_id == action_id, ActionLog.user_id == user_id)
            .order_by(ActionLog.created_at.desc())
        )
        return log

    async def undo_for_log(
        self, session: AsyncSession, user_id: str, log_id: str
    ) -> UndoRecord | None:
        undo: UndoRecord | None = await session.scalar(
            select(UndoRecord).where(
                UndoRecord.action_log_id == log_id,
                UndoRecord.user_id == user_id,
            )
        )
        return undo

    async def expire_old_actions(self, session: AsyncSession, user_id: str, now: object) -> int:
        pending = list(
            await session.scalars(
                select(PendingAction).where(
                    PendingAction.user_id == user_id,
                    PendingAction.expires_at <= now,
                    PendingAction.state.in_(
                        [
                            PendingActionState.NEEDS_INPUT,
                            PendingActionState.AWAITING_CONFIRMATION,
                            PendingActionState.AWAITING_SECOND_CONFIRMATION,
                            PendingActionState.READY,
                            PendingActionState.FAILED,
                        ]
                    ),
                )
            )
        )
        for action in pending:
            action.state = PendingActionState.EXPIRED
        return len(pending)
