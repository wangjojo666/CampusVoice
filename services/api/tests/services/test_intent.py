from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.schemas.intent import IntentName, IntentResult
from app.services.intent import IntentParseError, IntentParser


class InvalidThenValidLlm:
    def __init__(self, repaired: str) -> None:
        self.repaired = repaired
        self.repairs = 0

    async def extract(self, text: str, context: Sequence[str]) -> str:
        del text, context
        return "not-json"

    async def repair(self, text: str, invalid_output: str, validation_error: str) -> str:
        del text, invalid_output, validation_error
        self.repairs += 1
        return self.repaired


@pytest.mark.asyncio
async def test_fallback_parses_create_event_and_computes_required_fields() -> None:
    parser = IntentParser()
    result = await parser.parse(
        "把机器学习考试加到日历，7月18日上午九点到十一点",
        now=datetime(2026, 7, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "机器学习考试"
    assert result.slots.date == "2026-07-18"
    assert result.slots.start_time == "09:00"
    assert result.slots.end_time == "11:00"
    assert result.missing_fields == []
    assert result.requires_confirmation is True


@pytest.mark.asyncio
async def test_explicit_create_wins_over_reminder_wording() -> None:
    result = await IntentParser().parse(
        "新建待办：后天下午三点提交人工智能作业，提前一天提醒我。",
        now=datetime(2026, 7, 14, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "提交人工智能作业"
    assert result.slots.due_date == "2026-07-16"
    assert result.slots.due_time == "15:00"
    assert result.slots.reminder_minutes == 1440
    assert result.missing_fields == []
    assert result.requires_confirmation is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reminder_text", "expected_minutes"),
    [
        ("提前半小时提醒我", 30),
        ("提前两个小时提醒我", 120),
        ("提前12小时通知我", 720),
    ],
)
async def test_fallback_extracts_reminder_without_polluting_event_title_or_time(
    reminder_text: str,
    expected_minutes: int,
) -> None:
    result = await IntentParser().parse(
        f"创建日程：明天下午三点项目组会，{reminder_text}。",
        now=datetime(2026, 7, 14, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "项目组会"
    assert result.slots.date == "2026-07-15"
    assert result.slots.start_time == "15:00"
    assert result.slots.end_time is None
    assert result.slots.reminder_minutes == expected_minutes
    assert result.missing_fields == []


@pytest.mark.asyncio
async def test_deterministic_enrichment_cleans_reminder_from_llm_title() -> None:
    llm = InvalidThenValidLlm(
        """{
          "intent":"create_event",
          "confidence":0.9,
          "slots":{"title":"项目组会，提前半小时提醒我"},
          "missing_fields":[],
          "ambiguities":[],
          "source_text":"wrong",
          "requires_confirmation":false
        }"""
    )

    result = await IntentParser(llm).parse(
        "创建日程：明天下午三点项目组会，提前半小时提醒我。",
        now=datetime(2026, 7, 14, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "项目组会"
    assert result.slots.start_time == "15:00"
    assert result.slots.end_time is None
    assert result.slots.reminder_minutes == 30
    assert result.missing_fields == []


@pytest.mark.asyncio
async def test_fallback_returns_unknown_without_create_signal() -> None:
    result = await IntentParser().parse("机器学习是什么")

    assert result.intent == IntentName.UNKNOWN
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_title"),
    [
        ("把机器学习作业优先级改为高", IntentName.UPDATE_TASK, "机器学习作业"),
        ("删除待办机器学习作业", IntentName.DELETE_TASK, "机器学习作业"),
        ("把项目组会改到明天下午三点", IntentName.UPDATE_EVENT, "项目组会"),
        ("删除日程项目答辩", IntentName.DELETE_EVENT, "项目答辩"),
        ("查询奖学金报名通知", IntentName.SEARCH_NOTICE, None),
        ("查看明天的日程", IntentName.QUERY_SCHEDULE, None),
    ],
)
async def test_fallback_covers_all_non_create_intents(
    text: str,
    expected_intent: IntentName,
    expected_title: str | None,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 12, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == expected_intent
    assert result.slots.title == expected_title
    expected_confirmation = expected_intent in {
        IntentName.UPDATE_TASK,
        IntentName.DELETE_TASK,
        IntentName.UPDATE_EVENT,
        IntentName.DELETE_EVENT,
    }
    assert result.requires_confirmation is expected_confirmation


@pytest.mark.asyncio
async def test_fallback_uses_prior_context_for_a_short_clarification() -> None:
    parser = IntentParser()
    now = datetime(2026, 7, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
    first = await parser.parse("创建日程：项目答辩", now=now)
    completed = await parser.parse("明天下午三点", context=[first.source_text], now=now)

    assert first.missing_fields == ["date", "start_time"]
    assert completed.intent == IntentName.CREATE_EVENT
    assert completed.slots.title == "项目答辩"
    assert completed.slots.date == "2026-07-13"
    assert completed.slots.start_time == "15:00"
    assert completed.missing_fields == []


@pytest.mark.asyncio
async def test_per_call_timezone_controls_relative_date_and_tonight_time() -> None:
    parser = IntentParser(timezone_name="Asia/Shanghai")
    instant = datetime(2026, 7, 13, 1, 0, tzinfo=ZoneInfo("UTC"))

    tomorrow = await parser.parse(
        "创建日程：项目组会，明天下午三点",
        now=instant,
        timezone_name="America/Los_Angeles",
    )
    tonight = await parser.parse(
        "创建日程：夜间复习，今晚七点",
        now=instant,
        timezone_name="America/Los_Angeles",
    )

    assert tomorrow.slots.date == "2026-07-13"
    assert tomorrow.slots.start_time == "15:00"
    assert tonight.slots.date == "2026-07-12"
    assert tonight.slots.start_time == "19:00"


@pytest.mark.asyncio
async def test_user_timezone_deterministically_overrides_llm_relative_date_guess() -> None:
    repaired = """{
      "intent":"create_event",
      "confidence":0.9,
      "slots":{"title":"夜间复习","date":"2099-01-01","start_time":"07:00"},
      "missing_fields":[],
      "ambiguities":[],
      "source_text":"wrong",
      "requires_confirmation":false
    }"""
    instant = datetime(2026, 7, 13, 1, 0, tzinfo=ZoneInfo("UTC"))

    result = await IntentParser(InvalidThenValidLlm(repaired)).parse(
        "创建日程：夜间复习，今晚七点",
        now=instant,
        timezone_name="America/Los_Angeles",
    )

    assert result.slots.date == "2026-07-12"
    assert result.slots.start_time == "19:00"


@pytest.mark.asyncio
async def test_llm_gets_exactly_one_structured_repair_and_policy_is_deterministic() -> None:
    repaired = """{
      "intent":"create_event",
      "confidence":0.9,
      "slots":{"title":"答辩"},
      "missing_fields":[],
      "ambiguities":[],
      "source_text":"wrong",
      "requires_confirmation":false
    }"""
    llm = InvalidThenValidLlm(repaired)
    result = await IntentParser(llm).parse("创建答辩日程")

    assert llm.repairs == 1
    assert result.source_text == "创建答辩日程"
    assert result.missing_fields == ["date", "start_time"]
    assert result.requires_confirmation is True


@pytest.mark.asyncio
async def test_invalid_repair_fails_closed() -> None:
    llm = InvalidThenValidLlm('{"intent":"create_event","unexpected":true}')

    with pytest.raises(IntentParseError) as error:
        await IntentParser(llm).parse("创建日程")

    assert llm.repairs == 1
    assert error.value.code == "invalid_model_output"


def test_intent_schema_forbids_unknown_fields() -> None:
    with pytest.raises(ValueError):
        IntentResult.model_validate(
            {
                "intent": "unknown",
                "confidence": 0.1,
                "slots": {"invented": "value"},
                "missing_fields": [],
                "ambiguities": [],
                "source_text": "test",
                "requires_confirmation": False,
            }
        )
