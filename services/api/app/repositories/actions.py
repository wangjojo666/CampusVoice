from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import ActionLog, PendingAction, UndoRecord
from app.models.enums import PendingActionState


class ActionRepository:
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

    async def log_for_action(self, session: AsyncSession, action_id: str) -> ActionLog | None:
        log: ActionLog | None = await session.scalar(
            select(ActionLog)
            .where(ActionLog.pending_action_id == action_id)
            .order_by(ActionLog.created_at.desc())
        )
        return log

    async def undo_for_log(self, session: AsyncSession, log_id: str) -> UndoRecord | None:
        undo: UndoRecord | None = await session.scalar(
            select(UndoRecord).where(UndoRecord.action_log_id == log_id)
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
