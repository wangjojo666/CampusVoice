import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.types import utc_now
from app.models.entities import (
    CalendarEvent,
    Document,
    DocumentChunk,
    ImpactCase,
    ImpactMigrationItem,
    ImpactMigrationPlan,
    NoticeChangeItem,
    NoticeChangeSet,
    NoticeClaim,
    NoticeSeries,
    Task,
    UserSettings,
    new_id,
)
from app.models.enums import DocumentStatus
from app.schemas.notice_radar import (
    ChangeEvidenceView,
    ImpactCaseView,
    ImpactListView,
    MigrationExecuteRequest,
    MigrationItemView,
    MigrationPlanView,
    MigrationUndoRequest,
    NoticeChangeItemView,
    NoticeChangeSetView,
    NoticeClaimView,
    NoticeSeriesCreate,
    NoticeSeriesView,
    NoticeTimelineView,
    NoticeVersionCreate,
    NoticeVersionView,
    RadarCardView,
    RadarView,
    VerificationReceiptView,
)
from app.services.errors import ConflictError, DomainError, NotFoundError
from app.services.notices.claims import (
    CHANGE_ALGORITHM_VERSION,
    EXTRACTOR_VERSION,
    extract_claims,
    normalize_course,
    normalize_grade,
    normalize_major,
    normalize_semantic_text,
)

EntityName = Literal["task", "event"]
ApplicabilityState = Literal["applicable", "not_applicable", "needs_review"]


@dataclass(frozen=True, slots=True)
class ApplicabilityResult:
    state: ApplicabilityState
    reason: str


class NoticeRadarService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def create_series(
        self, session: AsyncSession, user_id: str, data: NoticeSeriesCreate
    ) -> NoticeSeriesView:
        canonical_key = data.canonical_key.strip().casefold()
        existing = await session.scalar(
            select(NoticeSeries).where(
                NoticeSeries.user_id == user_id,
                NoticeSeries.canonical_key == canonical_key,
            )
        )
        if existing is not None:
            raise ConflictError(
                "notice_series_exists",
                "A notice series with this explicit canonical key already exists",
                {"series_id": existing.id},
            )
        series = NoticeSeries(
            user_id=user_id,
            canonical_key=canonical_key,
            normalized_title=_normalize_title(data.title),
            department=data.department,
            source_key=data.source_key,
        )
        session.add(series)
        await session.commit()
        return await self._series_view(session, series)

    async def list_series(
        self, session: AsyncSession, user_id: str, *, limit: int, offset: int
    ) -> list[NoticeSeriesView]:
        rows = list(
            await session.scalars(
                select(NoticeSeries)
                .where(NoticeSeries.user_id == user_id)
                .order_by(NoticeSeries.updated_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return [await self._series_view(session, row) for row in rows]

    async def add_version(
        self,
        session: AsyncSession,
        user_id: str,
        series_id: str,
        data: NoticeVersionCreate,
    ) -> NoticeVersionView:
        series = await self._owned_series(session, user_id, series_id)
        existing_revision = await session.scalar(
            select(Document).where(
                Document.user_id == user_id,
                Document.series_id == series_id,
                Document.revision_number == data.revision_number,
            )
        )
        if existing_revision is not None:
            if existing_revision.content_sha256 == _content_hash(data.content):
                return await self._version_view(session, existing_revision, include_claims=True)
            raise ConflictError(
                "notice_revision_exists",
                "This revision number already contains different content",
                {"document_id": existing_revision.id},
            )

        current = await session.scalar(
            select(Document).where(
                Document.user_id == user_id,
                Document.series_id == series_id,
                Document.is_current.is_(True),
            )
        )
        if current is None and data.supersedes_document_id is not None:
            raise ConflictError(
                "unexpected_supersedes_document",
                "The first revision cannot supersede another document",
            )
        if current is not None:
            if data.supersedes_document_id is None:
                raise DomainError(
                    "version_confirmation_required",
                    "An explicit supersedes_document_id is required; "
                    "title similarity is never enough",
                    status_code=422,
                    details={"expected_document_id": current.id},
                )
            if data.supersedes_document_id != current.id:
                raise ConflictError(
                    "ambiguous_version_chain",
                    "The selected predecessor is not the current revision of this series",
                    {"current_document_id": current.id},
                )
            if data.revision_number <= (current.revision_number or 0):
                raise ConflictError(
                    "non_monotonic_revision",
                    "A new notice revision must have a higher revision number",
                )

        duplicate_content = await session.scalar(
            select(Document).where(
                Document.user_id == user_id,
                Document.content_sha256 == _content_hash(data.content),
            )
        )
        if duplicate_content is not None:
            raise ConflictError(
                "duplicate_document",
                "Identical content was already imported and was not duplicated",
                {"document_id": duplicate_content.id},
            )

        now = utc_now()
        document_id = new_id("doc")
        chunk_id = new_id("chk")
        document = Document(
            id=document_id,
            user_id=user_id,
            title=data.title,
            department=data.department or series.department,
            publish_date=data.publish_date,
            applicable_group=data.applicable_group,
            source_url=data.source_url,
            version=data.version_label,
            file_type="txt",
            storage_path=f"inline://notice/{series_id}/{data.revision_number}",
            content_sha256=_content_hash(data.content),
            status=DocumentStatus.READY,
            series_id=series_id,
            supersedes_document_id=data.supersedes_document_id,
            revision_number=data.revision_number,
            effective_at=data.effective_at,
            is_current=True,
            ingest_source=data.ingest_source,
        )
        chunk = DocumentChunk(
            id=chunk_id,
            document_id=document_id,
            ordinal=0,
            content=data.content,
            metadata_json={"source": data.ingest_source, "evidence_offsets": "unicode-codepoints"},
        )
        if current is not None:
            current.is_current = False
        series.updated_at = now
        session.add(document)
        session.add(chunk)
        await session.flush()
        for extracted in extract_claims(data.content):
            session.add(
                NoticeClaim(
                    user_id=user_id,
                    document_id=document.id,
                    chunk_id=chunk.id,
                    claim_key=extracted.key,
                    claim_type=extracted.claim_type,
                    value_json=extracted.value,
                    normalized_value_json=extracted.normalized,
                    audience_rule_json=extracted.audience,
                    confidence=extracted.confidence,
                    evidence_start=extracted.start,
                    evidence_end=extracted.end,
                    extractor_version=EXTRACTOR_VERSION,
                    review_state=extracted.review_state,
                )
            )
        await session.flush()
        if current is not None:
            await self._ensure_claim_version(session, user_id, current)
            change_set = await self._create_change_set(session, user_id, series, current, document)
            await self._detect_impacts_internal(session, user_id, change_set)
        await session.commit()
        return await self._version_view(session, document, include_claims=True)

    async def timeline(
        self, session: AsyncSession, user_id: str, series_id: str
    ) -> NoticeTimelineView:
        series = await self._owned_series(session, user_id, series_id)
        documents = list(
            await session.scalars(
                select(Document)
                .where(Document.user_id == user_id, Document.series_id == series_id)
                .order_by(Document.revision_number.asc())
            )
        )
        return NoticeTimelineView(
            series=await self._series_view(session, series),
            versions=[
                await self._version_view(session, document, include_claims=False)
                for document in documents
            ],
        )

    async def claims(
        self, session: AsyncSession, user_id: str, document_id: str
    ) -> list[NoticeClaimView]:
        await self._owned_document(session, user_id, document_id)
        claims = list(
            await session.scalars(
                select(NoticeClaim)
                .where(
                    NoticeClaim.user_id == user_id,
                    NoticeClaim.document_id == document_id,
                )
                .order_by(NoticeClaim.claim_key)
            )
        )
        return [await self._claim_view(session, item) for item in claims]

    async def reanalyze(
        self, session: AsyncSession, user_id: str, document_id: str
    ) -> list[NoticeClaimView]:
        document = await self._owned_document(session, user_id, document_id)
        chunks = list(
            await session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document.id)
                .order_by(DocumentChunk.ordinal)
            )
        )
        existing = await session.scalar(
            select(func.count(NoticeClaim.id)).where(
                NoticeClaim.user_id == user_id,
                NoticeClaim.document_id == document_id,
                NoticeClaim.extractor_version == EXTRACTOR_VERSION,
            )
        )
        if existing:
            return await self.claims(session, user_id, document_id)
        for chunk in chunks:
            for extracted in extract_claims(chunk.content):
                session.add(
                    NoticeClaim(
                        user_id=user_id,
                        document_id=document.id,
                        chunk_id=chunk.id,
                        claim_key=extracted.key,
                        claim_type=extracted.claim_type,
                        value_json=extracted.value,
                        normalized_value_json=extracted.normalized,
                        audience_rule_json=extracted.audience,
                        confidence=extracted.confidence,
                        evidence_start=extracted.start,
                        evidence_end=extracted.end,
                        extractor_version=EXTRACTOR_VERSION,
                        review_state=extracted.review_state,
                    )
                )
        await session.commit()
        return await self.claims(session, user_id, document_id)

    async def change_set(
        self, session: AsyncSession, user_id: str, change_set_id: str
    ) -> NoticeChangeSetView:
        row = await session.scalar(
            select(NoticeChangeSet).where(
                NoticeChangeSet.id == change_set_id,
                NoticeChangeSet.user_id == user_id,
            )
        )
        if row is None:
            raise NotFoundError("notice change set", change_set_id)
        return await self._change_set_view(session, row)

    async def review_change(
        self,
        session: AsyncSession,
        user_id: str,
        change_item_id: str,
        decision: Literal["approved", "rejected"],
    ) -> NoticeChangeItemView:
        item = await session.scalar(
            select(NoticeChangeItem).where(
                NoticeChangeItem.id == change_item_id,
                NoticeChangeItem.user_id == user_id,
            )
        )
        if item is None:
            raise NotFoundError("notice change item", change_item_id)
        if item.review_state == decision:
            return await self._change_item_view(session, item)
        item.review_state = decision
        change_set = await self._owned_change_set(session, user_id, item.change_set_id)
        now = utc_now()
        if decision == "rejected":
            impacts = list(
                await session.scalars(
                    select(ImpactCase).where(
                        ImpactCase.user_id == user_id,
                        ImpactCase.change_item_id == item.id,
                        ImpactCase.status == "open",
                    )
                )
            )
            for impact in impacts:
                impact.status = "dismissed"
                impact.resolved_at = now
            await session.execute(
                update(ImpactMigrationPlan)
                .where(
                    ImpactMigrationPlan.user_id == user_id,
                    ImpactMigrationPlan.change_set_id == item.change_set_id,
                    ImpactMigrationPlan.status == "ready",
                )
                .values(status="invalidated", version=ImpactMigrationPlan.version + 1)
            )
        else:
            await self._detect_impacts_internal(session, user_id, change_set)
        await session.commit()
        return await self._change_item_view(session, item)

    async def detect_impacts(
        self, session: AsyncSession, user_id: str, change_set_id: str
    ) -> ImpactListView:
        change_set = await self._owned_change_set(session, user_id, change_set_id)
        await self._detect_impacts_internal(session, user_id, change_set)
        await session.commit()
        return await self.list_impacts(
            session, user_id, change_set_id=change_set_id, status=None, limit=200, offset=0
        )

    async def list_impacts(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        change_set_id: str | None,
        status: str | None,
        limit: int,
        offset: int,
    ) -> ImpactListView:
        predicates = [ImpactCase.user_id == user_id]
        if status is not None:
            predicates.append(ImpactCase.status == status)
        statement = select(ImpactCase).where(*predicates)
        count_statement = select(func.count(ImpactCase.id)).where(*predicates)
        if change_set_id is not None:
            statement = statement.join(
                NoticeChangeItem, NoticeChangeItem.id == ImpactCase.change_item_id
            ).where(NoticeChangeItem.change_set_id == change_set_id)
            count_statement = count_statement.join(
                NoticeChangeItem, NoticeChangeItem.id == ImpactCase.change_item_id
            ).where(NoticeChangeItem.change_set_id == change_set_id)
        total = int(await session.scalar(count_statement) or 0)
        rows = list(
            await session.scalars(
                statement.order_by(ImpactCase.detected_at.desc()).limit(limit).offset(offset)
            )
        )
        return ImpactListView(items=[_impact_view(item) for item in rows], total=total)

    async def build_plan(
        self, session: AsyncSession, user_id: str, change_set_id: str
    ) -> MigrationPlanView:
        change_set = await self._owned_change_set(session, user_id, change_set_id)
        await self._ensure_change_reviewed(session, change_set.id)
        applicability = await self._applicability(session, user_id, change_set.to_document_id)
        if applicability.state != "applicable":
            code = (
                "notice_not_applicable"
                if applicability.state == "not_applicable"
                else "notice_applicability_review_required"
            )
            raise DomainError(
                code,
                "This notice is not safely applicable to the current profile",
                status_code=422,
                details={
                    "applicability": applicability.state,
                    "reason": applicability.reason,
                },
            )
        all_open_impacts = list(
            await session.scalars(
                select(ImpactCase)
                .join(NoticeChangeItem, NoticeChangeItem.id == ImpactCase.change_item_id)
                .where(
                    ImpactCase.user_id == user_id,
                    ImpactCase.status == "open",
                    NoticeChangeItem.change_set_id == change_set.id,
                )
                .order_by(ImpactCase.entity_type, ImpactCase.entity_id)
            )
        )
        manual_impacts = [row for row in all_open_impacts if row.requires_manual_review]
        if manual_impacts:
            raise DomainError(
                "manual_impact_review_required",
                "Removed or de-scoped notice effects require an explicit human decision",
                status_code=422,
                details={
                    "impact_ids": [row.id for row in manual_impacts],
                    "recommendations": [row.recommended_action for row in manual_impacts],
                },
            )
        impact_rows = [
            row
            for row in all_open_impacts
            if row.recommended_action == "apply" and bool(row.proposed_patch)
        ]
        if not impact_rows:
            raise DomainError(
                "no_open_impacts",
                "No reviewed, applicable task or calendar impacts are available for migration",
                status_code=422,
            )
        grouped: dict[tuple[str, str], list[ImpactCase]] = defaultdict(list)
        for impact in impact_rows:
            grouped[(impact.entity_type, impact.entity_id)].append(impact)

        latest = await session.scalar(
            select(ImpactMigrationPlan)
            .where(
                ImpactMigrationPlan.user_id == user_id,
                ImpactMigrationPlan.change_set_id == change_set_id,
            )
            .order_by(ImpactMigrationPlan.generation.desc())
        )
        if latest is not None and latest.status not in {"ready", "undone", "invalidated"}:
            return await self.plan(session, user_id, latest.id)
        existing = latest if latest is not None and latest.status == "ready" else None
        generation = 1 if latest is None else latest.generation + (0 if existing else 1)

        prepared: list[tuple[str, str, dict[str, Any], dict[str, Any], list[str]]] = []
        conflicts: list[dict[str, Any]] = []
        for (entity_type, entity_id), impacts in grouped.items():
            entity = await self._owned_entity(session, user_id, entity_type, entity_id)
            before = _snapshot(entity_type, entity)
            patch: dict[str, Any] = {}
            source_claim_ids: list[str] = []
            primary_replacement: NoticeClaim | None = None
            candidate_replacements: list[NoticeClaim] = []
            for impact in impacts:
                patch.update(impact.proposed_patch)
                change = await session.get(NoticeChangeItem, impact.change_item_id)
                if change is not None and change.after_claim_id is not None:
                    source_claim_ids.append(change.after_claim_id)
                    replacement = await session.get(NoticeClaim, change.after_claim_id)
                    if replacement is not None:
                        candidate_replacements.append(replacement)
                        if change.before_claim_id == entity.source_claim_id:
                            primary_replacement = replacement
            if primary_replacement is None and entity.source_claim_id is not None:
                old_primary = await session.get(NoticeClaim, entity.source_claim_id)
                if old_primary is not None:
                    successor = await session.scalar(
                        select(NoticeClaim).where(
                            NoticeClaim.user_id == user_id,
                            NoticeClaim.document_id == change_set.to_document_id,
                            NoticeClaim.claim_key == old_primary.claim_key,
                            NoticeClaim.extractor_version == EXTRACTOR_VERSION,
                        )
                    )
                    if successor is not None and _stable_json(
                        successor.normalized_value_json
                    ) == _stable_json(old_primary.normalized_value_json):
                        primary_replacement = successor
            if primary_replacement is None and entity.source_claim_id is None:
                primary_replacement = min(
                    candidate_replacements,
                    key=lambda claim: _primary_claim_rank(entity_type, claim),
                    default=None,
                )
            if primary_replacement is not None:
                source_claim_ids.append(primary_replacement.id)
                patch.update(
                    {
                        "source_document_id": primary_replacement.document_id,
                        "source_chunk_id": primary_replacement.chunk_id,
                        "source_claim_id": primary_replacement.id,
                    }
                )
            source_claim_ids = sorted(set(source_claim_ids))
            after = _patched_snapshot(before, patch, source_claim_ids)
            if entity_type == "event":
                conflicts.extend(await self._event_conflicts(session, user_id, entity_id, after))
            prepared.append((entity_type, entity_id, before, patch, source_claim_ids))

        risk_level = "high" if conflicts else "medium" if len(prepared) > 3 else "low"
        signature = _plan_signature(prepared, conflicts)
        if existing is not None:
            prior_items = list(
                await session.scalars(
                    select(ImpactMigrationItem).where(ImpactMigrationItem.plan_id == existing.id)
                )
            )
            prior_signature = _plan_signature(
                [
                    (
                        item.entity_type,
                        item.entity_id,
                        item.before_snapshot,
                        item.proposed_patch,
                        item.source_claim_ids,
                    )
                    for item in prior_items
                ],
                existing.conflicts_json,
            )
            if prior_signature == signature:
                return await self.plan(session, user_id, existing.id)

            # A ready preview is reusable only while its frozen business
            # inputs remain identical. Preserve the stale generation as
            # immutable history and create a new plan for changed conflicts,
            # entity snapshots, patches, or source lineage.
            existing.status = "invalidated"
            existing.version += 1
            generation = existing.generation + 1
            existing = None

        plan = existing or ImpactMigrationPlan(
            user_id=user_id,
            change_set_id=change_set_id,
            generation=generation,
        )
        if existing is None:
            session.add(plan)
            await session.flush()
        plan.status = "ready"
        plan.risk_level = risk_level
        plan.conflicts_json = conflicts
        plan.verification_json = {}
        plan.execute_receipt_json = {}
        plan.undo_receipt_json = {}
        for entity_type, entity_id, before, patch, source_claim_ids in prepared:
            session.add(
                ImpactMigrationItem(
                    plan_id=plan.id,
                    user_id=user_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    expected_version=int(before["version"]),
                    before_snapshot=before,
                    proposed_patch=patch,
                    source_claim_ids=source_claim_ids,
                )
            )
        for impact in impact_rows:
            impact.migration_plan_id = plan.id
        await session.commit()
        return await self.plan(session, user_id, plan.id)

    async def plan(self, session: AsyncSession, user_id: str, plan_id: str) -> MigrationPlanView:
        plan = await self._owned_plan(session, user_id, plan_id)
        items = list(
            await session.scalars(
                select(ImpactMigrationItem)
                .where(ImpactMigrationItem.plan_id == plan.id)
                .order_by(ImpactMigrationItem.entity_type, ImpactMigrationItem.entity_id)
            )
        )
        return _plan_view(plan, items)

    async def execute(
        self,
        session: AsyncSession,
        user_id: str,
        plan_id: str,
        request: MigrationExecuteRequest,
    ) -> VerificationReceiptView:
        plan = await self._owned_plan(session, user_id, plan_id)
        if plan.execution_idempotency_key is not None or plan.status in {
            "applied",
            "verified",
            "verification_failed",
            "undoing",
            "undo_applied",
            "undone",
            "undo_verification_failed",
        }:
            if plan.execution_idempotency_key != request.idempotency_key:
                raise ConflictError("migration_already_executed", "This migration already executed")
            if plan.status in {"applied", "verification_failed"}:
                return await self._verify(user_id, plan_id, operation="execute")
            return await self.receipt(user_id, plan_id, operation="execute")
        if plan.status != "ready":
            raise ConflictError(
                "migration_not_executable",
                "This migration preview is no longer executable",
                {"status": plan.status},
            )
        if plan.version != request.plan_version:
            raise ConflictError(
                "migration_plan_stale",
                "The preview changed; generate a new preview before executing",
                {"current_version": plan.version},
            )
        required = _required_confirmations(plan)
        if request.confirmation_stages != required:
            raise DomainError(
                "confirmation_stage_mismatch",
                f"This migration requires {required} confirmation stage(s)",
                status_code=422,
            )
        claimed = (
            await session.execute(
                update(ImpactMigrationPlan)
                .where(
                    ImpactMigrationPlan.id == plan.id,
                    ImpactMigrationPlan.user_id == user_id,
                    ImpactMigrationPlan.status == "ready",
                    ImpactMigrationPlan.version == request.plan_version,
                )
                .values(
                    status="executing",
                    execution_idempotency_key=request.idempotency_key,
                    updated_at=utc_now(),
                )
                .returning(ImpactMigrationPlan.id)
            )
        ).scalar_one_or_none()
        if claimed is None:
            await session.rollback()
            fresh = await self._owned_plan(session, user_id, plan_id)
            if fresh.execution_idempotency_key == request.idempotency_key:
                if fresh.status == "applied":
                    return await self._verify(user_id, plan_id, operation="execute")
                if fresh.execute_receipt_json:
                    return await self.receipt(user_id, plan_id, operation="execute")
            raise ConflictError(
                "migration_execution_conflict", "Another request already claimed this migration"
            )
        try:
            plan.status = "executing"
            plan.execution_idempotency_key = request.idempotency_key
            applicability = await self._applicability(
                session, user_id, await self._plan_target_document_id(session, plan)
            )
            if applicability.state != "applicable":
                raise ConflictError(
                    "migration_applicability_changed",
                    "The notice applicability changed after preview",
                    {"applicability": applicability.state, "reason": applicability.reason},
                )
            items = list(
                await session.scalars(
                    select(ImpactMigrationItem).where(ImpactMigrationItem.plan_id == plan.id)
                )
            )
            await self._ensure_plan_impacts_executable(session, user_id, plan.id)
            locked: list[tuple[ImpactMigrationItem, Task | CalendarEvent]] = []
            current_conflicts: list[dict[str, Any]] = []
            for item in items:
                entity = await self._owned_entity(
                    session,
                    user_id,
                    item.entity_type,
                    item.entity_id,
                    for_update=True,
                )
                actual_before = _snapshot(item.entity_type, entity)
                if not _snapshots_match(item.before_snapshot, actual_before, ignore_version=False):
                    raise ConflictError(
                        "entity_version_conflict",
                        "An affected object changed after preview; regenerate the migration",
                        {"entity_id": item.entity_id, "expected_version": item.expected_version},
                    )
                predicted = _patched_snapshot(
                    actual_before, item.proposed_patch, item.source_claim_ids
                )
                if item.entity_type == "event":
                    current_conflicts.extend(
                        await self._event_conflicts(
                            session, user_id, item.entity_id, predicted, for_update=True
                        )
                    )
                locked.append((item, entity))
            if _stable_json(_sorted_conflicts(current_conflicts)) != _stable_json(
                _sorted_conflicts(plan.conflicts_json)
            ):
                raise ConflictError(
                    "calendar_conflicts_changed",
                    "Calendar conflicts changed after preview; regenerate the migration",
                    {
                        "preview_conflicts": plan.conflicts_json,
                        "current_conflicts": current_conflicts,
                    },
                )
            if current_conflicts and not request.allow_conflicts:
                raise ConflictError(
                    "calendar_conflict",
                    "The preview contains calendar conflicts; execution was not performed",
                    {"conflicts": current_conflicts},
                )
            for item, entity in locked:
                _apply_patch(entity, item.proposed_patch, item.source_claim_ids)
                item.after_snapshot = _snapshot(item.entity_type, entity)
            plan.status = "applied"
            plan.executed_at = utc_now()
            plan.version += 1
            impacts = list(
                await session.scalars(
                    select(ImpactCase).where(
                        ImpactCase.user_id == user_id,
                        ImpactCase.migration_plan_id == plan.id,
                    )
                )
            )
            for impact in impacts:
                impact.status = "resolved"
                impact.resolved_at = utc_now()
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        return await self._verify(user_id, plan_id, operation="execute")

    async def undo(
        self,
        session: AsyncSession,
        user_id: str,
        plan_id: str,
        request: MigrationUndoRequest,
    ) -> VerificationReceiptView:
        plan = await self._owned_plan(session, user_id, plan_id)
        if plan.status in {"undo_applied", "undone", "undo_verification_failed"}:
            if plan.undo_idempotency_key != request.idempotency_key:
                raise ConflictError("migration_already_undone", "This migration was already undone")
            if plan.status in {"undo_applied", "undo_verification_failed"}:
                return await self._verify(user_id, plan_id, operation="undo")
            return await self.receipt(user_id, plan_id, operation="undo")
        if plan.status not in {"applied", "verified", "verification_failed"}:
            raise ConflictError(
                "migration_not_undoable",
                "Only an applied or verified migration can be undone as a group",
            )
        if plan.version != request.plan_version:
            raise ConflictError(
                "migration_plan_stale",
                "The migration receipt changed; reload it before undoing",
                {"current_version": plan.version},
            )
        claimed = (
            await session.execute(
                update(ImpactMigrationPlan)
                .where(
                    ImpactMigrationPlan.id == plan.id,
                    ImpactMigrationPlan.user_id == user_id,
                    ImpactMigrationPlan.status.in_(
                        [
                            "applied",
                            "verified",
                            "verification_failed",
                        ]
                    ),
                    ImpactMigrationPlan.version == request.plan_version,
                )
                .values(status="undoing", undo_idempotency_key=request.idempotency_key)
                .returning(ImpactMigrationPlan.id)
            )
        ).scalar_one_or_none()
        if claimed is None:
            await session.rollback()
            fresh = await self._owned_plan(session, user_id, plan_id)
            if fresh.undo_idempotency_key == request.idempotency_key:
                if fresh.status in {"undo_applied", "undo_verification_failed"}:
                    return await self._verify(user_id, plan_id, operation="undo")
                if fresh.undo_receipt_json:
                    return await self.receipt(user_id, plan_id, operation="undo")
            raise ConflictError(
                "migration_undo_conflict", "Another request already claimed this group undo"
            )
        try:
            plan.status = "undoing"
            plan.undo_idempotency_key = request.idempotency_key
            items = list(
                await session.scalars(
                    select(ImpactMigrationItem).where(ImpactMigrationItem.plan_id == plan.id)
                )
            )
            locked: list[tuple[ImpactMigrationItem, Task | CalendarEvent]] = []
            for item in items:
                entity = await self._owned_entity(
                    session,
                    user_id,
                    item.entity_type,
                    item.entity_id,
                    for_update=True,
                )
                after_version = int((item.after_snapshot or {}).get("version", -1))
                if entity.version != after_version:
                    raise ConflictError(
                        "entity_version_conflict",
                        "An affected object changed after migration; group undo was not performed",
                        {"entity_id": item.entity_id, "expected_version": after_version},
                    )
                locked.append((item, entity))
            for item, entity in locked:
                _restore_snapshot(entity, item.before_snapshot)
            plan.status = "undo_applied"
            plan.undone_at = utc_now()
            plan.version += 1
            impacts = list(
                await session.scalars(
                    select(ImpactCase).where(
                        ImpactCase.user_id == user_id,
                        ImpactCase.migration_plan_id == plan.id,
                    )
                )
            )
            for impact in impacts:
                impact.status = "open"
                impact.resolved_at = None
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        return await self._verify(user_id, plan_id, operation="undo")

    async def receipt(
        self,
        user_id: str,
        plan_id: str,
        *,
        operation: Literal["execute", "undo"],
    ) -> VerificationReceiptView:
        async with self._factory() as session:
            plan = await self._owned_plan(session, user_id, plan_id)
            receipt = (
                plan.execute_receipt_json if operation == "execute" else plan.undo_receipt_json
            )
            if not receipt:
                raise DomainError(
                    "migration_receipt_not_available",
                    f"No {operation} receipt exists for this migration",
                    status_code=404,
                    details={"operation": operation},
                )
            items = list(
                await session.scalars(
                    select(ImpactMigrationItem)
                    .where(ImpactMigrationItem.plan_id == plan.id)
                    .order_by(ImpactMigrationItem.entity_type, ImpactMigrationItem.entity_id)
                )
            )
            item_receipts = [
                item.execute_verification_json
                if operation == "execute"
                else item.undo_verification_json
                for item in items
            ]
            item_views = []
            for item, item_receipt in zip(items, item_receipts, strict=True):
                view = _migration_item_view(item)
                item_views.append(view.model_copy(update={"verification": item_receipt}))
            return VerificationReceiptView(
                plan_id=plan.id,
                status=str(receipt["status"]),
                operation=operation,
                verified_count=int(receipt["verified_count"]),
                total_count=int(receipt["total_count"]),
                all_verified=bool(receipt["verified"]),
                items=item_views,
                verified_at=datetime.fromisoformat(str(receipt["verified_at"])),
            )

    async def radar(self, session: AsyncSession, user_id: str, *, limit: int) -> RadarView:
        change_sets = list(
            await session.scalars(
                select(NoticeChangeSet)
                .where(NoticeChangeSet.user_id == user_id)
                .order_by(NoticeChangeSet.created_at.desc())
                .limit(limit * 3)
            )
        )
        cards: list[RadarCardView] = []
        for change_set in change_sets:
            series = await session.get(NoticeSeries, change_set.series_id)
            before = await session.get(Document, change_set.from_document_id)
            after = await session.get(Document, change_set.to_document_id)
            if after is None:
                continue
            applicability = await self._applicability(session, user_id, after.id)
            before_applicability = (
                await self._applicability(session, user_id, before.id)
                if before is not None
                else ApplicabilityResult("not_applicable", "No predecessor document")
            )
            de_scoped = (
                before_applicability.state == "applicable"
                and applicability.state == "not_applicable"
            )
            if applicability.state == "not_applicable" and not de_scoped:
                continue
            changes = list(
                await session.scalars(
                    select(NoticeChangeItem).where(NoticeChangeItem.change_set_id == change_set.id)
                )
            )
            impacts = list(
                await session.scalars(
                    select(ImpactCase)
                    .join(NoticeChangeItem, NoticeChangeItem.id == ImpactCase.change_item_id)
                    .where(
                        ImpactCase.user_id == user_id,
                        NoticeChangeItem.change_set_id == change_set.id,
                    )
                )
            )
            task_count = len({item.entity_id for item in impacts if item.entity_type == "task"})
            event_count = len({item.entity_id for item in impacts if item.entity_type == "event"})
            title = (
                after.title
                if after is not None
                else (series.normalized_title if series else "通知")
            )
            needs_review = (
                de_scoped
                or applicability.state == "needs_review"
                or any(item.review_state == "pending" for item in changes)
            )
            message = (
                f"《{title}》新版不再适用于当前档案；请人工决定保留或取消既有安排。"
                if de_scoped
                else f"《{title}》已更新，影响 {event_count} 个日程和 {task_count} 个待办。"
            )
            cards.append(
                RadarCardView(
                    card_type="needs_review" if needs_review else "version_change",
                    change_set_id=change_set.id,
                    document_id=after.id,
                    series_id=change_set.series_id,
                    title=title,
                    from_revision=(before.revision_number if before else 0) or 0,
                    to_revision=(after.revision_number if after else 0) or 0,
                    change_count=len(changes),
                    affected_tasks=task_count,
                    affected_events=event_count,
                    needs_review=needs_review,
                    applicability=applicability.state,
                    applicability_reason=applicability.reason,
                    message=message,
                    created_at=change_set.created_at,
                )
            )

        current_documents = list(
            await session.scalars(
                select(Document)
                .where(
                    Document.user_id == user_id,
                    Document.series_id.is_not(None),
                    Document.is_current.is_(True),
                )
                .order_by(Document.created_at.desc())
            )
        )
        now = utc_now()
        deadline_horizon = now + timedelta(days=14)
        for document in current_documents:
            if document.series_id is None:
                continue
            applicability = await self._applicability(session, user_id, document.id)
            if applicability.state == "not_applicable":
                continue
            if document.revision_number == 1:
                needs_review = applicability.state == "needs_review"
                cards.append(
                    RadarCardView(
                        card_type="needs_review" if needs_review else "new_notice",
                        change_set_id=None,
                        document_id=document.id,
                        series_id=document.series_id,
                        title=document.title,
                        from_revision=0,
                        to_revision=1,
                        change_count=0,
                        affected_tasks=0,
                        affected_events=0,
                        needs_review=needs_review,
                        applicability=applicability.state,
                        applicability_reason=applicability.reason,
                        message=f"与我有关的新通知：《{document.title}》。",
                        created_at=document.created_at,
                    )
                )
            deadline = await session.scalar(
                select(NoticeClaim)
                .where(
                    NoticeClaim.user_id == user_id,
                    NoticeClaim.document_id == document.id,
                    NoticeClaim.claim_key == "task.due_at",
                )
                .order_by(NoticeClaim.created_at.desc())
            )
            if deadline is None:
                continue
            deadline_at = _as_datetime(deadline.normalized_value_json.get("iso"))
            if deadline_at is None or deadline_at < now or deadline_at > deadline_horizon:
                continue
            needs_review = (
                applicability.state == "needs_review" or deadline.review_state != "approved"
            )
            cards.append(
                RadarCardView(
                    card_type="upcoming_deadline",
                    change_set_id=None,
                    document_id=document.id,
                    series_id=document.series_id,
                    title=document.title,
                    from_revision=0,
                    to_revision=document.revision_number or 1,
                    change_count=0,
                    affected_tasks=0,
                    affected_events=0,
                    needs_review=needs_review,
                    applicability=applicability.state,
                    applicability_reason=applicability.reason,
                    deadline_at=deadline_at,
                    message=f"《{document.title}》即将截止。",
                    created_at=document.created_at,
                )
            )
        cards.sort(key=lambda card: card.created_at, reverse=True)
        return RadarView(items=cards[:limit], total=len(cards))

    async def _ensure_claim_version(
        self,
        session: AsyncSession,
        user_id: str,
        document: Document,
    ) -> None:
        existing_keys = set(
            await session.scalars(
                select(NoticeClaim.claim_key).where(
                    NoticeClaim.user_id == user_id,
                    NoticeClaim.document_id == document.id,
                    NoticeClaim.extractor_version == EXTRACTOR_VERSION,
                )
            )
        )
        if existing_keys:
            return
        chunks = list(
            await session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document.id)
                .order_by(DocumentChunk.ordinal)
            )
        )
        for chunk in chunks:
            for extracted in extract_claims(chunk.content):
                if extracted.key in existing_keys:
                    continue
                session.add(
                    NoticeClaim(
                        user_id=user_id,
                        document_id=document.id,
                        chunk_id=chunk.id,
                        claim_key=extracted.key,
                        claim_type=extracted.claim_type,
                        value_json=extracted.value,
                        normalized_value_json=extracted.normalized,
                        audience_rule_json=extracted.audience,
                        confidence=extracted.confidence,
                        evidence_start=extracted.start,
                        evidence_end=extracted.end,
                        extractor_version=EXTRACTOR_VERSION,
                        review_state=extracted.review_state,
                    )
                )
                existing_keys.add(extracted.key)
        await session.flush()

    async def _create_change_set(
        self,
        session: AsyncSession,
        user_id: str,
        series: NoticeSeries,
        before_document: Document,
        after_document: Document,
    ) -> NoticeChangeSet:
        existing = await session.scalar(
            select(NoticeChangeSet).where(
                NoticeChangeSet.user_id == user_id,
                NoticeChangeSet.from_document_id == before_document.id,
                NoticeChangeSet.to_document_id == after_document.id,
                NoticeChangeSet.algorithm_version == CHANGE_ALGORITHM_VERSION,
            )
        )
        if existing is not None:
            return existing
        change_set = NoticeChangeSet(
            user_id=user_id,
            series_id=series.id,
            from_document_id=before_document.id,
            to_document_id=after_document.id,
            algorithm_version=CHANGE_ALGORITHM_VERSION,
            status="ready",
        )
        session.add(change_set)
        await session.flush()
        before_claims = {
            item.claim_key: item
            for item in await session.scalars(
                select(NoticeClaim).where(
                    NoticeClaim.user_id == user_id,
                    NoticeClaim.document_id == before_document.id,
                    NoticeClaim.extractor_version == EXTRACTOR_VERSION,
                )
            )
        }
        after_claims = {
            item.claim_key: item
            for item in await session.scalars(
                select(NoticeClaim).where(
                    NoticeClaim.user_id == user_id,
                    NoticeClaim.document_id == after_document.id,
                    NoticeClaim.extractor_version == EXTRACTOR_VERSION,
                )
            )
        }
        for key in sorted(before_claims.keys() | after_claims.keys()):
            before = before_claims.get(key)
            after = after_claims.get(key)
            if before is not None and after is not None:
                if _stable_json(before.normalized_value_json) == _stable_json(
                    after.normalized_value_json
                ):
                    continue
                change_type = "changed"
            else:
                change_type = "added" if before is None else "removed"
            confidence = min(
                before.confidence if before is not None else 1.0,
                after.confidence if after is not None else 1.0,
            )
            review_state = (
                "pending"
                if confidence < 0.8
                or (before is not None and before.review_state == "pending")
                or (after is not None and after.review_state == "pending")
                else "approved"
            )
            session.add(
                NoticeChangeItem(
                    user_id=user_id,
                    change_set_id=change_set.id,
                    claim_key=key,
                    change_type=change_type,
                    before_claim_id=before.id if before else None,
                    after_claim_id=after.id if after else None,
                    severity=_change_severity(key, change_type),
                    confidence=confidence,
                    review_state=review_state,
                )
            )
        await session.flush()
        return change_set

    async def _detect_impacts_internal(
        self, session: AsyncSession, user_id: str, change_set: NoticeChangeSet
    ) -> None:
        before_applicability = await self._applicability(
            session, user_id, change_set.from_document_id
        )
        after_applicability = await self._applicability(session, user_id, change_set.to_document_id)
        if before_applicability.state == "not_applicable":
            return
        changes = list(
            await session.scalars(
                select(NoticeChangeItem).where(
                    NoticeChangeItem.change_set_id == change_set.id,
                    NoticeChangeItem.user_id == user_id,
                    NoticeChangeItem.review_state == "approved",
                )
            )
        )
        tasks = list(
            await session.scalars(
                select(Task).where(
                    Task.user_id == user_id,
                    Task.source_document_id == change_set.from_document_id,
                )
            )
        )
        events = list(
            await session.scalars(
                select(CalendarEvent).where(
                    CalendarEvent.user_id == user_id,
                    CalendarEvent.source_document_id == change_set.from_document_id,
                )
            )
        )
        audience_transition = (
            before_applicability.state == "applicable" and after_applicability.state != "applicable"
        )
        for change in changes:
            before = (
                await session.get(NoticeClaim, change.before_claim_id)
                if change.before_claim_id
                else None
            )
            after = (
                await session.get(NoticeClaim, change.after_claim_id)
                if change.after_claim_id
                else None
            )
            for entity_type, entities in (("task", tasks), ("event", events)):
                for entity in entities:
                    depends_on_change = False
                    if before is not None:
                        depends_on_change = _entity_depends_on_claim(entity_type, entity, before)
                    elif after is not None:
                        depends_on_change = _entity_accepts_added_claim(entity_type, entity, after)
                    if change.claim_key == "audience" and audience_transition:
                        depends_on_change = True
                    if not depends_on_change:
                        continue
                    patch = _impact_patch(entity_type, entity, change.claim_key, before, after)
                    recommended_action: Literal["apply", "keep", "cancel", "manual_review"] = (
                        "apply"
                    )
                    requires_manual_review = False
                    if after_applicability.state != "applicable":
                        patch = {}
                        recommended_action = (
                            "cancel"
                            if after_applicability.state == "not_applicable"
                            else "manual_review"
                        )
                        requires_manual_review = True
                    elif after is None:
                        recommended_action = (
                            "cancel"
                            if change.claim_key in {"event.start_at", "event.end_at", "task.due_at"}
                            else "manual_review"
                        )
                        requires_manual_review = True
                    elif not patch:
                        recommended_action = (
                            "keep" if change.claim_key == "audience" else "manual_review"
                        )
                        requires_manual_review = recommended_action == "manual_review"
                    if not patch and recommended_action == "apply":
                        continue
                    existing = await session.scalar(
                        select(ImpactCase).where(
                            ImpactCase.user_id == user_id,
                            ImpactCase.change_item_id == change.id,
                            ImpactCase.entity_type == entity_type,
                            ImpactCase.entity_id == entity.id,
                        )
                    )
                    if existing is not None:
                        if existing.status == "dismissed":
                            existing.status = "open"
                            existing.resolved_at = None
                            existing.migration_plan_id = None
                        if existing.status == "open":
                            existing.entity_version = entity.version
                            existing.reason = _impact_reason(
                                change.claim_key,
                                before_applicability,
                                after_applicability,
                                recommended_action,
                            )
                            existing.severity = change.severity
                            existing.current_snapshot = _snapshot(entity_type, entity)
                            existing.proposed_patch = patch
                            existing.recommended_action = recommended_action
                            existing.requires_manual_review = requires_manual_review
                        continue
                    session.add(
                        ImpactCase(
                            user_id=user_id,
                            change_item_id=change.id,
                            entity_type=entity_type,
                            entity_id=entity.id,
                            entity_version=entity.version,
                            reason=_impact_reason(
                                change.claim_key,
                                before_applicability,
                                after_applicability,
                                recommended_action,
                            ),
                            severity=change.severity,
                            current_snapshot=_snapshot(entity_type, entity),
                            proposed_patch=patch,
                            recommended_action=recommended_action,
                            requires_manual_review=requires_manual_review,
                            status="open",
                        )
                    )
        await session.flush()

    async def _applicability(
        self, session: AsyncSession, user_id: str, document_id: str
    ) -> ApplicabilityResult:
        audience = await session.scalar(
            select(NoticeClaim).where(
                NoticeClaim.user_id == user_id,
                NoticeClaim.document_id == document_id,
                NoticeClaim.claim_key == "audience",
                NoticeClaim.extractor_version == EXTRACTOR_VERSION,
            )
        )
        if audience is None:
            return ApplicabilityResult("applicable", "No audience restriction was extracted")
        if audience.review_state != "approved":
            return ApplicabilityResult("needs_review", "The audience claim has not been approved")
        settings = await session.get(UserSettings, user_id)
        if settings is None:
            return ApplicabilityResult(
                "needs_review", "The user profile needed for audience matching is missing"
            )
        rule = audience.audience_rule_json
        if rule.get("major"):
            if not settings.major:
                return ApplicabilityResult(
                    "needs_review", "The notice names a major but the profile does not"
                )
            if normalize_major(str(rule["major"])) != normalize_major(settings.major):
                return ApplicabilityResult("not_applicable", "The major does not match")
        if rule.get("grade"):
            if not settings.grade:
                return ApplicabilityResult(
                    "needs_review", "The notice names a grade but the profile does not"
                )
            if normalize_grade(str(rule["grade"])) != normalize_grade(settings.grade):
                return ApplicabilityResult("not_applicable", "The grade does not match")
        if rule.get("course"):
            expected = normalize_course(str(rule["course"]))
            courses = {
                normalize_course(str(value))
                for course in settings.current_courses
                for value in (course.get("name"), course.get("code"))
                if value
            }
            if not courses:
                return ApplicabilityResult(
                    "needs_review", "The notice names a course but the profile has none"
                )
            if expected not in courses:
                return ApplicabilityResult("not_applicable", "The course does not match")
        return ApplicabilityResult("applicable", "The explicit audience rule matches")

    async def _is_applicable(self, session: AsyncSession, user_id: str, document_id: str) -> bool:
        return (await self._applicability(session, user_id, document_id)).state == "applicable"

    async def _verify(
        self,
        user_id: str,
        plan_id: str,
        *,
        operation: Literal["execute", "undo"],
    ) -> VerificationReceiptView:
        async with self._factory() as session:
            plan = await self._owned_plan(session, user_id, plan_id)
            existing_receipt = (
                plan.execute_receipt_json if operation == "execute" else plan.undo_receipt_json
            )
            terminal_status = "verified" if operation == "execute" else "undone"
            if existing_receipt and plan.status == terminal_status:
                return await self.receipt(user_id, plan_id, operation=operation)
            items = list(
                await session.scalars(
                    select(ImpactMigrationItem).where(ImpactMigrationItem.plan_id == plan.id)
                )
            )
            results: list[tuple[ImpactMigrationItem, bool, dict[str, Any]]] = []
            for item in items:
                entity = await self._owned_entity(
                    session, user_id, item.entity_type, item.entity_id
                )
                actual = _snapshot(item.entity_type, entity)
                expected = item.after_snapshot if operation == "execute" else item.before_snapshot
                verified = _snapshots_match(
                    expected or {}, actual, ignore_version=operation == "undo"
                )
                results.append((item, verified, actual))
            all_verified = bool(results) and all(result[1] for result in results)
            now = utc_now()
            for item, verified, actual in results:
                expected = item.after_snapshot if operation == "execute" else item.before_snapshot
                item_receipt = {
                    "operation": operation,
                    "verified": verified,
                    "verified_at": now.isoformat(),
                    "expected_snapshot": expected or {},
                    "database_snapshot": actual,
                }
                if not verified:
                    item_receipt["reason"] = "database_snapshot_mismatch"
                item.verification_json = item_receipt
                if operation == "execute":
                    item.execute_verification_json = item_receipt
                else:
                    item.undo_verification_json = item_receipt
            receipt = {
                "operation": operation,
                "verified": all_verified,
                "verified_count": sum(1 for _, verified, _ in results if verified),
                "total_count": len(results),
                "verified_at": now.isoformat(),
            }
            plan.verification_json = receipt
            if operation == "execute":
                plan.status = "verified" if all_verified else "verification_failed"
                receipt["status"] = plan.status
                plan.execute_receipt_json = receipt
            else:
                plan.status = "undone" if all_verified else "undo_verification_failed"
                receipt["status"] = plan.status
                plan.undo_receipt_json = receipt
            await session.commit()
            return await self.receipt(user_id, plan_id, operation=operation)

    async def _event_conflicts(
        self,
        session: AsyncSession,
        user_id: str,
        event_id: str,
        after: dict[str, Any],
        *,
        for_update: bool = False,
    ) -> list[dict[str, Any]]:
        start = _as_datetime(after.get("start_at"))
        end = _as_datetime(after.get("end_at"))
        if start is None or end is None:
            return []
        statement = select(CalendarEvent).where(
            CalendarEvent.user_id == user_id,
            CalendarEvent.id != event_id,
            CalendarEvent.start_at < end,
            CalendarEvent.end_at > start,
        )
        if for_update:
            statement = statement.with_for_update()
        rows = list(await session.scalars(statement))
        return [
            {
                "entity_id": event_id,
                "conflicting_event_id": row.id,
                "title": row.title,
                "start_at": row.start_at.isoformat(),
                "end_at": row.end_at.isoformat(),
            }
            for row in rows
        ]

    async def _ensure_change_reviewed(self, session: AsyncSession, change_set_id: str) -> None:
        pending = int(
            await session.scalar(
                select(func.count(NoticeChangeItem.id)).where(
                    NoticeChangeItem.change_set_id == change_set_id,
                    NoticeChangeItem.review_state == "pending",
                )
            )
            or 0
        )
        if pending:
            raise DomainError(
                "change_review_required",
                "Low-confidence changes must be reviewed before migration preview",
                status_code=422,
                details={"pending_count": pending},
            )

    async def _plan_target_document_id(
        self, session: AsyncSession, plan: ImpactMigrationPlan
    ) -> str:
        change_set = await self._owned_change_set(session, plan.user_id, plan.change_set_id)
        return change_set.to_document_id

    async def _ensure_plan_impacts_executable(
        self,
        session: AsyncSession,
        user_id: str,
        plan_id: str,
    ) -> None:
        rows = list(
            await session.execute(
                select(ImpactCase, NoticeChangeItem)
                .join(NoticeChangeItem, NoticeChangeItem.id == ImpactCase.change_item_id)
                .where(
                    ImpactCase.user_id == user_id,
                    ImpactCase.migration_plan_id == plan_id,
                )
                .with_for_update()
            )
        )
        if not rows:
            raise ConflictError(
                "migration_plan_invalidated",
                "The migration no longer has reviewed impact rows",
            )
        invalid = [
            impact.id
            for impact, change in rows
            if impact.status != "open"
            or impact.recommended_action != "apply"
            or impact.requires_manual_review
            or change.review_state != "approved"
        ]
        if invalid:
            raise ConflictError(
                "migration_plan_invalidated",
                "An impact or its evidence review changed after preview",
                {"impact_ids": invalid},
            )

    async def _series_view(self, session: AsyncSession, series: NoticeSeries) -> NoticeSeriesView:
        count = int(
            await session.scalar(
                select(func.count(Document.id)).where(Document.series_id == series.id)
            )
            or 0
        )
        current = await session.scalar(
            select(Document.id).where(
                Document.series_id == series.id,
                Document.is_current.is_(True),
            )
        )
        return NoticeSeriesView(
            id=series.id,
            canonical_key=series.canonical_key,
            normalized_title=series.normalized_title,
            department=series.department,
            source_key=series.source_key,
            version_count=count,
            current_document_id=current,
            created_at=series.created_at,
            updated_at=series.updated_at,
        )

    async def _version_view(
        self, session: AsyncSession, document: Document, *, include_claims: bool
    ) -> NoticeVersionView:
        claims = await self.claims(session, document.user_id, document.id) if include_claims else []
        if document.series_id is None or document.revision_number is None:
            raise DomainError("invalid_notice_version", "Document is not linked to a notice series")
        return NoticeVersionView(
            id=document.id,
            series_id=document.series_id,
            supersedes_document_id=document.supersedes_document_id,
            revision_number=document.revision_number,
            title=document.title,
            version_label=document.version or str(document.revision_number),
            effective_at=document.effective_at,
            publish_date=document.publish_date,
            is_current=document.is_current,
            ingest_source=document.ingest_source,
            claims=claims,
            created_at=document.created_at,
        )

    async def _claim_view(self, session: AsyncSession, claim: NoticeClaim) -> NoticeClaimView:
        chunk = await session.get(DocumentChunk, claim.chunk_id)
        evidence = "" if chunk is None else chunk.content[claim.evidence_start : claim.evidence_end]
        return NoticeClaimView(
            id=claim.id,
            document_id=claim.document_id,
            chunk_id=claim.chunk_id,
            claim_key=claim.claim_key,
            claim_type=claim.claim_type,
            value=claim.value_json,
            normalized_value=claim.normalized_value_json,
            audience_rule=claim.audience_rule_json,
            confidence=claim.confidence,
            evidence_text=evidence,
            evidence_start=claim.evidence_start,
            evidence_end=claim.evidence_end,
            extractor_version=claim.extractor_version,
            review_state=claim.review_state,
        )

    async def _change_set_view(
        self, session: AsyncSession, change_set: NoticeChangeSet
    ) -> NoticeChangeSetView:
        items = list(
            await session.scalars(
                select(NoticeChangeItem)
                .where(NoticeChangeItem.change_set_id == change_set.id)
                .order_by(NoticeChangeItem.claim_key)
            )
        )
        return NoticeChangeSetView(
            id=change_set.id,
            series_id=change_set.series_id,
            from_document_id=change_set.from_document_id,
            to_document_id=change_set.to_document_id,
            algorithm_version=change_set.algorithm_version,
            status=change_set.status,
            items=[await self._change_item_view(session, item) for item in items],
            created_at=change_set.created_at,
        )

    async def _change_item_view(
        self, session: AsyncSession, item: NoticeChangeItem
    ) -> NoticeChangeItemView:
        before = (
            await session.get(NoticeClaim, item.before_claim_id) if item.before_claim_id else None
        )
        after = await session.get(NoticeClaim, item.after_claim_id) if item.after_claim_id else None
        return NoticeChangeItemView(
            id=item.id,
            claim_key=item.claim_key,
            change_type=item.change_type,
            severity=item.severity,
            confidence=item.confidence,
            review_state=item.review_state,
            before=await self._change_evidence(session, before),
            after=await self._change_evidence(session, after),
        )

    async def _change_evidence(
        self, session: AsyncSession, claim: NoticeClaim | None
    ) -> ChangeEvidenceView | None:
        if claim is None:
            return None
        view = await self._claim_view(session, claim)
        return ChangeEvidenceView(
            claim_id=claim.id,
            document_id=claim.document_id,
            chunk_id=claim.chunk_id,
            value=claim.value_json,
            normalized_value=claim.normalized_value_json,
            evidence_text=view.evidence_text,
            evidence_start=claim.evidence_start,
            evidence_end=claim.evidence_end,
        )

    async def _owned_series(
        self, session: AsyncSession, user_id: str, series_id: str
    ) -> NoticeSeries:
        series = await session.scalar(
            select(NoticeSeries).where(
                NoticeSeries.id == series_id,
                NoticeSeries.user_id == user_id,
            )
        )
        if series is None:
            raise NotFoundError("notice series", series_id)
        return series

    async def _owned_document(
        self, session: AsyncSession, user_id: str, document_id: str
    ) -> Document:
        document = await session.scalar(
            select(Document).where(Document.id == document_id, Document.user_id == user_id)
        )
        if document is None:
            raise NotFoundError("document", document_id)
        return document

    async def _owned_change_set(
        self, session: AsyncSession, user_id: str, change_set_id: str
    ) -> NoticeChangeSet:
        row = await session.scalar(
            select(NoticeChangeSet).where(
                NoticeChangeSet.id == change_set_id,
                NoticeChangeSet.user_id == user_id,
            )
        )
        if row is None:
            raise NotFoundError("notice change set", change_set_id)
        return row

    async def _owned_plan(
        self, session: AsyncSession, user_id: str, plan_id: str
    ) -> ImpactMigrationPlan:
        row = await session.scalar(
            select(ImpactMigrationPlan).where(
                ImpactMigrationPlan.id == plan_id,
                ImpactMigrationPlan.user_id == user_id,
            )
        )
        if row is None:
            raise NotFoundError("impact migration plan", plan_id)
        return row

    async def _owned_entity(
        self,
        session: AsyncSession,
        user_id: str,
        entity_type: str,
        entity_id: str,
        *,
        for_update: bool = False,
    ) -> Task | CalendarEvent:
        if entity_type == "task":
            task_statement = select(Task).where(Task.id == entity_id, Task.user_id == user_id)
            if for_update:
                task_statement = task_statement.with_for_update()
            task = await session.scalar(task_statement)
            if task is None:
                raise NotFoundError(entity_type, entity_id)
            return task
        if entity_type == "event":
            event_statement = select(CalendarEvent).where(
                CalendarEvent.id == entity_id, CalendarEvent.user_id == user_id
            )
            if for_update:
                event_statement = event_statement.with_for_update()
            event = await session.scalar(event_statement)
            if event is None:
                raise NotFoundError(entity_type, entity_id)
            return event
        else:
            raise DomainError(
                "unsupported_impact_entity", "Only task and event impacts are supported"
            )


def _entity_depends_on_claim(
    entity_type: str,
    entity: Task | CalendarEvent,
    claim: NoticeClaim,
) -> bool:
    if entity.source_claim_id == claim.id:
        return True
    key = claim.claim_key
    normalized = claim.normalized_value_json
    if entity_type == "event" and isinstance(entity, CalendarEvent):
        if key == "event.start_at":
            return _same_datetime(entity.start_at, normalized.get("iso"))
        if key == "event.end_at":
            return _same_datetime(entity.end_at, normalized.get("iso"))
        if key == "event.location" and entity.location:
            return normalize_semantic_text(entity.location) == str(normalized.get("text", ""))
        if key == "reminder.minutes":
            return entity.reminder_minutes == int(normalized.get("minutes", -1))
    if entity_type == "task" and isinstance(entity, Task):
        if key == "task.due_at" and entity.due_at is not None:
            return _same_datetime(entity.due_at, normalized.get("iso"))
        if key == "event.start_at" and entity.due_at is not None:
            return _same_datetime(entity.due_at, normalized.get("iso"))
        if (
            key == "reminder.minutes"
            and entity.due_at is not None
            and entity.reminder_at is not None
        ):
            expected = int(normalized.get("minutes", -1))
            actual = int((entity.due_at - entity.reminder_at).total_seconds() // 60)
            return actual == expected
    return False


def _entity_accepts_added_claim(
    entity_type: str,
    entity: Task | CalendarEvent,
    claim: NoticeClaim,
) -> bool:
    if entity_type == "event" and isinstance(entity, CalendarEvent):
        return claim.claim_key == "event.location" and not entity.location
    if entity_type == "task" and isinstance(entity, Task):
        return claim.claim_key == "task.due_at" and entity.due_at is None
    return False


def _primary_claim_rank(entity_type: str, claim: NoticeClaim) -> tuple[int, str, str]:
    priorities = (
        {
            "event.start_at": 0,
            "task.due_at": 1,
            "reminder.minutes": 2,
        }
        if entity_type == "task"
        else {
            "event.start_at": 0,
            "event.end_at": 1,
            "event.location": 2,
            "reminder.minutes": 3,
        }
    )
    return priorities.get(claim.claim_key, 100), claim.claim_key, claim.id


def _impact_reason(
    claim_key: str,
    before: ApplicabilityResult,
    after: ApplicabilityResult,
    action: str,
) -> str:
    if before.state == "applicable" and after.state == "not_applicable":
        return f"{claim_key} changed while the successor notice no longer applies; {action}"
    if after.state == "needs_review":
        return f"{claim_key} changed but successor applicability needs review"
    return f"{claim_key} changed in the explicit successor notice; {action}"


def _same_datetime(current: datetime, expected: Any) -> bool:
    parsed = _as_datetime(expected)
    return parsed is not None and _canonical_value(current) == _canonical_value(parsed)


def _impact_patch(
    entity_type: str,
    entity: Task | CalendarEvent,
    claim_key: str,
    before: NoticeClaim | None,
    after: NoticeClaim | None,
) -> dict[str, Any]:
    if after is None:
        return {}
    if entity_type == "event" and isinstance(entity, CalendarEvent):
        if claim_key in {"event.start_at", "event.end_at"}:
            return {claim_key.split(".", 1)[1]: after.normalized_value_json.get("iso")}
        if claim_key == "event.location":
            return {"location": after.value_json.get("text")}
        if claim_key == "reminder.minutes":
            return {"reminder_minutes": after.normalized_value_json.get("minutes")}
    if entity_type == "task" and isinstance(entity, Task):
        if claim_key == "task.due_at":
            return {"due_at": after.normalized_value_json.get("iso")}
        if claim_key == "reminder.minutes" and entity.due_at:
            minutes = int(after.normalized_value_json.get("minutes", 0))
            return {"reminder_at": (entity.due_at - _minutes(minutes)).isoformat()}
        if claim_key == "event.start_at" and entity.due_at is not None and before is not None:
            old = _as_datetime(before.normalized_value_json.get("iso"))
            new = _as_datetime(after.normalized_value_json.get("iso"))
            if old is not None and new is not None:
                delta = new - old
                patch = {"due_at": (entity.due_at + delta).isoformat()}
                if entity.reminder_at is not None:
                    patch["reminder_at"] = (entity.reminder_at + delta).isoformat()
                return patch
    return {}


def _snapshot(entity_type: str, entity: Task | CalendarEvent) -> dict[str, Any]:
    common: dict[str, Any] = {
        "id": entity.id,
        "title": entity.title,
        "description": entity.description,
        "course_id": entity.course_id,
        "course": entity.course,
        "source_document_id": entity.source_document_id,
        "source_chunk_id": entity.source_chunk_id,
        "source_claim_id": entity.source_claim_id,
        "source_history": list(entity.source_history),
        "version": entity.version,
    }
    if entity_type == "task" and isinstance(entity, Task):
        common.update(
            {
                "due_at": _iso(entity.due_at),
                "reminder_at": _iso(entity.reminder_at),
                "priority": entity.priority.value,
                "status": entity.status.value,
            }
        )
    elif isinstance(entity, CalendarEvent):
        common.update(
            {
                "start_at": entity.start_at.isoformat(),
                "end_at": entity.end_at.isoformat(),
                "location": entity.location,
                "reminder_minutes": entity.reminder_minutes,
            }
        )
    return common


def _patched_snapshot(
    before: dict[str, Any], patch: dict[str, Any], source_claim_ids: list[str]
) -> dict[str, Any]:
    after = dict(before)
    after.update(patch)
    after["version"] = int(before["version"]) + 1
    if source_claim_ids:
        after["source_history"] = _source_history_after_migration(
            before,
            patch,
            source_claim_ids,
            captured_at="migration-preview",
        )
    return after


def _apply_patch(
    entity: Task | CalendarEvent, patch: dict[str, Any], source_claim_ids: list[str]
) -> None:
    source_before = {
        "source_document_id": entity.source_document_id,
        "source_chunk_id": entity.source_chunk_id,
        "source_claim_id": entity.source_claim_id,
        "source_history": list(entity.source_history),
    }
    next_history = _source_history_after_migration(
        source_before,
        patch,
        source_claim_ids,
        captured_at=utc_now().isoformat(),
    )
    for key, value in patch.items():
        if key in {"start_at", "end_at", "due_at", "reminder_at"}:
            setattr(entity, key, _as_datetime(value))
        elif key in {
            "location",
            "reminder_minutes",
            "source_document_id",
            "source_chunk_id",
            "source_claim_id",
        }:
            setattr(entity, key, value)
    if source_claim_ids:
        entity.source_history = next_history
    entity.version += 1


def _source_history_after_migration(
    before: dict[str, Any],
    patch: dict[str, Any],
    source_claim_ids: list[str],
    *,
    captured_at: str,
) -> list[dict[str, Any]]:
    history = list(before.get("source_history", []))
    previous_claim_id = before.get("source_claim_id")
    primary_claim_id = patch.get("source_claim_id", previous_claim_id)
    if primary_claim_id != previous_claim_id and any(
        before.get(key) for key in ("source_document_id", "source_chunk_id", "source_claim_id")
    ):
        history.append(
            {
                "document_id": before.get("source_document_id"),
                "chunk_id": before.get("source_chunk_id"),
                "claim_id": previous_claim_id,
                "role": "superseded_primary",
                "captured_at": captured_at,
            }
        )
    history.extend(
        {
            "claim_id": claim_id,
            "role": "supporting",
            "captured_at": captured_at,
        }
        for claim_id in source_claim_ids
        if claim_id != primary_claim_id
    )
    return history


def _restore_snapshot(entity: Task | CalendarEvent, snapshot: dict[str, Any]) -> None:
    for key in (
        "title",
        "description",
        "course_id",
        "course",
        "source_document_id",
        "source_chunk_id",
        "source_claim_id",
        "source_history",
        "location",
        "reminder_minutes",
    ):
        if key in snapshot and hasattr(entity, key):
            setattr(entity, key, snapshot[key])
    for key in ("start_at", "end_at", "due_at", "reminder_at"):
        if key in snapshot and hasattr(entity, key):
            setattr(entity, key, _as_datetime(snapshot[key]))
    entity.version += 1


def _plan_view(plan: ImpactMigrationPlan, items: list[ImpactMigrationItem]) -> MigrationPlanView:
    return MigrationPlanView(
        id=plan.id,
        change_set_id=plan.change_set_id,
        status=plan.status,
        risk_level=plan.risk_level,
        required_confirmations=_required_confirmations(plan),
        conflicts=plan.conflicts_json,
        items=[_migration_item_view(item) for item in items],
        verification=plan.verification_json,
        execute_receipt=plan.execute_receipt_json,
        undo_receipt=plan.undo_receipt_json,
        generation=plan.generation,
        version=plan.version,
        executed_at=plan.executed_at,
        undone_at=plan.undone_at,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _migration_item_view(item: ImpactMigrationItem) -> MigrationItemView:
    return MigrationItemView(
        id=item.id,
        entity_type=item.entity_type,
        entity_id=item.entity_id,
        expected_version=item.expected_version,
        before=item.before_snapshot,
        after=item.after_snapshot
        or _patched_snapshot(item.before_snapshot, item.proposed_patch, item.source_claim_ids),
        source_claim_ids=item.source_claim_ids,
        verification=item.verification_json,
        execute_verification=item.execute_verification_json,
        undo_verification=item.undo_verification_json,
    )


def _impact_view(item: ImpactCase) -> ImpactCaseView:
    return ImpactCaseView(
        id=item.id,
        change_item_id=item.change_item_id,
        entity_type=item.entity_type,
        entity_id=item.entity_id,
        entity_version=item.entity_version,
        reason=item.reason,
        severity=item.severity,
        current_snapshot=item.current_snapshot,
        proposed_patch=item.proposed_patch,
        recommended_action=item.recommended_action,
        requires_manual_review=item.requires_manual_review,
        status=item.status,
        migration_plan_id=item.migration_plan_id,
        detected_at=item.detected_at,
        resolved_at=item.resolved_at,
    )


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sorted_conflicts(conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(conflicts, key=lambda conflict: _stable_json(_canonical_value(conflict)))


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _change_severity(key: str, change_type: str) -> str:
    if change_type == "removed" or key in {"event.start_at", "event.end_at", "task.due_at"}:
        return "high"
    if key in {"event.location", "audience", "required_materials", "action_requirement"}:
        return "medium"
    return "low"


def _required_confirmations(plan: ImpactMigrationPlan) -> int:
    return 2 if plan.risk_level == "high" or bool(plan.conflicts_json) else 1


def _plan_signature(
    prepared: list[tuple[str, str, dict[str, Any], dict[str, Any], list[str]]],
    conflicts: list[dict[str, Any]],
) -> str:
    return _stable_json({"items": prepared, "conflicts": conflicts})


def _as_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _minutes(value: int) -> Any:
    from datetime import timedelta

    return timedelta(minutes=value)


def _snapshots_match(
    expected: dict[str, Any], actual: dict[str, Any], *, ignore_version: bool
) -> bool:
    keys = set(expected)
    if ignore_version:
        keys.discard("version")
    return all(
        _stable_json(_canonical_value(expected.get(key)))
        == _stable_json(_canonical_value(actual.get(key)))
        for key in keys
    )


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, str) and "T" in value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC).isoformat()
    return value


def _verification_time(plan: ImpactMigrationPlan) -> datetime:
    raw = plan.verification_json.get("verified_at")
    if isinstance(raw, str):
        return datetime.fromisoformat(raw)
    return plan.updated_at
