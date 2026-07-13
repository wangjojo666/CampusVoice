from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.session import create_database_engine, create_session_factory
from app.models.entities import CalendarEvent, Hotword, Task, User
from app.models.enums import HotwordCategory, SourceType, TaskPriority, TaskStatus
from app.services.verification.service import VerificationService


@pytest.fixture
async def verification_session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    engine = create_database_engine(f"sqlite+aiosqlite:///{tmp_path / 'verification-service.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = create_session_factory(engine)
    async with factory() as session:
        session.add(User(id="verification-user", display_name="Verification User"))
        await session.commit()
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_verify_task_covers_presence_absence_mismatch_and_duplicate(
    verification_session: AsyncSession,
) -> None:
    session = verification_session
    first = Task(
        user_id="verification-user",
        title="Database assignment",
        course="Databases",
        due_at=datetime(2026, 7, 20, 8, tzinfo=UTC),
        priority=TaskPriority.HIGH,
        status=TaskStatus.PENDING,
        source_type=SourceType.MANUAL,
    )
    duplicate = Task(
        user_id="verification-user",
        title="Database assignment",
        course="Databases",
        due_at=datetime(2026, 7, 20, 8, 3, tzinfo=UTC),
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.PENDING,
        source_type=SourceType.MANUAL,
    )
    session.add_all([first, duplicate])
    await session.commit()
    first_id = first.id

    verifier = VerificationService()
    duplicate_report = await verifier.verify_task(
        session,
        "verification-user",
        first_id,
        {
            "title": "Database assignment",
            "priority": "high",
            "due_at": "2026-07-20T08:00:00Z",
        },
    )
    assert duplicate_report.success is False
    assert duplicate_report.verified_fields == {
        "title": True,
        "priority": True,
        "due_at": True,
    }
    assert duplicate_report.side_effects == ("duplicate_task_created",)
    assert duplicate_report.record is not None
    assert duplicate_report.as_dict()["side_effects"] == ["duplicate_task_created"]

    mismatch = await verifier.verify_task(
        session,
        "verification-user",
        first_id,
        {"title": "A different assignment"},
    )
    assert mismatch.success is False
    assert mismatch.verified_fields == {"title": False}

    missing = await verifier.verify_task(
        session,
        "verification-user",
        "missing-task",
        {"title": "Missing"},
    )
    assert missing.success is False
    assert missing.verified_fields == {"title": False}

    absent = await verifier.verify_task(
        session,
        "verification-user",
        "missing-task",
        {},
        should_exist=False,
    )
    assert absent.success is True
    assert absent.verified_fields == {"absent": True}

    unexpectedly_present = await verifier.verify_task(
        session,
        "verification-user",
        first_id,
        {},
        should_exist=False,
    )
    assert unexpectedly_present.success is False
    assert unexpectedly_present.verified_fields == {"absent": False}


@pytest.mark.asyncio
async def test_verify_event_reports_duplicates_conflicts_and_override(
    verification_session: AsyncSession,
) -> None:
    session = verification_session
    start = datetime(2026, 7, 21, 9, tzinfo=UTC)
    primary = CalendarEvent(
        user_id="verification-user",
        title="Project review",
        start_at=start,
        end_at=start + timedelta(hours=1),
        location="Room 301",
        source_type=SourceType.MANUAL,
    )
    duplicate = CalendarEvent(
        user_id="verification-user",
        title="Project review",
        start_at=start,
        end_at=start + timedelta(hours=1),
        location="Room 301",
        source_type=SourceType.MANUAL,
    )
    overlap = CalendarEvent(
        user_id="verification-user",
        title="Office hour",
        start_at=start + timedelta(minutes=30),
        end_at=start + timedelta(hours=2),
        location="Room 302",
        source_type=SourceType.MANUAL,
    )
    session.add_all([primary, duplicate, overlap])
    await session.commit()
    primary_id = primary.id
    overlap_id = overlap.id

    verifier = VerificationService()
    report = await verifier.verify_event(
        session,
        "verification-user",
        primary_id,
        {"title": "Project review", "start_at": "2026-07-21T09:00:00+00:00"},
    )
    assert report.success is False
    assert report.verified_fields == {"title": True, "start_at": True}
    assert report.side_effects == ("duplicate_event_created", "time_conflict")

    conflict_rejected = await verifier.verify_event(
        session,
        "verification-user",
        overlap_id,
        {"title": "Office hour"},
    )
    assert conflict_rejected.success is False
    assert conflict_rejected.side_effects == ("time_conflict",)

    conflict_allowed = await verifier.verify_event(
        session,
        "verification-user",
        overlap_id,
        {"title": "Office hour"},
        allow_conflict=True,
    )
    assert conflict_allowed.success is True
    assert conflict_allowed.side_effects == ("time_conflict",)

    missing = await verifier.verify_event(
        session,
        "verification-user",
        "missing-event",
        {"title": "Missing"},
    )
    assert missing.success is False
    assert missing.verified_fields == {"title": False}

    absent = await verifier.verify_event(
        session,
        "verification-user",
        "missing-event",
        {},
        should_exist=False,
    )
    assert absent.success is True

    present = await verifier.verify_event(
        session,
        "verification-user",
        primary_id,
        {},
        should_exist=False,
    )
    assert present.success is False
    assert present.verified_fields == {"absent": False}


@pytest.mark.asyncio
async def test_verify_hotword_covers_success_mismatch_missing_and_absence(
    verification_session: AsyncSession,
) -> None:
    session = verification_session
    hotword = Hotword(
        user_id="verification-user",
        term="Transformer",
        category=HotwordCategory.AI_TERM,
        source="user",
        weight=1.5,
        is_active=True,
    )
    session.add(hotword)
    await session.commit()
    hotword_id = hotword.id

    verifier = VerificationService()
    matched = await verifier.verify_hotword(
        session,
        "verification-user",
        hotword_id,
        {"term": "Transformer", "category": "ai_term", "is_active": True},
    )
    assert matched.success is True
    assert all(matched.verified_fields.values())
    assert matched.record is not None

    mismatch = await verifier.verify_hotword(
        session,
        "verification-user",
        hotword_id,
        {"weight": 2.0},
    )
    assert mismatch.success is False
    assert mismatch.verified_fields == {"weight": False}

    missing = await verifier.verify_hotword(
        session,
        "verification-user",
        "missing-hotword",
        {"term": "Missing"},
    )
    assert missing.success is False
    assert missing.verified_fields == {"term": False}

    absent = await verifier.verify_hotword(
        session,
        "verification-user",
        "missing-hotword",
        {},
        should_exist=False,
    )
    assert absent.success is True

    present = await verifier.verify_hotword(
        session,
        "verification-user",
        hotword_id,
        {},
        should_exist=False,
    )
    assert present.success is False
    assert present.verified_fields == {"absent": False}
