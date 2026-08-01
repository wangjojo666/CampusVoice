from collections.abc import Sequence
from datetime import datetime
from time import perf_counter
from zoneinfo import ZoneInfo

import pytest

from app.schemas.intent import IntentName, IntentResult
from app.services.intent import IntentParseError, IntentParser
from app.services.intent.parser import (
    _classify_intent,
    _has_abort_prefix_before_mutation,
    _has_ambiguous_update_target,
    _has_contextual_ambiguous_target,
    _strip_outer_quoted_literal,
    _strip_update_trailing_connectors,
    _without_reminder_phrases,
)


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


class RecordingMutationLlm:
    def __init__(self) -> None:
        self.extract_calls = 0
        self.repair_calls = 0

    async def extract(self, text: str, context: Sequence[str]) -> str:
        del text, context
        self.extract_calls += 1
        return """{
          "intent":"delete_task",
          "confidence":0.99,
          "slots":{"title":"论文草稿"},
          "missing_fields":[],
          "ambiguities":[],
          "source_text":"不要删除任务：论文草稿",
          "requires_confirmation":true
        }"""

    async def repair(self, text: str, invalid_output: str, validation_error: str) -> str:
        del text, invalid_output, validation_error
        self.repair_calls += 1
        return "{}"


class StaticMutationLlm:
    def __init__(self, response: str) -> None:
        self.response = response
        self.extract_calls = 0
        self.repair_calls = 0

    async def extract(self, text: str, context: Sequence[str]) -> str:
        del text, context
        self.extract_calls += 1
        return self.response

    async def repair(self, text: str, invalid_output: str, validation_error: str) -> str:
        del text, invalid_output, validation_error
        self.repair_calls += 1
        return "{}"


_CONFLICTING_COMMAND_TEXTS = (
    "创建日程和待办：准备考试",
    "创建日程，添加待办: 准备考试",
    "删除任务A并创建待办B",
    "删除任务A并创建待办B，提前一天提醒我",
    "删除任务A，创建待办B",
    "删除任务A, 创建待办B",
    "删除任务A。创建待办B",
    "删除任务A；创建待办B",
    "删除任务A; 创建待办B",
    "删除任务A\n创建待办B",
    "查看任务，删除旧任务",
    "删除任务A并删除任务B",
    "删除任务A，然后删除任务B",
    "删除任务A再删除任务B",
    "删除任务A同时删除任务B",
    "先删除任务A再删除任务B",
    "请先删除任务A，然后删除任务B",
    "先创建待办A再创建待办B",
    "先更新任务A再更新任务B",
    "删除任务A和删除任务B",
    "删除任务A以及删除任务B",
    "删除任务A、删除任务B",
    "删除任务A与删除任务B",
    "分别删除任务A和任务B",
    "分别创建任务A和任务B",
    "创建日程明天15:00，然后删除任务B",
    "创建待办：准备考试。删除任务B",
    "创建待办：准备考试，删除任务B",
    "删除任务：A然后删除任务B",
    "删除任务：A和删除任务B",
    "创建待办：A然后创建待办B",
    "创建待办：准备考试并删除任务B",
    "创建待办：准备考试再删除任务B",
    "创建待办：准备考试以及删除任务B",
    "删除任务A和任务B",
    "删除任务A、任务B",
    "创建任务A与任务B",
    "更新任务A以及任务B",
    "删除任务A之后删除任务B",
    "删除任务A接下来删除任务B",
    "删除任务A又删除任务B",
    "删除任务A且删除任务B",
    "创建待办：准备考试然后把任务B删了",
    "创建待办：准备考试然后任务B删了",
    "创建待办：准备考试然后删除它",
    "创建待办：准备考试然后删除旧作业",
    "创建待办：准备考试然后取消组会",
    "创建待办：准备考试然后把旧作业删掉",
    "删除两个任务",
    "删除所有任务",
    "删除任务A随即删除任务B",
    "删除任务A然后任务B给移除了",
    "删除任务A然后任务B取消掉",
    "删除任务A然后顺手把任务B删了",
    "删除任务A然后也把任务B删了",
    "完成任务A随之完成任务B",
    "标记任务A继而标记任务B",
    "改名任务A继而改名任务B",
    "删除三个任务",
    "删除5个任务",
    "删除每个任务",
    "删除剩余任务",
    "删除上述任务",
    "删除任务A、B",
    "创建待办：准备考试然后删除教程任务",
    "创建待办：准备考试然后删除示例任务",
    "创建待办：准备考试顺便删除任务B",
    "创建待办：准备考试最后删除任务B",
    "创建待办：准备考试后删除任务B",
    "创建待办：A然后删除任务教程",
    "删除任务：A然后删除任务示例",
    "创建待办：A，然后删除任务Python的教程",
    "删除一个任务",
    "删除1个任务",
    "删除十一个任务",
    "删除二十一个任务",
    "删除两项任务",
    "删除0个任务",
    "删除01个任务",
    "删除几个任务",
    "删除几项任务",
    "删除各项任务",
    "删除每一项任务",
    "删除任意一个任务",
    "删除大部分任务",
    "删除两份作业",
    "删除2份作业",
    "删除两场会议",
    "删除2场会议",
    "删除三次考试",
    "删除2次考试",
    "删除五节课程",
    "删除2节课程",
    "删除任务A或者任务B",
    "删除任务A或任务B",
    "删除任务A跟任务B",
    "删除任务A及任务B",
    "删除任务A还有任务B",
    "删除日程A或者日程B",
    "删除日程A及日程B",
    "删除任务A/任务B",
    "删除任务A＆任务B",
    "删除任务A&任务B",
    "删除任务A+任务B",
    "创建两场会议",
    "创建三个任务",
    "创建2份作业",
)

_NEGATED_MUTATION_TEXTS = (
    "不要创建待办：整理资料",
    "不用更新任务: 论文草稿",
    "请别再删除任务：旧作业",
    "无需创建日程：班会",
    "不必更新事件：项目复盘",
    "不要删除日历事件：答辩",
    "不要标记任务完成",
    "不需要删除任务：旧材料",
    "不要将这个任务删除",
    "请勿删除任务：旧作业",
    "不要把旧任务从列表中删除",
    "别把这条旧任务删除",
    "禁止删除任务",
    "不能马上删除任务",
    "不可以把事件删除",
    "我没有删除任务",
    "任务还没完成",
    "尚未完成任务",
    "不\u200b要删除任务",
    "不删除任务",
    "不再删除任务",
    "我不想删除任务",
    "别删任务",
    "别改任务",
    "不要把这是一个超过三十二个字符而且包含很多描述信息的旧任务从当前列表中彻底删除",
    "不要把任务「旧作业，第二版」删除",
)

_QUERY_MUTATION_CONFLICT_TEXTS = (
    "查看已完成日程",
    "查询已完成任务",
    "查看标记为完成的待办",
    "查看删除的事件",
    "搜索调整后的日程",
    "查一下取消的任务",
    "列出已完成任务",
    "想知道已完成日程",
    "给我看看取消的事件",
    "帮我找找已删除任务",
    "我想看调整后的日程",
    "显示取消的事件",
    "看一下已完成任务",
    "列一下已完成任务",
    "我想了解已删除的任务",
    "请告诉我已完成的日程",
)

_NARRATIVE_MUTATION_TEXTS = (
    "我昨天删除了任务",
    "任务已经完成",
    "老师取消了日程",
    "删除任务是老师做的",
    "更新任务只是一个例子",
    "取消日程是系统记录",
    "删除任务了吗",
    "删除任务由老师负责",
    "更新任务属于系统行为",
    "删除任务为什么发生？",
    "删除任务何时发生？",
    "删除任务是否合理？",
    "删除任务能否执行？",
    "删除任务是什么意思？",
    "删除任务的原因",
    "我刚把任务B删了",
    "老师刚把任务B删了",
    "我刚改了任务B",
    "任务由老师删除",
    "任务被老师删除",
    "任务让老师删除了",
    "任务昨天被删除",
    "任务已经被删除",
    "任务A删除了",
    "任务A被删除了",
    "任务A给删除了",
    "任务A删除失败",
    "任务A不能删除",
    "任务A不要删除",
    "这个任务别删除",
    "任务A无需删除",
    "任务A已经删除了",
    "删除任务A怎么操作",
    "删除任务A要怎么做",
    "删除任务A怎么弄？",
    "删除任务A的方法",
    "删除任务A的步骤",
    "删除任务A该如何恢复",
    "删除任务A会怎样",
)


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
    ("text", "expected_intent", "expected_title"),
    [
        (
            "创建待办：明天下午四点整理日程安排。",
            IntentName.CREATE_TASK,
            "整理日程安排",
        ),
        (
            "创建待办: 明天下午四点整理日程安排。",
            IntentName.CREATE_TASK,
            "整理日程安排",
        ),
        (
            "创建任务：明天下午四点记录异常事件。",
            IntentName.CREATE_TASK,
            "记录异常事件",
        ),
        ("创建待办：读书，删除算法笔记", IntentName.CREATE_TASK, "读书,删除算法笔记"),
        ("创建待办：读书，分析删除算法", IntentName.CREATE_TASK, "读书,分析删除算法"),
        ("创建待办：阅读删除算法笔记", IntentName.CREATE_TASK, "阅读删除算法笔记"),
        ("创建待办：准备把论文交给老师", IntentName.CREATE_TASK, "准备把论文交给老师"),
        (
            "创建待办：研究创建任务和删除任务的区别",
            IntentName.CREATE_TASK,
            "研究创建任务和删除任务的区别",
        ),
        (
            "创建待办：学习创建任务和删除任务",
            IntentName.CREATE_TASK,
            "学习创建任务和删除任务",
        ),
        (
            "创建待办：讨论创建任务与删除任务",
            IntentName.CREATE_TASK,
            "讨论创建任务与删除任务",
        ),
        (
            "创建待办：整理删除任务和创建任务文档",
            IntentName.CREATE_TASK,
            "整理删除任务和创建任务文档",
        ),
        (
            "创建待办：创建任务和删除任务的区别",
            IntentName.CREATE_TASK,
            "创建任务和删除任务的区别",
        ),
        ("创建任务复习数学和英语", IntentName.CREATE_TASK, "复习数学和英语"),
        ("创建任务阅读战争与和平", IntentName.CREATE_TASK, "阅读战争与和平"),
        ("创建待办买牛奶和面包", IntentName.CREATE_TASK, "买牛奶和面包"),
        ("创建任务学习所有课程", IntentName.CREATE_TASK, "学习所有课程"),
        ("创建任务整理两个任务的区别", IntentName.CREATE_TASK, "整理两个任务的区别"),
        ("创建任务复习三个考试章节", IntentName.CREATE_TASK, "复习三个考试章节"),
        ("创建一个任务：复习数学", IntentName.CREATE_TASK, "复习数学"),
        ("更新待办：日程复盘", IntentName.UPDATE_TASK, "日程复盘"),
        ("更新任务：课程任务，优先级改为高", IntentName.UPDATE_TASK, "课程任务"),
        (
            "更新任务：2026-07-30计划，优先级改为高",
            IntentName.UPDATE_TASK,
            "2026-07-30计划",
        ),
        ("删除任务: 日历复盘", IntentName.DELETE_TASK, "日历复盘"),
        ("删除任务：C++学习任务", IntentName.DELETE_TASK, "C++学习任务"),
        ("删除任务：学习任务", IntentName.DELETE_TASK, "学习任务"),
        ("删除任务：阅读《明天》", IntentName.DELETE_TASK, "阅读《明天》"),
        ("删除任务：看电影《后天》", IntentName.DELETE_TASK, "看电影《后天》"),
        ("删除任务：7月30日计划", IntentName.DELETE_TASK, "7月30日计划"),
        (
            "创建日程：明天下午四点安排任务复盘。",
            IntentName.CREATE_EVENT,
            "安排任务复盘",
        ),
        (
            "创建日历事件: 明天下午四点清理待办清单。",
            IntentName.CREATE_EVENT,
            "清理待办清单",
        ),
        ("更新日程：任务复盘", IntentName.UPDATE_EVENT, "任务复盘"),
        ("删除事件: 待办复盘", IntentName.DELETE_EVENT, "待办复盘"),
    ],
)
async def test_explicit_target_scope_ignores_title_keywords(
    text: str,
    expected_intent: IntentName,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == expected_intent
    assert result.slots.title == expected_title
    assert result.requires_confirmation is True


@pytest.mark.asyncio
async def test_explicit_target_date_literal_does_not_fill_due_date() -> None:
    result = await IntentParser().parse(
        "更新任务：2026-07-30计划，优先级改为高",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == "2026-07-30计划"
    assert result.slots.due_date is None
    assert result.slots.priority == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "title"),
    [
        ("创建待办：阅读《明天》", IntentName.CREATE_TASK, "阅读《明天》"),
        (
            "创建日程：讨论“今天”这个概念",
            IntentName.CREATE_EVENT,
            "讨论“今天”这个概念",
        ),
    ],
)
async def test_create_title_quoted_date_literal_is_not_a_temporal_slot(
    text: str,
    intent: IntentName,
    title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == intent
    assert result.slots.title == title
    assert result.slots.due_date is None
    assert result.slots.date is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "title", "priority"),
    [
        (
            "删除任务：研究“提前一天提醒我”的语义",
            IntentName.DELETE_TASK,
            "研究“提前一天提醒我”的语义",
            None,
        ),
        (
            "更新任务：研究“提前一天提醒我”的语义，优先级改为高",
            IntentName.UPDATE_TASK,
            "研究“提前一天提醒我”的语义",
            "high",
        ),
        (
            "删除任务：提前一天提醒我",
            IntentName.DELETE_TASK,
            "提前一天提醒我",
            None,
        ),
        (
            "删除任务：项目组会，提前半小时提醒我",
            IntentName.DELETE_TASK,
            "项目组会,提前半小时提醒我",
            None,
        ),
    ],
)
async def test_explicit_mutation_target_preserves_reminder_literal(
    text: str,
    intent: IntentName,
    title: str,
    priority: str | None,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == intent
    assert result.slots.title == title
    assert result.slots.priority == priority
    assert result.slots.reminder_minutes is None
    assert result.missing_fields == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "title"),
    [
        (
            "创建待办：研究“提前一天提醒我”的语义",
            IntentName.CREATE_TASK,
            "研究“提前一天提醒我”的语义",
        ),
        (
            "创建待办：学习明天和后天的区别",
            IntentName.CREATE_TASK,
            "学习明天和后天的区别",
        ),
        (
            "创建日程：研究明天下午三点的含义",
            IntentName.CREATE_EVENT,
            "研究明天下午三点的含义",
        ),
        (
            "创建待办：整理提前一天提醒我的教程",
            IntentName.CREATE_TASK,
            "整理提前一天提醒我的教程",
        ),
    ],
)
async def test_create_meta_title_preserves_temporal_literal(
    text: str,
    intent: IntentName,
    title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == intent
    assert result.slots.title == title
    assert result.slots.due_date is None
    assert result.slots.due_time is None
    assert result.slots.date is None
    assert result.slots.start_time is None
    assert result.slots.reminder_minutes is None


@pytest.mark.asyncio
async def test_create_meta_title_allows_trailing_reminder_modifier() -> None:
    result = await IntentParser().parse(
        "创建待办：研究明天的含义，提前一天提醒我",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "研究明天的含义"
    assert result.slots.due_date is None
    assert result.slots.reminder_minutes == 1440


@pytest.mark.asyncio
@pytest.mark.parametrize("modifier", ["截止", "开始"])
async def test_create_meta_title_drops_trailing_temporal_modifier_word(
    modifier: str,
) -> None:
    result = await IntentParser().parse(
        f"创建待办：学习“明天”的含义，后天下午三点{modifier}",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "学习“明天”的含义"
    assert result.slots.due_date == "2026-07-30"
    assert result.slots.due_time == "15:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "删除任务A|任务B",
        "删除任务A｜任务B",
        "删除任务A·任务B",
        "删除任务A•任务B",
        "创建任务A|任务B",
    ],
)
async def test_symbol_separated_multiple_targets_fail_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(text)

    assert llm.extract_calls == 0
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.requires_confirmation is False


@pytest.mark.asyncio
async def test_non_meta_create_title_still_extracts_temporal_slots() -> None:
    result = await IntentParser().parse(
        "创建待办：学习数学明天下午三点截止",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.due_date == "2026-07-29"
    assert result.slots.due_time == "15:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建日程：明天和后天下午三点组会",
        "创建日程：明天下午三点和后天下午四点组会",
        "创建日程：明天到后天下午三点组会",
    ],
)
async def test_ambiguous_multi_date_create_fails_closed_without_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 0
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建日程：明天下午三点到四点组会",
        "创建日程：明天下午三点至四点组会",
        "创建日程：明天15:00-16:00组会",
        "创建日程：明天下午3-4点组会",
    ],
)
async def test_same_day_time_range_does_not_pollute_title(text: str) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "组会"
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == "15:00"
    assert result.slots.end_time == "16:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建日程：明天晚上十一点到一点夜间维护",
        "创建日程：明天下午三点到上午四点培训",
        "创建日程：明天23:00-01:00值班",
        "创建日程：明天晚上11点到凌晨1点值班",
    ],
)
async def test_non_increasing_time_range_fails_closed_without_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 0
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.requires_confirmation is False


_INVALID_DIRECT_TEMPORAL_MUTATIONS = (
    "创建待办：2026年2月30日交作业",
    "创建日程：112月1日下午三点开会",
    "创建日程：明天下午25点到四点开会",
    "创建待办：明天三点60分交作业",
    "创建日程：明天123:45开会",
    "创建日程：明天12:345开会",
    "创建日程：明天100点开会",
    "创建日程：明天15:开会",
    "创建日程：明天三点到四点到五点开会",
    "创建日程：明天三点和四点开会",
    "创建日程：明天三点、四点开会",
    "创建日程：明天三点四点开会",
    "创建日程：明天三点到四",
    "创建待办：明天交作业，提前366天提醒我",
    "创建待办：明天交作业，提前9999天提醒我",
    "创建待办：明天交作业，提前99999天提醒我",
    "创建待办：明天交作业，提前一天提醒我，提前两天提醒我",
    "创建日程：明天凌晨13点开会",
    "创建日程：明天上午15点开会",
    "创建日程：明天下午0点开会",
    "创建日程：明天晚上0点开会",
    "创建日程：明天中午0点开会",
    "创建日程：明天晚上12点开会",
    "创建日程：今晚12点开会",
    "创建日程：明天午夜12点开会",
    "创建日程：明天半夜12点开会",
    "创建日程：明天下午15:00-16:00组会",
    "把任务A优先级改为高，截止日期改到2026-02-30",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _INVALID_DIRECT_TEMPORAL_MUTATIONS)
async def test_invalid_mutation_temporal_metadata_fails_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 0
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "2026年2月30日下午三点",
        "112月1日下午三点",
        "明天下午25点到四点",
        "明天三点60分",
        "明天123:45",
        "明天12:345",
        "明天100点",
        "明天三点到四点到五点",
        "明天三点和四点",
        "明天三点、四点",
        "明天三点四点",
        "提前366天提醒我",
        "提前9999天提醒我",
        "提前一天提醒我，提前两天提醒我",
        "明天晚上12点",
        "明天午夜12点",
        "明天晚上十一点到凌晨一点",
        "明天23:00-01:00",
    ],
)
async def test_invalid_context_temporal_metadata_fails_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 0
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "start_time", "end_time"),
    [
        ("明天15:00-16:00", "15:00", "16:00"),
        ("明天下午三点至四点", "15:00", "16:00"),
        ("明天下午3-4点", "15:00", "16:00"),
        ("明天凌晨12点到1点", "00:00", "01:00"),
    ],
)
async def test_context_temporal_ranges_require_explicit_safe_connectors(
    text: str,
    start_time: str,
    end_time: str,
) -> None:
    result = await IntentParser().parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == start_time
    assert result.slots.end_time == end_time
    assert result.missing_fields == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("time_text", "expected"),
    [
        ("凌晨0点", "00:00"),
        ("凌晨12点", "00:00"),
        ("上午12点", "12:00"),
        ("中午12点", "12:00"),
        ("下午12点", "12:00"),
    ],
)
async def test_explicit_period_hour_boundaries_are_normalized_safely(
    time_text: str,
    expected: str,
) -> None:
    result = await IntentParser().parse(
        f"创建日程：明天{time_text}值班",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "值班"
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("把任务A删除", "A"),
        ("请把任务A删掉", "A"),
        ("将任务A移除", "A"),
    ],
)
async def test_object_first_delete_does_not_include_predicate_in_title(
    text: str,
    title: str,
) -> None:
    result = await IntentParser().parse(text)

    assert result.intent == IntentName.DELETE_TASK
    assert result.slots.title == title
    assert result.missing_fields == []


@pytest.mark.asyncio
async def test_update_target_strips_long_trailing_whitespace() -> None:
    padding = "\t" * 9_000

    result = await IntentParser().parse(f"把任务A{padding}优先级改为高")

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == "A"
    assert result.slots.priority == "high"
    assert result.missing_fields == []


def test_update_trailing_connector_strip_handles_near_miss() -> None:
    padding = "\t" * 9_000

    assert _strip_update_trailing_connectors(f"A{padding}然后\t") == "A"
    assert _strip_update_trailing_connectors(f"A{padding}X") == f"A{padding}X"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "title"),
    [
        ("创建一项任务：复习数学", IntentName.CREATE_TASK, "复习数学"),
        ("创建一场会议：项目组会", IntentName.CREATE_EVENT, "项目组会"),
        ("创建1份作业：提交报告", IntentName.CREATE_TASK, "提交报告"),
        ("创建1次讲座：安全培训", IntentName.CREATE_EVENT, "安全培训"),
    ],
)
async def test_single_quantity_create_envelope_is_not_part_of_title(
    text: str,
    intent: IntentName,
    title: str,
) -> None:
    result = await IntentParser().parse(text)

    assert result.intent == intent
    assert result.slots.title == title


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：研究优先级改为高的影响",
        "创建待办：记录低优先级改为高的历史",
        "创建待办：写优先级改为高的教程",
        "创建待办：阅读任务完成的含义",
        "创建待办：分析未完成任务的原因",
    ],
)
async def test_create_task_title_does_not_set_update_only_slots(text: str) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.new_title is None
    assert result.slots.priority is None
    assert result.slots.status is None


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _CONFLICTING_COMMAND_TEXTS)
@pytest.mark.parametrize("context", [(), ("创建日程：项目答辩",)])
async def test_conflicting_targets_or_mutations_fail_closed(
    text: str,
    context: tuple[str, ...],
) -> None:
    result = await IntentParser().parse(
        text,
        context=context,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.missing_fields == []
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _CONFLICTING_COMMAND_TEXTS)
@pytest.mark.parametrize("context", [(), ("创建日程：项目答辩",)])
async def test_conflicting_targets_or_mutations_bypass_llm(
    text: str,
    context: tuple[str, ...],
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=context,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 0
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.missing_fields == []
    assert result.ambiguities == []
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _NEGATED_MUTATION_TEXTS)
@pytest.mark.parametrize("context", [(), ("创建日程：项目答辩",)])
async def test_explicitly_negated_mutations_fail_closed(
    text: str,
    context: tuple[str, ...],
) -> None:
    result = await IntentParser().parse(
        text,
        context=context,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.missing_fields == []
    expected_ambiguities = ["指代不明确，需要确认具体对象"] if "这个任务" in text else []
    assert result.ambiguities == expected_ambiguities
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _NEGATED_MUTATION_TEXTS)
async def test_explicitly_negated_mutation_bypasses_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 0
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.missing_fields == []
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_title"),
    [
        ("创建待办：不要忘记交作业", IntentName.CREATE_TASK, "不要忘记交作业"),
        ("创建日程：别迟到", IntentName.CREATE_EVENT, "别迟到"),
    ],
)
async def test_title_negation_does_not_block_positive_mutation(
    text: str,
    expected_intent: IntentName,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == expected_intent
    assert result.slots.title == expected_title
    assert result.requires_confirmation is True


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _QUERY_MUTATION_CONFLICT_TEXTS)
@pytest.mark.parametrize("context", [(), ("创建日程：项目答辩",)])
async def test_query_mutation_conflicts_fail_closed(
    text: str,
    context: tuple[str, ...],
) -> None:
    result = await IntentParser().parse(
        text,
        context=context,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.missing_fields == []
    assert result.ambiguities == []
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _QUERY_MUTATION_CONFLICT_TEXTS)
async def test_query_mutation_conflict_bypasses_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 0
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.missing_fields == []
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _NARRATIVE_MUTATION_TEXTS)
@pytest.mark.parametrize("context", [(), ("创建日程：项目答辩",)])
async def test_narrative_mutation_phrases_fail_closed(
    text: str,
    context: tuple[str, ...],
) -> None:
    result = await IntentParser().parse(
        text,
        context=context,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _NARRATIVE_MUTATION_TEXTS)
async def test_narrative_mutation_phrases_bypass_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 0
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["\u200b", "\u2060", "\ufeff", " \u200b \u2060 "])
async def test_format_only_input_is_empty_and_bypasses_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    with pytest.raises(IntentParseError) as error:
        await IntentParser(llm).parse(text)

    assert error.value.code == "empty_text"
    assert llm.extract_calls == 0
    assert llm.repair_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_title"),
    [
        ("创\u200b建待办：交作业", IntentName.CREATE_TASK, "交作业"),
        ("创建待办﹕交作业", IntentName.CREATE_TASK, "交作业"),
        ("删除\u200b任务：旧作业", IntentName.DELETE_TASK, "旧作业"),
        ("更新\u2060任务：旧作业", IntentName.UPDATE_TASK, "旧作业"),
    ],
)
async def test_semantic_normalization_is_shared_with_slot_extraction(
    text: str,
    expected_intent: IntentName,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == expected_intent
    assert result.slots.title == expected_title
    assert result.source_text == text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["机器学习是什么", "帮我处理一下论文草稿", "创建待办：论文草稿"],
)
async def test_llm_mutation_requires_matching_deterministic_authority(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "slots_json",
    [
        '{"title":"财务资料"}',
        '{"task_id":"task-other"}',
        '{"title":"财务资料","description":"注入","course":"未知课程","location":"未知地点"}',
    ],
)
async def test_llm_cannot_change_deterministic_mutation_slots(slots_json: str) -> None:
    response = (
        '{"intent":"delete_task","confidence":0.99,"slots":'
        + slots_json
        + ',"missing_fields":[],"ambiguities":[],"source_text":"删除任务：论文草稿",'
        + '"requires_confirmation":true}'
    )
    llm = StaticMutationLlm(response)

    result = await IntentParser(llm).parse(
        "删除任务：论文草稿",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.DELETE_TASK
    assert result.slots.model_dump(exclude_none=True) == {"title": "论文草稿"}
    assert result.requires_confirmation is True


@pytest.mark.asyncio
async def test_llm_can_only_add_grounded_safe_metadata_to_deterministic_mutation() -> None:
    llm = StaticMutationLlm(
        """{
          "intent":"create_event",
          "confidence":0.99,
          "slots":{
            "title":"别的会议",
            "description":"讨论机器学习项目",
            "course":"机器学习",
            "location":"图书馆",
            "date":"2099-01-01",
            "start_time":"23:59",
            "event_id":"evt-other"
          },
          "missing_fields":[],
          "ambiguities":[],
          "source_text":"创建日程：明天下午三点组会，地点为图书馆，课程为机器学习，描述为讨论机器学习项目",
          "requires_confirmation":true
        }"""
    )

    result = await IntentParser(llm).parse(
        "创建日程：明天下午三点组会，地点为图书馆，课程为机器学习，描述为讨论机器学习项目",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.model_dump(exclude_none=True) == {
        "title": "组会,地点为图书馆,课程为机器学习,描述为讨论机器学习项目",
        "description": "讨论机器学习项目",
        "course": "机器学习",
        "date": "2026-07-29",
        "start_time": "15:00",
        "location": "图书馆",
    }
    assert result.requires_confirmation is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata_json",
    [
        '{"description":"未提及的描述","course":"其他课程","location":"校外"}',
        (
            '{"description":"'
            + ("x" * 4_001)
            + '","course":"'
            + ("y" * 161)
            + '","location":"'
            + ("z" * 241)
            + '"}'
        ),
        '{"description":"讨论\\u0000项目","course":"机器\\u0000学习","location":"图书\\u0000馆"}',
    ],
)
async def test_llm_ungrounded_or_unsafe_metadata_is_ignored(metadata_json: str) -> None:
    response = (
        '{"intent":"create_event","confidence":0.99,"slots":'
        + metadata_json
        + ',"missing_fields":[],"ambiguities":[],"source_text":'
        '"创建日程：明天下午三点组会","requires_confirmation":true}'
    )
    llm = StaticMutationLlm(response)

    result = await IntentParser(llm).parse(
        "创建日程：明天下午三点组会",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.model_dump(exclude_none=True) == {
        "title": "组会",
        "date": "2026-07-29",
        "start_time": "15:00",
    }
    assert result.requires_confirmation is True


@pytest.mark.asyncio
async def test_llm_metadata_rejects_field_confused_source_substrings() -> None:
    llm = StaticMutationLlm(
        """{
          "intent":"create_event",
          "confidence":0.99,
          "slots":{"description":"创建","course":"明天","location":"馆"},
          "missing_fields":[],
          "ambiguities":[],
          "source_text":"创建日程：明天下午三点图书馆组会",
          "requires_confirmation":true
        }"""
    )

    result = await IntentParser(llm).parse(
        "创建日程：明天下午三点图书馆组会",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.description is None
    assert result.slots.course is None
    assert result.slots.location is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "metadata_json", "field"),
    [
        (
            "创建日程：明天下午三点不要在图书馆讨论项目",
            IntentName.CREATE_EVENT,
            '{"location":"图书馆"}',
            "location",
        ),
        (
            "创建待办：明天下午复习材料，不是机器学习课程",
            IntentName.CREATE_TASK,
            '{"course":"机器学习"}',
            "course",
        ),
        (
            "创建日程：明天下午三点组会，不能在图书馆",
            IntentName.CREATE_EVENT,
            '{"location":"图书馆"}',
            "location",
        ),
        (
            "创建待办：复习材料，禁止选机器学习课",
            IntentName.CREATE_TASK,
            '{"course":"机器学习"}',
            "course",
        ),
        (
            "创建待办：整理材料，请勿将机密信息作为备注",
            IntentName.CREATE_TASK,
            '{"description":"机密信息"}',
            "description",
        ),
        (
            "创建日程：明天下午三点组会，不准在图书馆",
            IntentName.CREATE_EVENT,
            '{"location":"图书馆"}',
            "location",
        ),
        (
            "创建待办：复习材料，不可选机器学习课",
            IntentName.CREATE_TASK,
            '{"course":"机器学习"}',
            "course",
        ),
        (
            "创建待办：整理材料，不得将机密信息作为备注",
            IntentName.CREATE_TASK,
            '{"description":"机密信息"}',
            "description",
        ),
        (
            "创建日程：明天下午三点组会，没在图书馆开",
            IntentName.CREATE_EVENT,
            '{"location":"图书馆"}',
            "location",
        ),
        (
            "创建待办：复习材料，没有选机器学习课程",
            IntentName.CREATE_TASK,
            '{"course":"机器学习"}',
            "course",
        ),
        (
            "创建待办：整理材料，未将机密信息作为备注",
            IntentName.CREATE_TASK,
            '{"description":"机密信息"}',
            "description",
        ),
        (
            "创建日程：明天下午三点组会，尚未在图书馆确认",
            IntentName.CREATE_EVENT,
            '{"location":"图书馆"}',
            "location",
        ),
        (
            "创建待办：复习材料，不曾选机器学习课程",
            IntentName.CREATE_TASK,
            '{"course":"机器学习"}',
            "course",
        ),
    ],
)
async def test_llm_metadata_rejects_negated_explicit_cues(
    text: str,
    intent: IntentName,
    metadata_json: str,
    field: str,
) -> None:
    response = (
        '{"intent":"'
        + intent.value
        + '","confidence":0.99,"slots":'
        + metadata_json
        + ',"missing_fields":[],"ambiguities":[],"source_text":"'
        + text
        + '","requires_confirmation":true}'
    )
    llm = StaticMutationLlm(response)

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == intent
    assert getattr(result.slots, field) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "算了",
        "算了吧",
        "不要了",
        "不要了吧",
        "不用了",
        "不用了吧",
        "取消",
        "停止",
        "别动它",
        "别动任务",
    ],
)
async def test_abort_phrases_do_not_inherit_context_or_call_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=["删除任务：论文草稿"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 0
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "context"),
    [
        ("明天下午三点", ("删除任务：论文草稿",)),
        ("继续", ("创建日程：项目答辩",)),
        ("明天下午三点", ("创建日程：项目答辩", "删除任务：论文草稿")),
        ("明天下午三点", ("创建日程：项目答辩", "查看今天的日程")),
        ("论文草稿", ("删除任务", "创建待办：交作业")),
        ("明天下午三点", ("创建日程：项目答辩", "算了")),
        ("天气不错", ("删除任务",)),
        ("论文草稿", ("删除任务",)),
        ("明天下午三点", ("创建日程：项目答辩", "天气不错")),
        ("明天下午三点", ("创建日程：项目答辩", "好的")),
        ("明天下午三点", ("创建日程：项目答辩", "机器学习是什么")),
        ("明天下午三点天气不错", ("创建日程：项目答辩",)),
        ("明天下午三点查询天气", ("创建日程：项目答辩",)),
        ("明天下午三点不用开会", ("创建日程：项目答辩",)),
        ("明天下午三点算了", ("创建日程：项目答辩",)),
        ("算了，明天下午三点", ("创建日程：项目答辩",)),
        ("明天和后天下午三点", ("创建日程：项目答辩",)),
    ],
)
async def test_context_continuation_requires_a_supplied_missing_field(
    text: str,
    context: tuple[str, ...],
) -> None:
    result = await IntentParser().parse(
        text,
        context=context,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_title"),
    [
        ("创建待办：查看已完成日程", IntentName.CREATE_TASK, "查看已完成日程"),
        ("创建日程：查询已完成任务", IntentName.CREATE_EVENT, "查询已完成任务"),
    ],
)
async def test_title_query_mutation_words_do_not_pollute_positive_mutation(
    text: str,
    expected_intent: IntentName,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == expected_intent
    assert result.slots.title == expected_title
    assert result.requires_confirmation is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_title"),
    [
        ("查看明天的日程", IntentName.QUERY_SCHEDULE, None),
        ("查看今天的日程安排", IntentName.QUERY_SCHEDULE, None),
        ("列出会议安排", IntentName.QUERY_SCHEDULE, None),
        ("查看课程安排", IntentName.QUERY_SCHEDULE, None),
        ("创建待办，整理日程安排", IntentName.CREATE_TASK, "整理日程安排"),
        ("别忘了创建任务：交作业", IntentName.CREATE_TASK, "交作业"),
        ("请先删除任务：旧作业", IntentName.DELETE_TASK, "旧作业"),
        ("完成任务：机器学习作业", IntentName.UPDATE_TASK, "机器学习作业"),
        ("删除事件：项目答辩", IntentName.DELETE_EVENT, "项目答辩"),
    ],
)
async def test_non_conflicting_query_and_mutations_remain_supported(
    text: str,
    expected_intent: IntentName,
    expected_title: str | None,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == expected_intent
    assert result.slots.title == expected_title
    assert result.requires_confirmation is (expected_intent != IntentName.QUERY_SCHEDULE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["更新任务A标题改为任务B", "更新任务A改名为任务B"],
)
async def test_single_update_with_new_title_is_not_a_multi_command(text: str) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == "A"
    assert result.slots.new_title == "任务B"
    assert result.requires_confirmation is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "title", "new_title"),
    [
        ("更新任务：高优先级论文重命名为论文终稿", "高优先级论文", "论文终稿"),
        ("更新任务：低优先级论文重命名为论文终稿", "低优先级论文", "论文终稿"),
        ("更新任务：完成报告重命名为总结", "完成报告", "总结"),
        ("更新任务：未完成论文重命名为总结", "未完成论文", "总结"),
        ("更新任务：论文草稿重命名为高优先级论文", "论文草稿", "高优先级论文"),
        ("更新任务：论文草稿重命名为低优先级论文", "论文草稿", "低优先级论文"),
        ("更新任务：论文草稿重命名为完成报告", "论文草稿", "完成报告"),
        ("更新任务：论文草稿重命名为未完成论文", "论文草稿", "未完成论文"),
        (
            "更新任务：论文草稿重命名为优先级改为高的教程",
            "论文草稿",
            "优先级改为高的教程",
        ),
        (
            "更新任务：论文草稿重命名为状态改为完成的说明",
            "论文草稿",
            "状态改为完成的说明",
        ),
        ("更新任务：完成报告重命名为高优先级论文", "完成报告", "高优先级论文"),
        ("更新任务：论文草稿重命名为2026-02-30计划", "论文草稿", "2026-02-30计划"),
        ("更新任务：论文草稿重命名为2026-07-30计划", "论文草稿", "2026-07-30计划"),
        ("更新任务：论文草稿重命名为下午三点复盘", "论文草稿", "下午三点复盘"),
        ("更新任务：论文草稿重命名为提前9999天提醒我", "论文草稿", "提前9999天提醒我"),
    ],
)
async def test_update_task_target_and_new_title_do_not_set_other_fields(
    text: str,
    title: str,
    new_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == title
    assert result.slots.new_title == new_title
    assert result.slots.priority is None
    assert result.slots.status is None
    assert result.slots.due_date is None
    assert result.slots.due_time is None
    assert result.slots.reminder_minutes is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "new_title"),
    [
        ("更新事件：项目答辩重命名为明天见", "明天见"),
        ("更新事件：项目答辩重命名为下午三点复盘", "下午三点复盘"),
        ("更新事件：项目答辩重命名为2026-02-30计划", "2026-02-30计划"),
    ],
)
async def test_update_event_new_title_does_not_set_temporal_fields(
    text: str,
    new_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_EVENT
    assert result.slots.title == "项目答辩"
    assert result.slots.new_title == new_title
    assert result.slots.date is None
    assert result.slots.start_time is None
    assert result.slots.end_time is None
    assert result.slots.reminder_minutes is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "priority", "status"),
    [
        ("更新任务A，把优先级改为高", "high", None),
        ("把任务A改为低优先级", "low", None),
        ("更新任务：论文草稿标记为完成", None, "completed"),
        ("把任务A标记为完成", None, "completed"),
        ("完成任务：机器学习作业", None, "completed"),
    ],
)
async def test_update_task_fields_require_structured_modifiers(
    text: str,
    priority: str | None,
    status: str | None,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.priority == priority
    assert result.slots.status == status


@pytest.mark.asyncio
@pytest.mark.parametrize(("level", "expected"), [("高", "high"), ("低", "low")])
async def test_update_task_parses_priority_after_field_name(
    level: str,
    expected: str,
) -> None:
    result = await IntentParser().parse(
        f"更新任务：论文草稿优先级改为{level}",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == "论文草稿"
    assert result.slots.priority == expected
    assert result.requires_confirmation is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "title", "new_title", "priority", "date"),
    [
        ("更新任务A，把优先级改为高", IntentName.UPDATE_TASK, "A", None, "high", None),
        ("修改任务A，把标题改为B", IntentName.UPDATE_TASK, "A", "B", None, None),
        (
            "更新事件A，然后把时间改到明天",
            IntentName.UPDATE_EVENT,
            "A",
            None,
            None,
            "2026-07-29",
        ),
    ],
)
async def test_update_field_clauses_are_not_multiple_mutations(
    text: str,
    intent: IntentName,
    title: str,
    new_title: str | None,
    priority: str | None,
    date: str | None,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == intent
    assert result.slots.title == title
    assert result.slots.new_title == new_title
    assert result.slots.priority == priority
    assert result.slots.date == date
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
async def test_context_clarification_inherits_period_for_end_time() -> None:
    parser = IntentParser()
    now = datetime(2026, 7, 12, tzinfo=ZoneInfo("Asia/Shanghai"))

    completed = await parser.parse(
        "明天下午三点到四点",
        context=["创建日程：项目答辩"],
        now=now,
    )

    assert completed.intent == IntentName.CREATE_EVENT
    assert completed.slots.date == "2026-07-13"
    assert completed.slots.start_time == "15:00"
    assert completed.slots.end_time == "16:00"
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


_RESIDUAL_INVALID_TEMPORAL_CASES = (
    pytest.param(
        "创建日程：2026年2月3日4下午三点开会",
        "2026年2月3日4下午三点",
        id="date-trailing-digit",
    ),
    pytest.param("创建日程：大后天下午三点开会", "大后天下午三点", id="date-grand-after"),
    pytest.param("创建日程：明明天下午三点开会", "明明天下午三点", id="date-double-tomorrow"),
    pytest.param("创建日程：明天后下午三点开会", "明天后下午三点", id="date-tomorrow-suffix"),
    pytest.param("创建日程：明天3到到4点开会", "明天3到到4点", id="range-double-connector"),
    pytest.param("创建日程：明天3和4点开会", "明天3和4点", id="range-conjunction"),
    pytest.param("创建日程：明天三点到四点到开会", "明天三点到四点到", id="range-trailing-dao"),
    pytest.param("创建日程：明天到三点到四点开会", "明天到三点到四点", id="range-leading-dao"),
    pytest.param("创建日程：明天三点到四点至开会", "明天三点到四点至", id="range-trailing-zhi"),
    pytest.param(
        "创建日程：明天三点到四点再到开会",
        "明天三点到四点再到",
        id="range-trailing-zai-dao",
    ),
    pytest.param("创建日程：明天三点到开会", "明天三点到", id="range-missing-end"),
    pytest.param("创建日程：明天到下午三点开会", "明天到下午三点", id="range-missing-start"),
    pytest.param("创建日程：明天25-3-4点开会", "明天25-3-4点", id="range-three-numbers-large"),
    pytest.param("创建日程：明天3-4-5点开会", "明天3-4-5点", id="range-three-numbers"),
    pytest.param("创建日程：明天廿三点开会", "明天廿三点", id="time-unsupported-twenty"),
    pytest.param("创建日程：明天卅三点开会", "明天卅三点", id="time-unsupported-thirty"),
    pytest.param("创建日程：明天下午-3点开会", "明天下午-3点", id="time-period-minus"),
    pytest.param("创建日程：明天+3点开会", "明天+3点", id="time-leading-plus"),
    pytest.param("创建日程：明天.3点开会", "明天.3点", id="time-leading-dot"),
    pytest.param("创建日程：明天下午晚上三点开会", "明天下午晚上三点", id="time-double-period"),
    pytest.param("创建日程：明天夜里十二点开会", "明天夜里十二点", id="time-unsupported-period"),
    pytest.param(
        "创建日程：明天三点到下午四点开会",
        "明天三点到下午四点",
        id="range-second-period-only",
    ),
    pytest.param("创建日程：明天下午三点半开会", "明天下午三点半", id="time-half-hour"),
    pytest.param("创建日程：明天3点30.5分开会", "明天3点30.5分", id="time-fractional-minute"),
    pytest.param("创建日程：明天3点30分45秒开会", "明天3点30分45秒", id="time-seconds"),
    pytest.param("创建日程：明天3点45分6开会", "明天3点45分6", id="time-trailing-number"),
    pytest.param("创建日程：明天三点三十分开会", "明天三点三十分", id="time-chinese-minute"),
    pytest.param("创建日程：明天中午10点开会", "明天中午10点", id="time-ambiguous-noon"),
    pytest.param("创建待办：明天交作业，提前-1天提醒我", "提前-1天提醒我", id="reminder-negative"),
    pytest.param(
        "创建待办：明天交作业，提前+1天提醒我",
        "提前+1天提醒我",
        id="reminder-positive-sign",
    ),
    pytest.param(
        "创建待办：明天交作业，提前1.5小时提醒我",
        "提前1.5小时提醒我",
        id="reminder-fractional",
    ),
    pytest.param(
        "创建待办：明天交作业，提前365天1小时提醒我",
        "提前365天1小时提醒我",
        id="reminder-compound-unit",
    ),
    pytest.param(
        "创建待办：明天交作业，提前一天和两天提醒我",
        "提前一天和两天提醒我",
        id="reminder-compound-amount",
    ),
    pytest.param(
        "创建待办：明天交作业，提前一天提醒我和两天提醒我",
        "提前一天提醒我和两天提醒我",
        id="reminder-extra-signal",
    ),
    pytest.param(
        "创建待办：明天交作业，提前一天提醒我，提前两天",
        "提前一天提醒我，提前两天",
        id="reminder-orphan-tail",
    ),
    pytest.param(
        "创建日程：明天明天下午三点组会",
        "明天明天下午三点",
        id="date-adjacent-tomorrow",
    ),
    pytest.param(
        "创建日程：今天明天下午三点组会",
        "今天明天下午三点",
        id="date-adjacent-today-tomorrow",
    ),
    pytest.param(
        "创建日程：后天明天下午三点组会",
        "后天明天下午三点",
        id="date-adjacent-after-tomorrow",
    ),
    pytest.param(
        "创建日程：今晚明天下午三点组会",
        "今晚明天下午三点",
        id="date-adjacent-tonight-tomorrow",
    ),
    pytest.param(
        "创建日程：明天今晚三点组会",
        "明天今晚三点",
        id="date-adjacent-tomorrow-tonight",
    ),
    pytest.param(
        "创建日程：2026年2月3.5日下午三点组会",
        "2026年2月3.5日下午三点",
        id="date-fractional-day",
    ),
    pytest.param(
        "创建日程：2026年+2月3日下午三点组会",
        "2026年+2月3日下午三点",
        id="date-signed-month",
    ),
    pytest.param(
        "创建日程：2026年02.5月3日下午三点组会",
        "2026年02.5月3日下午三点",
        id="date-fractional-month",
    ),
    pytest.param(
        "创建日程：2026-02-03.5下午三点组会",
        "2026-02-03.5下午三点",
        id="date-iso-fractional-day",
    ),
    pytest.param(
        "创建日程：2026-7-29-30下午三点组会",
        "2026-7-29-30下午三点",
        id="date-extra-hyphen-component",
    ),
    pytest.param(
        "创建日程：2026/7/29/30下午三点组会",
        "2026/7/29/30下午三点",
        id="date-extra-slash-component",
    ),
    pytest.param(
        "创建日程：2026年7月29日/30下午三点组会",
        "2026年7月29日/30下午三点",
        id="date-extra-slash-after-suffix",
    ),
    pytest.param(
        "创建日程：2026年7月29日-30日下午三点组会",
        "2026年7月29日-30日下午三点",
        id="date-extra-date-after-suffix",
    ),
    pytest.param("创建日程：下午3.5点组会", "下午3.5点", id="time-fractional-hour"),
    pytest.param("创建日程：下午3点.5分组会", "下午3点.5分", id="time-dot-after-hour"),
    pytest.param("创建日程：下午3点+30分组会", "下午3点+30分", id="time-plus-minute"),
    pytest.param("创建日程：下午3:30+5组会", "下午3:30+5", id="time-plus-tail"),
    pytest.param("创建日程：下午3点:20组会", "下午3点:20", id="time-colon-after-hour"),
    pytest.param("创建日程：下午3点30分:20组会", "下午3点30分:20", id="time-colon-after-minute"),
    pytest.param("创建日程：下午午3点组会", "下午午3点", id="time-repeated-afternoon"),
    pytest.param("创建日程：晚上上3点组会", "晚上上3点", id="time-repeated-evening"),
    pytest.param("创建日程：中午午3点组会", "中午午3点", id="time-repeated-noon"),
    pytest.param("创建日程：夜间3点组会", "夜间3点", id="time-unsupported-night"),
    pytest.param("创建日程：晚间3点组会", "晚间3点", id="time-unsupported-late"),
    pytest.param("创建日程：傍晚6点组会", "傍晚6点", id="time-unsupported-dusk"),
    pytest.param("创建日程：深夜11点组会", "深夜11点", id="time-unsupported-deep-night"),
    pytest.param("创建日程：下午大约3点组会", "下午大约3点", id="time-period-approximate"),
    pytest.param("创建日程：下午约3点组会", "下午约3点", id="time-period-short-approximate"),
    pytest.param("创建日程：下午的3点组会", "下午的3点", id="time-period-particle-gap"),
    pytest.param("创建日程：今晚大概8点组会", "今晚大概8点", id="time-tonight-approximate"),
    pytest.param("创建日程：下午3点过一刻组会", "下午3点过一刻", id="time-quarter-offset"),
    pytest.param("创建日程：下午3点差5分组会", "下午3点差5分", id="time-after-offset"),
    pytest.param("创建日程：下午差5分3点组会", "下午差5分3点", id="time-before-offset"),
    pytest.param("创建日程：从下午3点组会", "从下午3点", id="range-leading-from-only"),
    pytest.param("创建日程：3或4点组会", "3或4点", id="range-or"),
    pytest.param("创建日程：3跟4点组会", "3跟4点", id="range-with"),
    pytest.param("创建日程：3以及4点组会", "3以及4点", id="range-and-long"),
    pytest.param("创建日程：3/4点组会", "3/4点", id="range-slash"),
    pytest.param(
        "创建待办：交作业，提前一天后提醒我",
        "提前一天后提醒我",
        id="reminder-contradictory-after",
    ),
    pytest.param("创建待办：交作业，提醒我", "提醒我", id="reminder-missing-amount"),
)


def _assert_no_llm_unknown(result: IntentResult, llm: RecordingMutationLlm) -> None:
    assert llm.extract_calls == 0
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.missing_fields == []
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _RESIDUAL_INVALID_TEMPORAL_CASES)
async def test_residual_invalid_temporal_mutations_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del context_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        direct_text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _RESIDUAL_INVALID_TEMPORAL_CASES)
async def test_residual_invalid_context_temporal_data_fails_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del direct_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        context_text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "那就下午25点吧",
        "好的，2026年2月30日下午三点",
        "嗯，3点30.5分吧",
    ],
)
async def test_non_pure_unsafe_context_temporal_data_bypasses_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "start_time", "end_time"),
    [
        ("创建日程：明天从三点到四点组会", "03:00", "04:00"),
        ("创建日程：明天下午三点到四点组会", "15:00", "16:00"),
        ("创建日程：明天下午3-4点组会", "15:00", "16:00"),
        ("创建日程：明天凌晨12点到1点组会", "00:00", "01:00"),
    ],
)
async def test_strict_temporal_coverage_preserves_legal_ranges(
    text: str,
    start_time: str,
    end_time: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "组会"
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == start_time
    assert result.slots.end_time == end_time


@pytest.mark.asyncio
async def test_strict_reminder_coverage_preserves_maximum_supported_value() -> None:
    result = await IntentParser().parse(
        "创建待办：明天交作业，提前365天提醒我",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "交作业"
    assert result.slots.due_date == "2026-07-29"
    assert result.slots.reminder_minutes == 525_600


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("opener", "closer"),
    [
        ("《", "》"),
        ("“", "”"),
        ("「", "」"),
        ("『", "』"),
        ("‘", "’"),
        ('"', '"'),
        ("'", "'"),
    ],
)
async def test_quoted_new_title_is_atomic_for_all_supported_quote_pairs(
    opener: str,
    closer: str,
) -> None:
    result = await IntentParser().parse(
        f"更新任务：“论文草稿”重命名为{opener}第二版，最终稿{closer}",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == "论文草稿"
    assert result.slots.new_title == "第二版,最终稿"
    assert result.slots.priority is None
    assert result.slots.status is None
    assert result.slots.due_date is None
    assert result.slots.due_time is None
    assert result.slots.reminder_minutes is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：“论文，明天下午三点，优先级改为高”"
        "重命名为“第二版，2026年8月1日，下午四点，状态改为完成”",
        "把任务“论文，明天下午三点，优先级改为高”"
        "重命名为“第二版，2026年8月1日，下午四点，状态改为完成”",
    ],
)
async def test_quoted_target_and_new_title_fields_remain_literal(text: str) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == "论文,明天下午三点,优先级改为高"
    assert result.slots.new_title == "第二版,2026年8月1日,下午四点,状态改为完成"
    assert result.slots.priority is None
    assert result.slots.status is None
    assert result.slots.due_date is None
    assert result.slots.due_time is None
    assert result.slots.reminder_minutes is None


@pytest.mark.asyncio
async def test_real_fields_after_quoted_literals_are_still_applied() -> None:
    priority = await IntentParser().parse(
        "更新任务：“论文草稿”重命名为“第二版，优先级改为高”，优先级改为高",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    status = await IntentParser().parse(
        "更新任务：“优先级改为高的论文”，状态改为完成",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    due_date = await IntentParser().parse(
        "更新任务：“论文草稿”重命名为“第二版”，截止日期改到明天",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    due_time_without_delimiter = await IntentParser().parse(
        "把任务“普通论文”重命名为“第二版”，截止时间改到明天下午三点",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert priority.slots.title == "论文草稿"
    assert priority.slots.new_title == "第二版,优先级改为高"
    assert priority.slots.priority == "high"
    assert priority.slots.status is None
    assert status.slots.title == "优先级改为高的论文"
    assert status.slots.priority is None
    assert status.slots.status == "completed"
    assert due_date.slots.title == "论文草稿"
    assert due_date.slots.new_title == "第二版"
    assert due_date.slots.due_date == "2026-07-29"
    assert due_time_without_delimiter.slots.title == "普通论文"
    assert due_time_without_delimiter.slots.new_title == "第二版"
    assert due_time_without_delimiter.slots.due_date == "2026-07-29"
    assert due_time_without_delimiter.slots.due_time == "15:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "title", "date_field", "expected_date", "expected_time"),
    [
        (
            "创建待办：明天天气预报",
            IntentName.CREATE_TASK,
            "天气预报",
            "due_date",
            "2026-07-29",
            None,
        ),
        (
            "创建待办：7月29日日程安排",
            IntentName.CREATE_TASK,
            "日程安排",
            "due_date",
            "2026-07-29",
            None,
        ),
        (
            "创建待办：2026年7月29号交论文",
            IntentName.CREATE_TASK,
            "交论文",
            "due_date",
            "2026-07-29",
            None,
        ),
        (
            "创建日程：明天下午3点点名",
            IntentName.CREATE_EVENT,
            "点名",
            "date",
            "2026-07-29",
            "15:00",
        ),
        (
            "创建日程：明天下午3点30分分组讨论",
            IntentName.CREATE_EVENT,
            "分组讨论",
            "date",
            "2026-07-29",
            "15:30",
        ),
    ],
)
async def test_temporal_boundaries_preserve_natural_title_prefixes(
    text: str,
    intent: IntentName,
    title: str,
    date_field: str,
    expected_date: str,
    expected_time: str | None,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == intent
    assert result.slots.title == title
    assert getattr(result.slots, date_field) == expected_date
    assert result.slots.start_time == expected_time


@pytest.mark.asyncio
async def test_ascii_possessive_apostrophe_is_not_an_unclosed_quote() -> None:
    result = await IntentParser().parse(
        "创建待办：Bob's paper",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "Bob's paper"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("更新任务：《论文“第一版”》重命名为“第二版”", "论文“第一版”"),
        ("更新任务“论文:第一版”重命名为“第二版”", "论文:第一版"),
    ],
)
async def test_nested_or_colon_quoted_update_targets_remain_atomic(
    text: str,
    title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == title
    assert result.slots.new_title == "第二版"
    assert result.slots.due_date is None
    assert result.slots.due_time is None


@pytest.mark.asyncio
async def test_curly_single_quoted_create_title_is_temporally_atomic() -> None:
    result = await IntentParser().parse(
        "创建待办：‘明天下午3点的表达’",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "‘明天下午3点的表达’"
    assert result.slots.due_date is None
    assert result.slots.due_time is None


@pytest.mark.asyncio
async def test_quoted_update_target_temporal_modifier_remains_literal() -> None:
    result = await IntentParser().parse(
        "更新任务：“论文截止日期改到明天的版本”，优先级改为高",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == "论文截止日期改到明天的版本"
    assert result.slots.priority == "high"
    assert result.slots.due_date is None


@pytest.mark.asyncio
@pytest.mark.parametrize("opener", ["《", "“", "「", "『", "‘", '"', "'"])
async def test_unclosed_update_quotes_fail_closed_without_llm(opener: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        f"更新任务：{opener}论文草稿重命名为第二版",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：“论文草稿”重命名为“第二版",
        "更新任务：论文草稿”重命名为第二版",
        "更新任务：优先级改为高的论文重命名为第二版",
        "更新任务：论文，重命名为“第二版”“第三版”",
        "更新任务：论文，重命名为“第二版”尾巴",
        "更新任务：论文，重命名为“第二版”，重命名为“第三版”",
        "更新任务A重命名为B然后删除任务C",
        "更新任务A重命名为B并删除任务C",
        "更新任务A重命名为B再创建待办C",
        "更新任务A重命名为B再重命名为C",
        "更新任务：《论文“第一版》，重命名为“第二版”",
        "更新任务：《论文第一版”》，重命名为“第二版”",
        "更新任务：论文截止日期改到明天的版本，优先级改为高",
        "更新任务：论文时间改到下午3点的版本，状态改为完成",
    ],
)
async def test_malformed_or_ambiguous_updates_fail_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context",
    [
        ("甲" * 10_001,),
        tuple("甲" * 9_000 for _ in range(6)),
    ],
)
async def test_oversized_context_fails_closed_without_llm(
    context: tuple[str, ...],
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        "明天下午三点",
        context=context,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


def test_mutation_classification_handles_near_limit_inputs_quickly() -> None:
    inputs = [
        ("更新甲" * 3_000)[:9_000],
        ("更新任务" + "和甲" * 5_000)[:9_000],
    ]

    started = perf_counter()
    results = [_classify_intent(text) for text in inputs]
    elapsed = perf_counter() - started

    assert all(isinstance(result, IntentName) for result in results)
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_unknown_deterministic_result_never_receives_reminder_slot() -> None:
    result = await IntentParser().parse(
        "天气不错，提前一天提醒我",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.requires_confirmation is False


@pytest.mark.asyncio
async def test_unknown_llm_result_never_keeps_or_receives_reminder_slot() -> None:
    llm = StaticMutationLlm(
        """{
          "intent":"unknown",
          "confidence":0.25,
          "slots":{"reminder_minutes":1440},
          "missing_fields":[],
          "ambiguities":[],
          "source_text":"天气不错，提前一天提醒我",
          "requires_confirmation":true
        }"""
    )

    result = await IntentParser(llm).parse(
        "天气不错，提前一天提醒我",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UNKNOWN
    assert result.slots.model_dump(exclude_none=True) == {}
    assert result.requires_confirmation is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_time"),
    [
        ("创建日程：2026-7-29下午三点组会", "15:00"),
        ("创建日程：2026-7-29晚上八点组会", "20:00"),
        ("创建日程：2026-7-29，下午三点组会", "15:00"),
    ],
)
async def test_numeric_date_boundaries_preserve_attached_period(
    text: str,
    expected_time: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "组会"
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == expected_time


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_time"),
    [
        ("2026-7-29下午三点", "15:00"),
        ("2026-7-29晚上八点", "20:00"),
        ("2026-7-29，下午三点", "15:00"),
    ],
)
async def test_context_numeric_date_boundaries_preserve_attached_period(
    text: str,
    expected_time: str,
) -> None:
    result = await IntentParser().parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "项目答辩"
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == expected_time


@pytest.mark.asyncio
async def test_ascii_single_quoted_possessive_title_is_temporally_atomic() -> None:
    result = await IntentParser().parse(
        "创建待办：'Bob's 明天下午三点 paper'",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "'Bob's 明天下午三点 paper'"
    assert result.slots.due_date is None
    assert result.slots.due_time is None


_ROUND7_INVALID_TEMPORAL_CASES = (
    pytest.param(
        "创建日程：明天下午大概在3点开会",
        "明天下午大概在3点",
        id="detached-period-at",
    ),
    pytest.param(
        "创建日程：明天下午可能3点开会",
        "明天下午可能3点",
        id="detached-period-possible",
    ),
    pytest.param(
        "创建日程：明天下午可能在3点开会",
        "明天下午可能在3点",
        id="detached-period-possible-at",
    ),
    pytest.param(
        "创建日程：明天下午（大约）3点开会",
        "明天下午（大约）3点",
        id="detached-period-parenthesized",
    ),
    pytest.param(
        "创建待办：交作业，提前一天提醒老师",
        "提前一天提醒老师",
        id="reminder-teacher-recipient",
    ),
    pytest.param(
        "创建待办：交作业，提前一天通知老师",
        "提前一天通知老师",
        id="notification-teacher-recipient",
    ),
    pytest.param(
        "创建待办：交作业，提前一天提醒我妈",
        "提前一天提醒我妈",
        id="reminder-relative-recipient",
    ),
    pytest.param(
        "创建待办：交作业，提前一天提醒你",
        "提前一天提醒你",
        id="reminder-second-person-recipient",
    ),
    pytest.param("创建日程：2026年开会", "2026年", id="incomplete-year"),
    pytest.param("创建日程：2026-7开会", "2026-7", id="incomplete-numeric-date"),
    pytest.param("创建待办：交作业，提醒", "提醒", id="orphan-remind"),
    pytest.param("创建待办：交作业，提醒一下", "提醒一下", id="orphan-remind-polite"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND7_INVALID_TEMPORAL_CASES)
async def test_round7_invalid_mutations_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del context_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        direct_text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND7_INVALID_TEMPORAL_CASES)
async def test_round7_invalid_context_fails_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del direct_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        context_text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：论文，优先级改为高，优先级改为低",
        "更新任务：论文，优先级改为高，优先级改为高",
        "更新任务：论文，状态改为完成，状态改为未完成",
        "更新任务：论文，状态改为完成，状态改为完成",
        "完成任务：论文，状态改为未完成",
    ],
)
async def test_duplicate_task_update_fields_fail_closed_without_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：“论文A”和“论文B”，优先级改为高",
        "更新任务：“论文A”或“论文B”，状态改为完成",
        "更新任务：“论文A”/“论文B”，重命名为“最终稿”",
        "删除任务：“论文A”和“论文B”",
        "删除日程：“组会A”/“组会B”",
    ],
)
async def test_multiple_quoted_targets_fail_closed_without_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_bare_one_point_quantity_fails_closed_without_llm() -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        "创建待办：写一点总结",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_overlong_incomplete_date_is_linear_and_bypasses_llm() -> None:
    llm = RecordingMutationLlm()
    text = "创建日程：" + ("1" * 9_900) + "年"

    started = perf_counter()
    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    elapsed = perf_counter() - started

    _assert_no_llm_unknown(result, llm)
    assert elapsed < 1.0


_ROUND9_INVALID_TEMPORAL_CASES = (
    pytest.param(
        "创建日程：下午2026年7月29日三点开会",
        "下午2026年7月29日三点",
        id="detached-period-full-date",
    ),
    pytest.param(
        "创建日程：下午大概在2026年7月29日三点开会",
        "下午大概在2026年7月29日三点",
        id="detached-period-filler-date",
    ),
    pytest.param("创建日程：下午，3点开会", "下午，3点", id="detached-period-comma"),
    pytest.param("创建日程：晚上：3点开会", "晚上：3点", id="detached-period-colon"),
    pytest.param("创建日程：上午；3点开会", "上午；3点", id="detached-period-semicolon"),
    pytest.param("创建日程：明天3点30秒开会", "明天3点30秒", id="time-seconds"),
    pytest.param("创建日程：明天3:30秒开会", "明天3:30秒", id="colon-time-seconds"),
    pytest.param("创建日程：明天3点钟开会", "明天3点钟", id="time-clock-suffix"),
    pytest.param("创建日程：明天3时钟开会", "明天3时钟", id="hour-clock-suffix"),
    pytest.param("创建日程：明天3点30分钟开会", "明天3点30分钟", id="time-minutes-word"),
    pytest.param("创建日程：明天3:30分钟开会", "明天3:30分钟", id="colon-minutes-word"),
    pytest.param("创建日程：明天3点30分整开会", "明天3点30分整", id="time-exact-suffix"),
    pytest.param("创建日程：明天3:30整开会", "明天3:30整", id="colon-exact-suffix"),
    pytest.param("创建日程：明天3点整开会", "明天3点整", id="hour-exact-suffix"),
    pytest.param("创建日程：明天3点多开会", "明天3点多", id="hour-approximate-more"),
    pytest.param("创建日程：明天3点左右开会", "明天3点左右", id="hour-around"),
    pytest.param("创建日程：明天3点30分左右开会", "明天3点30分左右", id="minute-around"),
    pytest.param("创建日程：从明天下午三点开会", "从明天下午三点", id="leading-from-date"),
    pytest.param(
        "创建日程：从2026年7月29日下午三点开会",
        "从2026年7月29日下午三点",
        id="leading-from-full-date",
    ),
    pytest.param("创建日程：到明天下午三点开会", "到明天下午三点", id="leading-to-date"),
    pytest.param("创建日程：明天从大概三点开会", "明天从大概三点", id="leading-from-filler"),
    pytest.param("创建日程：明天三点大概到开会", "明天三点大概到", id="trailing-to-filler"),
    pytest.param("创建日程：2026-开会", "2026-", id="incomplete-date-year-hyphen"),
    pytest.param("创建日程：2026/开会", "2026/", id="incomplete-date-year-slash"),
    pytest.param("创建日程：2026-7-开会", "2026-7-", id="incomplete-date-day-hyphen"),
    pytest.param("创建日程：2026/7/开会", "2026/7/", id="incomplete-date-day-slash"),
    pytest.param("创建日程：2026--7开会", "2026--7", id="repeated-date-hyphen"),
    pytest.param("创建日程：2026//7开会", "2026//7", id="repeated-date-slash"),
    pytest.param("创建日程：2026-/7开会", "2026-/7", id="mixed-date-separators"),
    pytest.param("创建日程：2026.5-7开会", "2026.5-7", id="fractional-date-component"),
    pytest.param("创建日程：2026.7.29开会", "2026.7.29", id="unsupported-dotted-date"),
    pytest.param("创建日程：2026..7.29开会", "2026..7.29", id="repeated-dotted-date"),
    pytest.param(
        "创建日程：2026年7月29号号开会",
        "2026年7月29号号",
        id="repeated-full-date-number-suffix",
    ),
    pytest.param(
        "创建日程：2026年7月29日日开会",
        "2026年7月29日日",
        id="repeated-full-date-day-suffix",
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND9_INVALID_TEMPORAL_CASES)
async def test_round9_invalid_temporal_mutations_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del context_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        direct_text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND9_INVALID_TEMPORAL_CASES)
async def test_round9_invalid_context_temporal_data_fails_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del direct_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        context_text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


_ROUND9_INVALID_REMINDER_CASES = (
    pytest.param(
        "创建待办：交作业，不要提前一天提醒我",
        "不要提前一天提醒我",
        id="negated-reminder-do-not",
    ),
    pytest.param(
        "创建待办：交作业，无需提前一天提醒我",
        "无需提前一天提醒我",
        id="negated-reminder-unneeded",
    ),
    pytest.param(
        "创建待办：交作业，别提前一天提醒我",
        "别提前一天提醒我",
        id="negated-reminder-dont",
    ),
    pytest.param("创建待办：交作业，提醒老师", "提醒老师", id="orphan-reminder-teacher"),
    pytest.param("创建待办：交作业，通知你", "通知你", id="orphan-notify-you"),
    pytest.param("创建待办：交作业，提醒我妈", "提醒我妈", id="orphan-reminder-relative"),
    pytest.param("创建待办：交作业，提醒一下我", "提醒一下我", id="orphan-reminder-polite"),
    pytest.param("创建待办：交作业，通知下我", "通知下我", id="orphan-notify-polite"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND9_INVALID_REMINDER_CASES)
async def test_round9_invalid_reminder_mutations_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del context_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        direct_text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND9_INVALID_REMINDER_CASES)
async def test_round9_invalid_context_reminders_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del direct_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        context_text,
        context=["创建待办：交作业"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：论文优先级改为高然后优先级改为低",
        "更新任务：论文优先级改为高并且优先级改为低",
        "更新任务：论文优先级改为高接着优先级改为低",
        "更新任务：论文优先级改为高随后优先级改为低",
        "更新任务：论文优先级改为高之后优先级改为低",
        "更新任务：论文优先级改为高又优先级改为低",
        "更新任务：论文优先级改为高再优先级改为低",
        "更新任务：论文优先级改为高、优先级改为低",
        "更新任务：论文优先级改为高优先级改为低",
        "更新任务：论文状态改为完成然后状态改为未完成",
        "更新任务：论文状态改为完成并且状态改为未完成",
        "更新任务：论文状态改为完成接着状态改为未完成",
        "更新任务：论文状态改为完成随后状态改为未完成",
        "更新任务：论文状态改为完成之后状态改为未完成",
        "更新任务：论文状态改为完成又状态改为未完成",
        "更新任务：论文状态改为完成再状态改为未完成",
        "更新任务：论文状态改为完成、状态改为未完成",
        "更新任务：论文状态改为完成状态改为未完成",
    ],
)
async def test_round9_loose_duplicate_task_fields_fail_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：论文A和“论文B”，优先级改为高",
        "更新任务：“论文A”和论文B，优先级改为高",
        "更新任务：论文A和“论文B”，重命名为“最终稿”",
        "删除任务：论文A和“论文B”",
        "删除任务：“论文A”和论文B",
        "删除日程：组会A和“组会B”",
        "删除日程：“组会A”和组会B",
    ],
)
async def test_round9_mixed_quoted_targets_fail_closed_without_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：论文重命名为“第二版”，垃圾",
        "更新任务：论文重命名为“第二版”，算了",
        "更新任务：论文重命名为“第二版”，取消",
        "更新任务：论文重命名为“第二版”，停止",
        "更新任务：论文重命名为“第二版”，别动了",
        "更新任务：论文重命名为“第二版”，截止日期改到明天，垃圾",
    ],
)
async def test_round9_invalid_quoted_rename_tails_fail_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：写1点总结",
        "创建待办：写１点总结",
        "创建待办：写一点总结",
        "创建待办：一点点总结",
        "创建待办：写一点",
        "创建待办：写一时总结",
    ],
)
async def test_round9_ambiguous_bare_one_times_fail_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round9_temporal_positive_controls_remain_supported() -> None:
    event = await IntentParser().parse(
        "创建日程：明天一点开会",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    task = await IntentParser().parse(
        "创建待办：明天一点交作业",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    ranged = await IntentParser().parse(
        "创建日程：从明天下午三点到四点开会",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    lexical_period = await IntentParser().parse(
        "创建日程：明天下午茶，3点新品讨论",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert event.intent == IntentName.CREATE_EVENT
    assert event.slots.date == "2026-07-29"
    assert event.slots.start_time == "01:00"
    assert task.intent == IntentName.CREATE_TASK
    assert task.slots.due_date == "2026-07-29"
    assert task.slots.due_time == "01:00"
    assert ranged.intent == IntentName.CREATE_EVENT
    assert ranged.slots.date == "2026-07-29"
    assert ranged.slots.start_time == "15:00"
    assert ranged.slots.end_time == "16:00"
    assert lexical_period.intent == IntentName.CREATE_EVENT
    assert lexical_period.slots.date == "2026-07-29"
    assert lexical_period.slots.start_time == "03:00"


@pytest.mark.asyncio
async def test_round9_affirmative_memory_reminder_remains_supported() -> None:
    result = await IntentParser().parse(
        "创建待办：交作业，不要忘记提前一天提醒我",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.reminder_minutes == 1_440


_ROUND10_INVALID_TEMPORAL_CASES = (
    pytest.param(
        "创建日程：明天3点下午开会",
        "明天3点下午",
        id="detached-period-after-time",
    ),
    pytest.param(
        "创建日程：明天3点，下午开会",
        "明天3点，下午",
        id="detached-period-after-time-comma",
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND10_INVALID_TEMPORAL_CASES)
async def test_round10_detached_trailing_periods_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del context_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        direct_text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND10_INVALID_TEMPORAL_CASES)
async def test_round10_context_detached_trailing_periods_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del direct_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        context_text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：写1点总结，明天交",
        "创建待办：明天先写一点总结",
    ],
)
async def test_round10_distant_dates_do_not_disambiguate_bare_one_times(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：论文A，“论文B”，优先级改为高",
        "更新任务：“论文A”，论文B，优先级改为高",
        "删除任务：“论文A”，论文B",
        "删除日程：组会A，“组会B”",
    ],
)
async def test_round10_comma_mixed_quoted_targets_fail_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_intent", "expected_title"),
    [
        ("创建日程：2026年7月29日日程安排", IntentName.CREATE_EVENT, "日程安排"),
        ("创建日程：2026年7月29日日历整理", IntentName.CREATE_EVENT, "日历整理"),
        ("创建待办：整理2026年7月29日日报", IntentName.CREATE_TASK, "整理日报"),
        (
            "创建日程：2026年7月29号号召同学开会",
            IntentName.CREATE_EVENT,
            "号召同学开会",
        ),
    ],
)
async def test_round10_full_date_word_continuations_remain_supported(
    text: str,
    expected_intent: IntentName,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == expected_intent
    assert result.slots.title == expected_title
    date_value = (
        result.slots.date if expected_intent == IntentName.CREATE_EVENT else result.slots.due_date
    )
    assert date_value == "2026-07-29"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        ("创建日程：明天3点整理报告", "整理报告"),
        ("创建日程：明天3点钟楼见", "钟楼见"),
        ("创建日程：明天3点多媒体课", "多媒体课"),
        ("创建日程：明天3点左右手训练", "左右手训练"),
    ],
)
async def test_round10_time_suffix_word_continuations_remain_supported(
    text: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == expected_title
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == "03:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建日程：从明天三点到四点开会",
        "创建日程：从明天大概三点到四点开会",
    ],
)
async def test_round10_date_ranges_remove_linked_from_and_fillers_from_title(
    text: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "开会"
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == "03:00"
    assert result.slots.end_time == "04:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：交作业，不要忘记提前一天提醒我",
        "创建待办：交作业，别忘了提前一天提醒我",
    ],
)
async def test_round10_affirmative_reminder_prefixes_are_removed_from_title(
    text: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "交作业"
    assert result.slots.reminder_minutes == 1_440


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        ("创建待办：整理通知公告", "整理通知公告"),
        ("创建待办：研究提醒功能的实现", "研究提醒功能的实现"),
    ],
)
async def test_round10_nominal_reminder_words_remain_literal_titles(
    text: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == expected_title
    assert result.slots.reminder_minutes is None


@pytest.mark.asyncio
async def test_round10_quoted_rename_allows_conjunction_separated_real_fields() -> None:
    result = await IntentParser().parse(
        "更新任务：“论文草稿”重命名为“第二版”并且优先级改为高并且状态改为完成",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == "论文草稿"
    assert result.slots.new_title == "第二版"
    assert result.slots.priority == "high"
    assert result.slots.status == "completed"


@pytest.mark.asyncio
async def test_round10_relative_date_filler_keeps_bare_one_attached() -> None:
    result = await IntentParser().parse(
        "创建待办：明天大概一点交作业",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "交作业"
    assert result.slots.due_date == "2026-07-29"
    assert result.slots.due_time == "01:00"


@pytest.mark.asyncio
async def test_round10_explicit_notice_query_with_task_context_is_not_reminder_conflict() -> None:
    result = await IntentParser().parse(
        "查询通知",
        context=["创建待办：交作业"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.SEARCH_NOTICE


_ROUND11_INVALID_TEMPORAL_CASES = (
    pytest.param("创建日程：明天3点前开会", "明天3点前", id="time-before"),
    pytest.param("创建日程：明天3点后开会", "明天3点后", id="time-after"),
    pytest.param("创建日程：明天3点之前开会", "明天3点之前", id="time-before-long"),
    pytest.param("创建日程：明天3点之后开会", "明天3点之后", id="time-after-long"),
    pytest.param("创建日程：明天3点以前开会", "明天3点以前", id="time-earlier-than"),
    pytest.param("创建日程：明天3点以后开会", "明天3点以后", id="time-later-than"),
    pytest.param("创建日程：明天3点以内开会", "明天3点以内", id="time-within"),
    pytest.param("创建日程：明天3点以外开会", "明天3点以外", id="time-outside"),
    pytest.param("创建日程：明天3点上下开会", "明天3点上下", id="time-around-up-down"),
    pytest.param("创建日程：明天3点附近开会", "明天3点附近", id="time-nearby"),
    pytest.param("创建待办：7月29日前交作业", "7月29日前", id="short-date-before"),
    pytest.param(
        "创建待办：2026-7-29之后交作业",
        "2026-7-29之后",
        id="full-date-after",
    ),
    pytest.param("创建日程：明天之前开会", "明天之前", id="relative-date-before"),
    pytest.param("创建日程：明天以后开会", "明天以后", id="relative-date-after"),
    pytest.param("创建日程：明天左右开会", "明天左右", id="relative-date-around"),
    pytest.param("创建日程：大约3点开会", "大约3点", id="unanchored-approximate"),
    pytest.param("创建日程：明天下午预计3点开会", "明天下午预计3点", id="estimated"),
    pytest.param("创建日程：明天准时3点开会", "明天准时3点", id="on-time"),
    pytest.param("创建日程：明天正好3点开会", "明天正好3点", id="exactly"),
    pytest.param("创建日程：明天约莫3点开会", "明天约莫3点", id="roughly"),
    pytest.param("创建日程：明天最晚3点开会", "明天最晚3点", id="latest"),
    pytest.param("创建日程：明天最早3点开会", "明天最早3点", id="earliest"),
    pytest.param("创建日程：明天不到3点开会", "明天不到3点", id="not-yet"),
    pytest.param("创建日程：明天3点然后到", "明天3点然后到", id="dangling-then-to"),
    pytest.param("创建日程：明天3点接着到", "明天3点接着到", id="dangling-next-to"),
    pytest.param("创建待办：明天一点点总结", "明天一点点总结", id="one-point-quantity"),
    pytest.param("创建待办：明天一时兴起写总结", "明天一时兴起", id="one-shi-idiom"),
    pytest.param("创建待办：明天一点心意", "明天一点心意", id="one-point-token"),
    pytest.param("创建待办：整理三点建议", "整理三点建议", id="three-points-advice"),
    pytest.param("创建待办：记录两点要求", "记录两点要求", id="two-points-requirements"),
    pytest.param("创建待办：总结四点意见", "总结四点意见", id="four-points-opinions"),
    pytest.param("创建待办：明天完成三点内容", "明天完成三点内容", id="three-points-content"),
    pytest.param("创建日程：讨论三点原则", "讨论三点原则", id="three-points-principles"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND11_INVALID_TEMPORAL_CASES)
async def test_round11_invalid_temporal_mutations_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del context_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        direct_text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND11_INVALID_TEMPORAL_CASES)
async def test_round11_invalid_context_temporal_data_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del direct_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        context_text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：论文重命名为第二版，垃圾",
        "更新任务：论文重命名为第二版，算了",
        "更新任务：论文优先级改为高，垃圾",
        "更新任务：论文优先级改为高，算了",
        "更新任务：论文截止日期改到明天，算了",
        "更新任务：“论文草稿”重命名为“第二版”，然后",
        "更新任务：“论文草稿”重命名为“第二版”并且",
        "更新任务：“论文草稿”重命名为“第二版”，优先级改为高，然后",
        "更新任务：“论文草稿”重命名为“第二版”，状态改为完成，把",
        "更新任务：“论文草稿”重命名为“第二版”，截止日期改到明天，接着",
        "更新任务：“论文草稿”重命名为“第二版”，提前一天提醒我，随后",
        "更新任务：论文优先级改为高再改为低",
        "更新任务：论文优先级改为高再设为低",
        "更新任务：论文状态改为完成又未完成",
        "更新任务：论文优先级改为中",
        "更新任务：论文优先级改为高低",
        "更新任务：论文状态改为进行中",
        "更新任务：论文状态改为完成中",
    ],
)
async def test_round11_invalid_update_clauses_fail_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：论文A；“论文B”，优先级改为高",
        "更新任务：论文A;“论文B”，优先级改为高",
        "更新任务：论文A。“论文B”，优先级改为高",
        "更新任务：论文A！“论文B”，优先级改为高",
        "更新任务：论文A？“论文B”，优先级改为高",
        "删除任务：论文A；“论文B”",
        "删除任务：论文A。“论文B”",
        "删除任务：A和任务B",
        "删除任务：A、任务B",
        "删除任务：A以及任务B",
        "删除任务：A或任务B",
        "删除任务：A/任务B",
        "更新任务：A和任务B，优先级改为高",
        "删除日程：A和日程B",
    ],
)
async def test_round11_explicit_multiple_targets_fail_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


_ROUND11_INVALID_REMINDER_CASES = (
    pytest.param("创建待办：交作业，提醒我一下", "提醒我一下", id="remind-me-politeness"),
    pytest.param("创建待办：交作业，通知我一下", "通知我一下", id="notify-me-politeness"),
    pytest.param("创建待办：交作业，提醒本人", "提醒本人", id="remind-self"),
    pytest.param("创建待办：交作业，通知他们", "通知他们", id="notify-them"),
    pytest.param("创建待办：交作业，提醒负责人", "提醒负责人", id="remind-owner"),
    pytest.param("创建待办：交作业，提醒辅导员", "提醒辅导员", id="remind-counselor"),
    pytest.param("创建待办：交作业，通知张老师", "通知张老师", id="notify-teacher-name"),
    pytest.param("创建待办：交作业，提醒李明", "提醒李明", id="remind-person-name"),
    pytest.param(
        "创建待办：交作业，没必要提前一天提醒我",
        "没必要提前一天提醒我",
        id="unnecessary-reminder",
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND11_INVALID_REMINDER_CASES)
async def test_round11_invalid_reminder_mutations_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del context_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        direct_text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND11_INVALID_REMINDER_CASES)
async def test_round11_invalid_context_reminders_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del direct_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        context_text,
        context=["创建待办：交作业"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        ("创建日程：明天3点前端评审", "前端评审"),
        ("创建日程：明天3点后端联调", "后端联调"),
        ("创建日程：明天3点后勤安排", "后勤安排"),
        ("创建日程：明天3点上下文分析", "上下文分析"),
        ("创建日程：明天3点左右手训练", "左右手训练"),
        ("创建日程：明天3点以前端技术为主题复盘", "以前端技术为主题复盘"),
        ("创建日程：明天3点以内存优化为主题复盘", "以内存优化为主题复盘"),
        ("创建日程：明天3点以外语学习为主题复盘", "以外语学习为主题复盘"),
        ("创建日程：明天3点整形手术", "整形手术"),
        ("创建日程：明天3点钟点工面试", "钟点工面试"),
    ],
)
async def test_round11_time_relation_word_continuations_remain_supported(
    text: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == expected_title
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == "03:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        ("创建日程：明天后端联调", "后端联调"),
        ("创建日程：2026年7月29日前端评审", "前端评审"),
        ("创建日程：2026年7月29日后端联调", "后端联调"),
        ("创建日程：2026年7月29日左右手训练", "左右手训练"),
        ("创建日程：2026年7月29日日语课", "日语课"),
        ("创建日程：2026年7月29号号角排练", "号角排练"),
        ("创建日程：2026年7月29日号召同学开会", "号召同学开会"),
    ],
)
async def test_round11_date_relation_word_continuations_remain_supported(
    text: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == expected_title
    assert result.slots.date == "2026-07-29"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        ("创建待办：整理通知", "整理通知"),
        ("创建待办：发送通知", "发送通知"),
        ("创建待办：写提醒", "写提醒"),
        ("创建待办：开发提前一天提醒功能", "开发提前一天提醒功能"),
    ],
)
async def test_round11_nominal_reminder_titles_remain_supported(
    text: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == expected_title
    assert result.slots.reminder_minutes is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：交作业，别忘了要提前一天提醒我",
        "创建待办：交作业，务必提前一天提醒我",
        "创建待办：交作业，记住提前一天提醒我",
    ],
)
async def test_round11_affirmative_reminder_prefixes_remain_supported(
    text: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "交作业"
    assert result.slots.reminder_minutes == 1_440


@pytest.mark.asyncio
async def test_round11_update_positive_controls_remain_supported() -> None:
    quoted = await IntentParser().parse(
        "更新任务：“论文草稿”重命名为“第二版”并且优先级改为高并且状态改为完成",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    unquoted = await IntentParser().parse(
        "更新任务：论文草稿重命名为优先级改为高的教程",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    punctuated = await IntentParser().parse(
        "更新任务：“论文草稿”重命名为“第二版”。",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    embedded_quote = await IntentParser().parse(
        "删除任务：论文“最终稿”",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert quoted.intent == IntentName.UPDATE_TASK
    assert quoted.slots.new_title == "第二版"
    assert quoted.slots.priority == "high"
    assert quoted.slots.status == "completed"
    assert unquoted.intent == IntentName.UPDATE_TASK
    assert unquoted.slots.new_title == "优先级改为高的教程"
    assert punctuated.intent == IntentName.UPDATE_TASK
    assert punctuated.slots.new_title == "第二版"
    assert embedded_quote.intent == IntentName.DELETE_TASK
    assert embedded_quote.slots.title == "论文“最终稿”"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：论文重命名为第二版然后",
        "更新任务：论文重命名为第二版同时",
        "更新任务：论文重命名为第二版，优先级改为高，算了",
        "更新任务：论文，请把优先级改为高",
        "更新任务：论文，麻烦把状态改为完成",
        "更新任务：论文，垃圾，优先级改为高",
        "更新任务：论文优先级设置为中",
        "更新任务：论文状态设置为进行中",
        "更新任务：论文日期改到3点",
        "更新任务：论文截止日期改到3点",
        "更新任务：论文开始时间改到明天",
        "更新任务：论文时间改到提前一天提醒我",
        "更新任务：论文结束时间改到3点",
        "完成任务：论文，算了",
        "完成任务：论文然后",
    ],
)
async def test_round12_invalid_update_shapes_fail_closed_without_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "删除任务：任务A并任务B",
        "删除任务：任务A并且任务B",
        "删除任务：任务A同任务B",
        "删除任务：任务A连同任务B",
        "删除任务：任务A，任务B",
        "删除任务：任务A；任务B",
        "删除任务：任务A。任务B",
        "更新任务A和任务B，优先级改为高",
        "删除任务：任务A，另一个“任务B”",
        "删除任务：任务A和名为“任务B”",
        "更新任务：任务A；任务B，状态改为完成",
    ],
)
async def test_round12_additional_multiple_targets_fail_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


_ROUND12_INVALID_REMINDER_CASES = (
    pytest.param("创建待办：交作业然后提醒我", "然后提醒我", id="then-no-punctuation"),
    pytest.param("创建待办：交作业并且提醒我", "并且提醒我", id="and-no-punctuation"),
    pytest.param("创建待办：交作业再提醒我", "再提醒我", id="again-no-punctuation"),
    pytest.param("创建待办：交作业请提醒我", "请提醒我", id="please-no-punctuation"),
    pytest.param("创建待办：交作业，请你提醒我", "请你提醒我", id="please-you"),
    pytest.param("创建待办：交作业稍后提醒我", "稍后提醒我", id="later"),
    pytest.param("创建待办：交作业到时候提醒我", "到时候提醒我", id="then"),
    pytest.param("创建待办：交作业届时提醒我", "届时提醒我", id="at-that-time"),
    pytest.param("创建待办：交作业明天提醒我", "明天提醒我", id="tomorrow"),
    pytest.param("创建待办：交作业及时提醒我", "及时提醒我", id="promptly"),
    pytest.param("创建待办：交作业，别提醒我", "别提醒我", id="do-not-remind"),
    pytest.param("创建待办：交作业，不要提醒我", "不要提醒我", id="do-not-remind-long"),
    pytest.param("创建待办：交作业，无需提醒我", "无需提醒我", id="need-not-remind"),
    pytest.param("创建待办：交作业，请勿通知老师", "请勿通知老师", id="do-not-notify"),
    pytest.param(
        "创建待办：交作业，提醒系统管理员",
        "提醒系统管理员",
        id="disguised-system-admin",
    ),
    pytest.param(
        "创建待办：交作业，提醒功能负责人",
        "提醒功能负责人",
        id="disguised-feature-owner",
    ),
    pytest.param(
        "创建待办：交作业，通知服务负责人",
        "通知服务负责人",
        id="disguised-service-owner",
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND12_INVALID_REMINDER_CASES)
async def test_round12_invalid_reminder_requests_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del context_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        direct_text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND12_INVALID_REMINDER_CASES)
async def test_round12_invalid_context_reminders_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del direct_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        context_text,
        context=["创建待办：交作业"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


_ROUND12_INVALID_TEMPORAL_CASES = (
    pytest.param("创建日程：早于明天开会", "早于明天", id="earlier-than-date"),
    pytest.param("创建日程：晚于明天开会", "晚于明天", id="later-than-date"),
    pytest.param("创建日程：迟于明天开会", "迟于明天", id="later-than-date-alt"),
    pytest.param("创建日程：不迟于明天开会", "不迟于明天", id="no-later-than-date"),
    pytest.param("创建日程：不超过明天开会", "不超过明天", id="not-beyond-date"),
    pytest.param("创建日程：超过明天开会", "超过明天", id="beyond-date"),
    pytest.param("创建日程：至少明天开会", "至少明天", id="at-least-date"),
    pytest.param("创建日程：至多明天开会", "至多明天", id="at-most-date"),
    pytest.param("创建日程：明天3点之内开会", "明天3点之内", id="time-within-long"),
    pytest.param("创建日程：明天3点之外开会", "明天3点之外", id="time-outside-long"),
    pytest.param("创建日程：明天3点内开会", "明天3点内", id="time-within-short"),
    pytest.param("创建日程：明天3点不到开会", "明天3点不到", id="time-not-reaching"),
    pytest.param("创建日程：明天3点过后开会", "明天3点过后", id="time-after-alt"),
    pytest.param("创建日程：明天3点许开会", "明天3点许", id="time-about-classical"),
    pytest.param("创建日程：明天3点，之后开会", "明天3点，之后", id="detached-time-relation"),
    pytest.param("创建日程：明天，之前开会", "明天，之前", id="detached-date-relation"),
    pytest.param("创建日程：明天3点准时到", "明天3点准时到", id="on-time-dangling-to"),
    pytest.param("创建日程：明天3点正好到", "明天3点正好到", id="exactly-dangling-to"),
    pytest.param("创建日程：明天3点以及到", "明天3点以及到", id="and-dangling-to"),
    pytest.param("创建日程：明天3点接下来到", "明天3点接下来到", id="next-dangling-to"),
    pytest.param("创建日程：明天3点−4点开会", "明天3点−4点", id="unicode-minus-range"),
    pytest.param("创建日程：明天3点‑4点开会", "明天3点‑4点", id="nonbreaking-hyphen-range"),
    pytest.param("创建待办：整理三点看法", "整理三点看法", id="three-views"),
    pytest.param("创建待办：梳理三点认识", "梳理三点认识", id="three-understandings"),
    pytest.param("创建待办：总结三点启示", "总结三点启示", id="three-insights"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND12_INVALID_TEMPORAL_CASES)
async def test_round12_invalid_temporal_shapes_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del context_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        direct_text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND12_INVALID_TEMPORAL_CASES)
async def test_round12_invalid_context_temporal_shapes_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    del direct_text
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        context_text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "删除任务：需求分析与设计",
        "删除任务：研发和测试",
        "删除任务：A/B测试",
        "删除任务：UI/UX设计",
        "删除任务：R&D文档",
    ],
)
async def test_round12_natural_conjunction_targets_remain_supported(text: str) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.DELETE_TASK
    assert result.slots.title == text.split("：", 1)[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        ("创建日程：明天3点前沿论坛", "前沿论坛"),
        ("创建日程：明天3点后备计划", "后备计划"),
        ("创建日程：明天3点上下游讨论", "上下游讨论"),
        ("创建日程：明天3点左右脑训练", "左右脑训练"),
        ("创建日程：明天3点以内核为主题分享", "以内核为主题分享"),
        ("创建日程：明天3点以外贸为主题交流", "以外贸为主题交流"),
        ("创建日程：明天3点以后端为主题评审", "以后端为主题评审"),
    ],
)
async def test_round12_temporal_relation_word_continuations_remain_supported(
    text: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == expected_title
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == "03:00"


@pytest.mark.asyncio
async def test_round12_explicit_time_can_precede_lexical_point_title() -> None:
    result = await IntentParser().parse(
        "创建日程：明天3点讨论三点看法",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "讨论三点看法"
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == "03:00"
    assert result.slots.end_time is None


@pytest.mark.asyncio
async def test_round12_attached_date_keeps_bare_one_as_time() -> None:
    result = await IntentParser().parse(
        "创建日程：明天一时开会",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "开会"
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == "01:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        ("创建待办：优化通知服务", "优化通知服务"),
        ("创建待办：设计提醒系统", "设计提醒系统"),
        ("创建待办：编写通知模板", "编写通知模板"),
    ],
)
async def test_round12_nominal_reminder_titles_remain_supported(
    text: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == expected_title
    assert result.slots.reminder_minutes is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：交作业，然后请你提前一天提醒我",
        "创建待办：交作业并且麻烦你提前一天提醒我",
        "创建待办：交作业，到时候提前一天提醒我",
    ],
)
async def test_round12_valid_reminder_prefixes_are_removed_from_title(
    text: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "交作业"
    assert result.slots.reminder_minutes == 1_440


@pytest.mark.asyncio
async def test_round12_setting_synonyms_and_typed_time_update_remain_supported() -> None:
    priority = await IntentParser().parse(
        "更新任务：论文优先级设置为高",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    status = await IntentParser().parse(
        "更新任务：论文状态设置为已完成",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    due_time = await IntentParser().parse(
        "更新任务：论文开始时间改到3点",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert priority.intent == IntentName.UPDATE_TASK
    assert priority.slots.title == "论文"
    assert priority.slots.priority == "high"
    assert status.intent == IntentName.UPDATE_TASK
    assert status.slots.title == "论文"
    assert status.slots.status == "completed"
    assert due_time.intent == IntentName.UPDATE_TASK
    assert due_time.slots.title == "论文"
    assert due_time.slots.due_time == "03:00"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：“论文”重命名为“第二版”吧",
        "更新日程：答辩，地点改为图书馆吧",
        "更新任务：论文，课程改为软件工程呢",
        "更新任务：论文，描述改为完成初稿啊",
        "更新任务：“论文”重命名为“第二版”同时优先级改为高",
        "更新任务：“论文”重命名为“第二版”以及状态改为完成",
    ],
)
async def test_round12_particles_metadata_and_connectors_remain_supported(
    text: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent in {IntentName.UPDATE_TASK, IntentName.UPDATE_EVENT}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "删除任务：论文和报告",
        "删除任务：论文与报告",
        "删除任务：论文同报告",
        "删除任务：论文并报告",
        "删除任务：论文及报告",
        "删除任务：论文还有报告",
        "删除任务：论文或者报告",
        "更新任务：论文和报告，优先级改为高",
        "更新任务论文和报告，状态改为完成",
        "创建日程：大约在3点开会",
        "创建日程：差不多在3点开会",
        "创建日程：可能在3点开会",
        "创建日程：预计在3点开会",
        "创建日程：最晚在3点开会",
        "创建日程：最迟3点开会",
        "创建待办：大约在明天交作业",
        "创建待办：预计在明天交作业",
    ],
)
async def test_round13_ambiguous_targets_and_temporal_prefixes_fail_closed(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "大约在3点",
        "差不多会在3点",
        "预计在3点",
        "最迟3点",
        "大约在明天",
        "预计会在明天",
    ],
)
async def test_round13_context_temporal_prefixes_fail_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round13_update_particles_preserve_task_field_values() -> None:
    priority = await IntentParser().parse(
        "更新任务：论文优先级改为高吧",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    status = await IntentParser().parse(
        "更新任务：论文状态改为完成呢",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert priority.intent == IntentName.UPDATE_TASK
    assert priority.slots.title == "论文"
    assert priority.slots.priority == "high"
    assert status.intent == IntentName.UPDATE_TASK
    assert status.slots.title == "论文"
    assert status.slots.status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建日程：明天大概在3点开会",
        "创建日程：明天可能会在3点开会",
    ],
)
async def test_round13_date_anchored_approximate_times_remain_supported(
    text: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "开会"
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == "03:00"


_ROUND14_INVALID_DIRECT_CASES = (
    "创建待办：交作业，拒绝提前一天提醒我",
    "创建待办：交作业，免去提前一天提醒我",
    "创建待办：交作业，省掉提前一天提醒我",
    "创建待办：交作业，跳过提前一天提醒我",
    "创建待办：交作业，暂停提前一天提醒我",
    "创建待办：交作业，杜绝提前一天提醒我",
    "创建待办：交作业，放弃提前一天提醒我",
    "创建待办：交作业，撤掉提前一天提醒我",
    "创建待办：交作业，关掉提前一天提醒我",
    "创建待办：交作业，没想着提前一天提醒我",
    "创建待办：交作业，没有想要提前一天提醒我",
    "创建待办：交作业，从未计划提前一天提醒我",
    "创建待办：交作业，不要，提前一天提醒我",
    "创建待办：交作业，何必；提前一天提醒我",
    "创建待办：交作业，取消。提前一天提醒我",
    "创建待办：交作业，请老师提前一天提醒我",
    "创建待办：交作业，让老师提前一天提醒我",
    "创建待办：交作业，由老师提前一天提醒我",
    "创建待办：交作业，要求老师提前一天提醒我",
    "创建待办：交作业，安排老师提前一天提醒我",
    "创建待办：交作业，委托老师提前一天提醒我",
    "创建待办：交作业，麻烦老师提前一天提醒我",
    "创建日程：大约将在3点开会",
    "创建日程：预计将于3点开会",
    "创建日程：预计，3点开会",
    "创建待办：预计将于明天交作业",
    "创建日程：至迟3点开会",
    "创建日程：少于3点开会",
    "创建日程：明天3点/之前开会",
    "创建日程：明天3点·之后开会",
    "创建日程：明天3点，请在之前开会",
    "创建日程：明天3点最终到开会",
    "创建日程：明天3点按时到开会",
    "创建日程：明天3点恰好到开会",
    "创建日程：明天3点正点到开会",
    "创建日程：明天3点马上到开会",
    "创建日程：明天3点才到开会",
    "创建日程：会议明天3点结束",
    "删除任务：论文和讲稿",
    "删除任务：合同与发票",
    "删除任务：A和另一个B",
    "删除任务：A或是B",
    "删除任务：甲和乙",
    "删除任务：买牛奶和写报告",
    "更新任务：开会与买菜，优先级改为高",
    "完成任务：张三同李四",
    "更新任务：论文优先级设成中",
    "更新任务：论文状态设成进行中",
    "更新日程：答辩地点改为图书馆和描述改为项目答辩",
    "更新日程：答辩地点改为图书馆算了",
    "创建日程：明天3点开会，4点方案评审",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _ROUND14_INVALID_DIRECT_CASES)
async def test_round14_review_findings_fail_closed_without_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "拒绝提前一天提醒我",
        "不要，提前一天提醒我",
        "请老师提前一天提醒我",
        "大约将在3点",
        "预计，3点",
        "至迟3点",
        "明天3点/之前",
        "明天3点，请在之前",
        "明天3点最终到",
        "明天3点结束",
    ],
)
async def test_round14_context_findings_fail_closed_without_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        ("创建日程：明天3点晚间新闻讨论", "晚间新闻讨论"),
        ("创建日程：明天3点内务整理", "内务整理"),
        ("创建日程：明天3点前瞻论坛", "前瞻论坛"),
    ],
)
async def test_round14_relation_lexical_titles_remain_supported(
    text: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == expected_title
    assert result.slots.date == "2026-07-29"
    assert result.slots.start_time == "03:00"


@pytest.mark.asyncio
async def test_round14_dense_lexical_times_are_linear_and_bypass_llm() -> None:
    llm = RecordingMutationLlm()
    text = ("创建待办：" + "三点建议" * 2_500)[:9_900]

    started = perf_counter()
    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    elapsed = perf_counter() - started

    _assert_no_llm_unknown(result, llm)
    assert elapsed < 1.0


def _round15_metadata_llm(
    intent: IntentName,
    field: str,
    value: str,
) -> StaticMutationLlm:
    return StaticMutationLlm(
        '{"intent":"'
        + intent.value
        + '","confidence":0.99,"slots":{"'
        + field
        + '":"'
        + value
        + '"},"missing_fields":[],"ambiguities":[],"source_text":"ignored",'
        + '"requires_confirmation":true}'
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "field", "value"),
    [
        (
            "更新日程：答辩，地点改为图书馆",
            IntentName.UPDATE_EVENT,
            "location",
            "图书馆",
        ),
        (
            "更新日程：答辩，位置设置为图书馆",
            IntentName.UPDATE_EVENT,
            "location",
            "图书馆",
        ),
        (
            "更新日程：答辩，地点设成研发和测试实验室",
            IntentName.UPDATE_EVENT,
            "location",
            "研发和测试实验室",
        ),
        (
            "更新日程：答辩，地点改为“图书馆”",
            IntentName.UPDATE_EVENT,
            "location",
            "图书馆",
        ),
        (
            "更新日程：答辩，地点改为“咖啡”吧",
            IntentName.UPDATE_EVENT,
            "location",
            "咖啡",
        ),
        (
            "更新日程：答辩，地点改为咖啡吧",
            IntentName.UPDATE_EVENT,
            "location",
            "咖啡吧",
        ),
        (
            "更新日程：答辩，地点改为图书馆并且描述改为答辩",
            IntentName.UPDATE_EVENT,
            "location",
            "图书馆",
        ),
        (
            "更新任务：论文，课程设置为软件工程",
            IntentName.UPDATE_TASK,
            "course",
            "软件工程",
        ),
        (
            "更新任务：论文，备注更新为完成初稿",
            IntentName.UPDATE_TASK,
            "description",
            "完成初稿",
        ),
    ],
)
async def test_round15_update_metadata_requires_exact_explicit_assignment(
    text: str,
    intent: IntentName,
    field: str,
    value: str,
) -> None:
    llm = _round15_metadata_llm(intent, field, value)

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == intent
    assert getattr(result.slots, field) == value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "field", "candidate"),
    [
        (
            "创建日程：答辩，地点为图书馆东区",
            IntentName.CREATE_EVENT,
            "location",
            "图书馆",
        ),
        (
            "创建待办：论文，课程为软件工程",
            IntentName.CREATE_TASK,
            "course",
            "软件",
        ),
        (
            "更新日程：答辩，地点改为图书馆东区",
            IntentName.UPDATE_EVENT,
            "location",
            "图书馆",
        ),
        (
            "更新任务：论文，课程改为软件工程",
            IntentName.UPDATE_TASK,
            "course",
            "软件",
        ),
        (
            "更新日程：图书馆答辩，地点改为教室",
            IntentName.UPDATE_EVENT,
            "location",
            "图书馆",
        ),
        (
            "更新日程：答辩，描述改为图书馆",
            IntentName.UPDATE_EVENT,
            "location",
            "图书馆",
        ),
        (
            "更新日程：答辩，地点改为咖啡吧",
            IntentName.UPDATE_EVENT,
            "location",
            "咖啡",
        ),
        (
            "更新日程：答辩，地点改为“咖啡吧”",
            IntentName.UPDATE_EVENT,
            "location",
            "咖啡",
        ),
        (
            "更新任务：论文，课程改为哲学呢",
            IntentName.UPDATE_TASK,
            "course",
            "哲学",
        ),
    ],
)
async def test_round15_metadata_rejects_truncated_or_field_confused_values(
    text: str,
    intent: IntentName,
    field: str,
    candidate: str,
) -> None:
    llm = _round15_metadata_llm(intent, field, candidate)

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == intent
    assert getattr(result.slots, field) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_slots"),
    [
        (
            "更新任务：论文，把优先级改为高吧",
            {"title": "论文", "priority": "high"},
        ),
        (
            "更新任务：“论文”，把优先级改为高吧",
            {"title": "论文", "priority": "high"},
        ),
        (
            "更新任务：论文，并把优先级改为高",
            {"title": "论文", "priority": "high"},
        ),
        (
            "更新任务：论文，将状态改为完成",
            {"title": "论文", "status": "completed"},
        ),
        (
            "更新任务：“论文”，并将状态改为完成",
            {"title": "论文", "status": "completed"},
        ),
        (
            "更新任务：论文，把标题改为第二版",
            {"title": "论文", "new_title": "第二版"},
        ),
        (
            "更新任务：论文，把标题重命名为第二版",
            {"title": "论文", "new_title": "第二版"},
        ),
    ],
)
async def test_round15_explicit_targets_accept_scoped_object_markers(
    text: str,
    expected_slots: dict[str, str],
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.model_dump(exclude_none=True) == expected_slots


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：论文，把",
        "更新任务：论文，将",
        "删除任务：论文，把",
        "更新任务：论文，把，优先级改为高",
        "更新任务：论文，把算了",
        "更新任务：论文，把任务B优先级改为高",
        "更新任务：论文，把另一个任务状态改为完成",
        "更新任务：论文，将它状态改为完成",
        "更新任务：论文，把“报告”优先级改为高",
        "更新任务：论文，把优先级改为高，并删除任务B",
        "更新任务：论文，报告，把优先级改为高",
        "更新任务：论文，把改为高优先级",
        "更新任务：论文，把改为完成",
        "更新任务：论文，并把重命名为第二版",
        "更新任务：论文，把设置为高优先级",
        "更新任务：论文，将改为低优先级",
        "不要把地点改为图书馆",
    ],
)
async def test_round15_object_markers_do_not_hide_unsafe_targets_or_commands(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "value", "expected_priority"),
    [
        (
            "更新任务：论文，描述改为“方案A并且方案B”",
            "方案A并且方案B",
            None,
        ),
        (
            "更新任务：论文，备注改为“第一部分以及第二部分”",
            "第一部分以及第二部分",
            None,
        ),
        (
            "更新任务：论文，描述改为“方案A并且删除任务B”",
            "方案A并且删除任务B",
            None,
        ),
        (
            "更新任务：论文，描述改为“方案A并且方案B”，优先级改为高",
            "方案A并且方案B",
            "high",
        ),
    ],
)
async def test_round16_quoted_text_values_preserve_connectors(
    text: str,
    value: str,
    expected_priority: str | None,
) -> None:
    llm = _round15_metadata_llm(IntentName.UPDATE_TASK, "description", value)

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.description == value
    assert result.slots.priority == expected_priority


@pytest.mark.asyncio
async def test_round16_quoted_rename_disambiguates_terminal_particles() -> None:
    version = await IntentParser().parse(
        "更新任务：论文重命名为“第二版”吧",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    literal = await IntentParser().parse(
        "更新任务：论文重命名为“咖啡吧”",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert version.intent == IntentName.UPDATE_TASK
    assert version.slots.new_title == "第二版"
    assert literal.intent == IntentName.UPDATE_TASK
    assert literal.slots.new_title == "咖啡吧"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "更新任务：论文重命名为第二版吧",
        "更新任务：论文重命名为咖啡吧",
        "更新任务：论文重命名为最终稿呢",
        "更新任务：论文，描述改为方案A并且方案B",
        "更新任务：论文，备注改为第一部分以及第二部分",
    ],
)
async def test_round16_unquoted_particle_and_connector_values_fail_closed(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round17_dense_nominal_reminder_title_is_linear() -> None:
    prefix = "创建待办：研究"
    suffix = "提醒事项"
    target_length = 9_999
    text = prefix + "提醒" * ((target_length - len(prefix) - len(suffix)) // len("提醒")) + suffix

    started = perf_counter()
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    elapsed = perf_counter() - started

    assert len(text) == target_length
    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == text.removeprefix("创建待办：")
    assert elapsed < 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：交作业，从来没计划提前一天提醒我",
        "创建待办：交作业，从来没有计划提前一天提醒我",
        "创建待办：交作业，从未曾计划提前一天提醒我",
        "创建待办：交作业，未曾计划提前一天提醒我",
        "创建待办：交作业，压根没计划提前一天提醒我",
    ],
)
async def test_round18_reminder_planning_negations_fail_closed(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建日程：将近3点开会",
        "创建日程：至早3点开会",
        "创建日程：小于3点开会",
        "创建日程：大于3点开会",
        "创建日程：接近3点开会",
        "创建日程：接近于3点开会",
        "创建日程：临近3点开会",
        "创建日程：刚过3点开会",
        "创建日程：3点出头开会",
        "创建日程：3点来钟开会",
        "创建日程：3点刚过开会",
        "创建日程：差一点到3点开会",
        "创建日程：差点到3点开会",
    ],
)
async def test_round18_inexact_time_relations_fail_closed(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "完成任务A，然后",
        "完成任务A，接着",
        "完成任务A，再",
        "完成任务A，顺便",
        "完成任务A，并把",
        "完成任务A，随后",
        "完成任务A，之后",
        "完成任务A，以及",
        "完成任务A，且",
        "完成任务A，把",
        "完成任务A，将",
        "完成任务A，又",
        "完成任务A；然后",
        "完成任务A，然后。",
    ],
)
async def test_round18_direct_complete_dangling_tail_fails_closed(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round18_exact_time_reminder_and_complete_controls_remain_valid() -> None:
    parser = IntentParser()
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))

    reminder = await parser.parse("创建待办：交作业，计划提前一天提醒我", now=now)
    exact_time = await parser.parse("创建日程：3点开会", now=now)
    complete = await parser.parse("完成任务A", now=now)
    complete_with_priority = await parser.parse("完成任务A，把优先级改为高", now=now)

    assert reminder.intent == IntentName.CREATE_TASK
    assert reminder.slots.title == "交作业,计划"
    assert reminder.slots.reminder_minutes == 1_440
    assert exact_time.intent == IntentName.CREATE_EVENT
    assert exact_time.slots.title == "开会"
    assert exact_time.slots.start_time == "03:00"
    assert complete.intent == IntentName.UPDATE_TASK
    assert complete.slots.title == "A"
    assert complete.slots.status == "completed"
    assert complete_with_priority.intent == IntentName.UPDATE_TASK
    assert complete_with_priority.slots.title == "A"
    assert complete_with_priority.slots.priority == "high"
    assert complete_with_priority.slots.status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：交作业，避免提前一天提醒我",
        "创建待办：交作业，明确拒绝，提前一天提醒我",
        "创建待办：交作业，请老师，提前一天提醒我",
        "创建待办：交作业，从来未计划提前一天提醒我",
        "创建待办：交作业，尚未计划提前一天提醒我",
        "创建待办：交作业，并未计划提前一天提醒我",
        "创建待办：交作业，从未安排提前一天提醒我",
        "创建待办：交作业，未打算提前一天提醒我",
        "创建待办：交作业，从来没有安排提前一天提醒我",
    ],
)
async def test_round19_reminder_scope_and_negation_fails_closed(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建日程：大约要在3点开会",
        "创建日程：明天3点_之后开会",
        "创建日程：明天3点最好到",
        "创建日程：会议明天3点截止",
        "创建日程：会议明天3点下课",
        "创建日程：会议明天3点散会",
        "创建日程：靠近3点开会",
        "创建日程：快3点开会",
        "创建日程：约摸3点开会",
        "创建日程：估摸3点开会",
        "创建日程：明天3点冒头开会",
        "创建日程：明天3点刚刚过开会",
        "创建日程：大约\ufe0f在3点开会",
    ],
)
async def test_round19_temporal_semantic_variants_fail_closed(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "大约要在3点",
        "3点_之后",
        "3点最好到",
        "3点截止",
        "避免提前一天提醒我",
        "大约\ufe0f在3点",
    ],
)
async def test_round19_unsafe_context_variants_bypass_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "删除任务：A兼B",
        "删除俩任务",
        "更新任务：论文优先级指定为中",
        "更新日程：答辩地点移到图书馆",
        "更新日程：答辩地点改为图书馆作罢",
        "更新日程：答辩地点改为图书馆别改了",
        "更新任务：论文对报告优先级改为高",
        "更新任务：论文针对报告优先级改为高",
        "更新任务：论文向报告优先级改为高",
        "创建待办：A转而删除任务B",
        "创建待办：A转\ufe0f而删除任务B",
        "完成任务A，和",
        "完成任务A，与",
        "完成任务A，及",
        "完成任务A、",
    ],
)
async def test_round19_structural_mutation_variants_fail_closed(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


def _round19_metadata_cases() -> list[tuple[str, IntentName, str, str]]:
    return [
        (
            "更新日程：答辩，地点改为图书馆并且描述改为项目答辩",
            IntentName.UPDATE_EVENT,
            "location",
            "图书馆并且描述改为项目答辩",
        ),
        (
            "更新任务：论文，课程改为软件工程并且描述改为完成初稿",
            IntentName.UPDATE_TASK,
            "course",
            "软件工程并且描述改为完成初稿",
        ),
        (
            "更新任务：论文，描述改为第一部分并且课程改为软件工程",
            IntentName.UPDATE_TASK,
            "description",
            "第一部分并且课程改为软件工程",
        ),
        (
            "更新日程：答辩，地点改为“图书馆”并且描述改为项目答辩",
            IntentName.UPDATE_EVENT,
            "location",
            "“图书馆”并且描述改为项目答辩",
        ),
        (
            "更新日程：答辩，地点改为“图书馆”并且描述改为项目答辩",
            IntentName.UPDATE_EVENT,
            "location",
            "图书馆”并且描述改为项目答辩",
        ),
        (
            "更新日程：答辩，地点改为“图书馆”",
            IntentName.UPDATE_EVENT,
            "location",
            "“图书馆”",
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "field", "candidate"),
    _round19_metadata_cases(),
)
async def test_round19_metadata_rejects_structural_overcapture_and_quotes(
    text: str,
    intent: IntentName,
    field: str,
    candidate: str,
) -> None:
    llm = _round15_metadata_llm(intent, field, candidate)

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == intent
    assert getattr(result.slots, field) is None


@pytest.mark.asyncio
async def test_round19_structural_controls_remain_valid() -> None:
    parser = IntentParser()
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))

    reminder = await parser.parse("创建待办：交作业，提前一天提醒我", now=now)
    exact_time = await parser.parse("创建日程：明天3点开会", now=now)
    report_time = await parser.parse("创建日程：明天3点报到", now=now)
    update = await parser.parse("更新任务：论文，优先级改为高", now=now)
    ordinary_title = await parser.parse("创建待办：A转而思考", now=now)

    assert reminder.intent == IntentName.CREATE_TASK
    assert reminder.slots.reminder_minutes == 1_440
    assert exact_time.intent == IntentName.CREATE_EVENT
    assert exact_time.slots.start_time == "03:00"
    assert report_time.intent == IntentName.CREATE_EVENT
    assert report_time.slots.title == "报到"
    assert report_time.slots.start_time == "03:00"
    assert update.intent == IntentName.UPDATE_TASK
    assert update.slots.title == "论文"
    assert update.slots.priority == "high"
    assert ordinary_title.intent == IntentName.CREATE_TASK
    assert ordinary_title.slots.title == "A转而思考"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：交作业，明确不需要，提前一天提醒我",
        "创建待办：交作业，没有必要，提前一天提醒我",
        "创建待办：交作业，未要求，提前一天提醒我",
        "创建待办：交作业，并非需要，提前一天提醒我",
        "创建日程：近3点开会",
        "创建日程：差点儿3点开会",
        "创建日程：明天3点开外开会",
        "创建日程：明天3点稍过开会",
        "完成任务A，跟",
        "完成任务A，同",
        "完成任务A，还有",
        "完成任务A，或",
        "完成任务A，或者",
        "删除任务：A暨B",
        "更新任务：论文给报告优先级改为高",
    ],
)
async def test_round20_adjacent_structural_boundaries_fail_closed(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["近3点", "差点儿3点", "3点开外", "3点稍过"])
async def test_round20_adjacent_temporal_context_bypasses_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "intent", "value"),
    [
        (
            "更新任务：“论文”，描述改为“优先级改为高”",
            IntentName.UPDATE_TASK,
            "优先级改为高",
        ),
        (
            "更新任务：“论文”，备注改为“状态改为完成”",
            IntentName.UPDATE_TASK,
            "状态改为完成",
        ),
        (
            "更新日程：“答辩”，描述改为“地点改为图书馆”",
            IntentName.UPDATE_EVENT,
            "地点改为图书馆",
        ),
    ],
)
async def test_round21_fully_quoted_embedded_field_text_is_preserved(
    text: str,
    intent: IntentName,
    value: str,
) -> None:
    llm = _round15_metadata_llm(intent, "description", value)

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == intent
    assert result.slots.description == value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：交作业，没有必要了，提前一天提醒我",
        "创建待办：交作业，明确拒绝了，提前一天提醒我",
        "更新日程：答辩地点迁到图书馆",
        "更新日程：答辩位置转移到图书馆",
        "更新任务：论文拿报告优先级改为高",
        "更新任务：论文用报告状态改为完成",
    ],
)
async def test_round22_aspect_operator_and_object_variants_fail_closed(
    text: str,
) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：交作业，省去提前一天提醒我",
        "创建待办：交作业，叫小王提前一天提醒我",
        "创建日程：近乎3点开会",
        "创建日程：明天3点冒个头开会",
        "创建日程：明天3点的之后开会",
        "创建日程：明天3点最后到",
        "创建日程：报名明天3点截至",
        "删除任务：A加上B",
        "删除俩任务",
        "更新任务：论文状态变更为处理中",
        "更新日程：答辩描述改为初稿取消修改",
        "更新任务：论文替报告状态改为完成",
        "创建待办：A继而撤掉任务B",
    ],
)
async def test_round23_fuzz_root_variants_fail_closed(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["近乎3点", "3点冒个头", "3点的之后", "3点最后到", "3点截至"],
)
async def test_round23_fuzz_temporal_context_variants_bypass_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round23_fullwidth_quoted_metadata_candidate_is_rejected() -> None:
    text = "更新日程：答辩，地点改为＂图书馆＂"
    llm = _round15_metadata_llm(
        IntentName.UPDATE_EVENT,
        "location",
        "＂图书馆＂",
    )

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UPDATE_EVENT
    assert result.slots.location is None


@pytest.mark.asyncio
async def test_round23_fullwidth_quoted_metadata_content_remains_grounded() -> None:
    text = "更新日程：答辩，地点改为＂图书馆＂"
    llm = _round15_metadata_llm(
        IntentName.UPDATE_EVENT,
        "location",
        "图书馆",
    )

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UPDATE_EVENT
    assert result.slots.location == "图书馆"


@pytest.mark.asyncio
async def test_round23_direct_withdraw_synonym_remains_explicit() -> None:
    result = await IntentParser().parse(
        "撤掉任务B",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.DELETE_TASK
    assert result.slots.title == "B"


_ROUND24_INVALID_DIRECT_CASES = (
    "创建待办：整理三点诉求",
    "创建待办：梳理三点任务",
    "创建待办：记录两点需求",
    "创建待办：预计要到明天交作业",
    "创建日程：明天3点稍微过开会",
    "创建日程：明天3点再之后开会",
    "创建日程：明天3点尽快到",
    "创建日程：展览明天3点闭幕",
    "创建待办：交作业，免了提前一天提醒我",
    "创建待办：交作业，免了，提前一天提醒我",
    "创建待办：交作业，托小王提前一天提醒我",
    "创建待办：记录作业然后提醒系统",
    "删除任务：A外加B",
    "删除任务A外加B",
    "更新任务：论文课程换成软件工程",
    "更新任务：论文课程换为软件工程",
    "更新任务：论文描述改为初稿撤回修改",
    "更新任务：论文把报告优先级改为高",
    "更新任务：论文将报告状态改为完成",
    "更新任务：把论文优先级改为高",
    "创建待办：A之后清除任务B",
    "创建待办：A外加清除任务B",
    "清除任务B",
    "删除任务：«A»外加«B»",
    "更新任务：«论文»把«报告»优先级改为高",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _ROUND24_INVALID_DIRECT_CASES)
async def test_round24_fuzz_roots_fail_closed_without_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "整理三点诉求",
        "预计要到明天",
        "3点稍微过",
        "3点再之后",
        "3点尽快到",
        "明天3点闭幕",
        "免了提前一天提醒我",
        "免了，提前一天提醒我",
        "托小王提前一天提醒我",
        "记录作业然后提醒系统",
    ],
)
async def test_round24_contextual_fuzz_roots_bypass_llm(text: str) -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        text,
        context=["创建日程：项目答辩"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


_ROUND24_QUOTE_PAIRS = (
    ("«", "»"),
    ("‹", "›"),
    ("〝", "〞"),
    ("〈", "〉"),
    ("【", "】"),
    ("`", "`"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("opener", "closer"), _ROUND24_QUOTE_PAIRS)
async def test_round24_additional_quote_pairs_are_atomic(opener: str, closer: str) -> None:
    parser = IntentParser()
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))

    deleted = await parser.parse(f"删除任务：{opener}A外加B{closer}", now=now)
    updated = await parser.parse(
        f"更新任务：{opener}论文把报告{closer}，优先级改为高",
        now=now,
    )

    assert deleted.intent == IntentName.DELETE_TASK
    assert deleted.slots.title == "A外加B"
    assert updated.intent == IntentName.UPDATE_TASK
    assert updated.slots.title == "论文把报告"
    assert updated.slots.priority == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize(("opener", "closer"), _ROUND24_QUOTE_PAIRS)
async def test_round24_additional_quote_pairs_ground_clean_metadata_only(
    opener: str,
    closer: str,
) -> None:
    text = f"更新日程：答辩，地点改为{opener}图书馆{closer}"
    polluted_llm = _round15_metadata_llm(
        IntentName.UPDATE_EVENT,
        "location",
        f"{opener}图书馆{closer}",
    )
    clean_llm = _round15_metadata_llm(IntentName.UPDATE_EVENT, "location", "图书馆")
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))

    polluted = await IntentParser(polluted_llm).parse(text, now=now)
    clean = await IntentParser(clean_llm).parse(text, now=now)

    assert polluted_llm.extract_calls == 1
    assert polluted.slots.location is None
    assert clean_llm.extract_calls == 1
    assert clean.slots.location == "图书馆"


@pytest.mark.asyncio
@pytest.mark.parametrize(("opener", "closer"), _ROUND24_QUOTE_PAIRS)
async def test_round24_unbalanced_additional_quotes_fail_closed_without_llm(
    opener: str,
    closer: str,
) -> None:
    for text in (f"删除任务：{opener}A", f"删除任务：A{closer}"):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(
            text,
            now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
        )
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round24_temporal_and_reminder_controls_remain_supported() -> None:
    parser = IntentParser()
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    temporal_cases = (
        ("创建日程：明天3点诉求分析", "诉求分析"),
        ("创建日程：明天3点任务评审", "任务评审"),
        ("创建日程：明天3点需求讨论", "需求讨论"),
        ("创建日程：明天大约3点开会", "开会"),
        ("创建日程：明天3点稍微讨论", "稍微讨论"),
        ("创建日程：明天3点再讨论之后安排", "再讨论之后安排"),
        ("创建日程：明天3点尽快讨论", "尽快讨论"),
        ("创建日程：明天3点报到", "报到"),
        ("创建日程：明天3点闭幕式彩排", "闭幕式彩排"),
    )

    for text, expected_title in temporal_cases:
        result = await parser.parse(text, now=now)
        assert result.intent == IntentName.CREATE_EVENT
        assert result.slots.title == expected_title
        assert result.slots.date == "2026-07-29"
        assert result.slots.start_time == "03:00"

    reminder_titles = (
        "优化通知服务",
        "设计提醒系统",
        "编写通知模板",
        "记录提醒事项",
    )
    for title in reminder_titles:
        result = await parser.parse(f"创建待办：{title}", now=now)
        assert result.intent == IntentName.CREATE_TASK
        assert result.slots.title == title
        assert result.slots.reminder_minutes is None

    for text in (
        "创建待办：交作业，请你提前一天提醒我",
        "创建待办：交作业，不要忘记提前一天提醒我",
    ):
        result = await parser.parse(text, now=now)
        assert result.intent == IntentName.CREATE_TASK
        assert result.slots.title == "交作业"
        assert result.slots.reminder_minutes == 1_440


@pytest.mark.asyncio
async def test_round24_structural_controls_remain_supported() -> None:
    parser = IntentParser()
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))

    priority = await parser.parse("更新任务：论文，把优先级改为高", now=now)
    object_first = await parser.parse("把任务论文优先级改为高", now=now)
    quoted_assignment = await parser.parse(
        "更新任务：“课程换成软件工程”，优先级改为高",
        now=now,
    )
    quoted_abort = await parser.parse(
        "更新任务：论文，描述改为“初稿撤回修改”",
        now=now,
    )
    safety_title = await parser.parse("创建待办：研究清除任务的方法", now=now)
    lexical_target = await parser.parse("删除任务：“课外加练”", now=now)

    assert priority.intent == IntentName.UPDATE_TASK
    assert priority.slots.title == "论文"
    assert priority.slots.priority == "high"
    assert object_first.intent == IntentName.UPDATE_TASK
    assert object_first.slots.title == "论文"
    assert object_first.slots.priority == "high"
    assert quoted_assignment.intent == IntentName.UPDATE_TASK
    assert quoted_assignment.slots.title == "课程换成软件工程"
    assert quoted_assignment.slots.priority == "high"
    assert quoted_abort.intent == IntentName.UPDATE_TASK
    assert quoted_abort.slots.title == "论文"
    assert safety_title.intent == IntentName.CREATE_TASK
    assert safety_title.slots.title == "研究清除任务的方法"
    assert lexical_target.intent == IntentName.DELETE_TASK
    assert lexical_target.slots.title == "课外加练"


_ROUND25_TEMPORAL_UNSAFE_CASES = (
    ("创建待办：整理明天的三点诉求", "整理明天的三点诉求"),
    ("创建待办：梳理明天三点任务", "梳理明天三点任务"),
    ("创建待办：整理三到四点诉求", "整理三到四点诉求"),
    ("创建待办：记录两至三点需求", "记录两至三点需求"),
    ("创建待办：梳理3-4点任务", "梳理3-4点任务"),
    ("创建待办：整理下午三点诉求", "整理下午三点诉求"),
    ("创建日程：大约是在明天3点开会", "大约是在明天3点"),
    ("创建日程：预计还要到明天3点开会", "预计还要到明天3点"),
    ("创建日程：预计仍要到明天3点开会", "预计仍要到明天3点"),
    ("创建日程：约摸着明天3点开会", "约摸着明天3点"),
    ("创建日程：预估也要到明天3点开会", "预估也要到明天3点"),
    ("创建日程：大约是在3点开会", "大约是在3点"),
    ("创建日程：明天3点往后开会", "3点往后"),
    ("创建日程：明天3点往前开会", "3点往前"),
    ("创建日程：明天3点再往后开会", "3点再往后"),
    ("创建日程：明天往后开会", "明天往后"),
    ("创建日程：明天3点然后再之后开会", "3点然后再之后"),
    ("创建日程：明天3点稍稍过开会", "3点稍稍过"),
    ("创建日程：明天3点微微过开会", "3点微微过"),
    ("创建日程：明天3点略略过开会", "3点略略过"),
    ("创建日程：明天3点赶快到", "3点赶快到"),
    ("创建日程：明天3点快点到", "3点快点到"),
    ("创建日程：明天3点迅速到", "3点迅速到"),
    ("创建日程：明天3点尽速到", "3点尽速到"),
    ("创建日程：明天3点务必赶到", "3点务必赶到"),
    ("创建日程：明天3点得赶到", "3点得赶到"),
    ("创建日程：展览明天3点落幕", "明天3点落幕"),
    ("创建日程：会议明天3点闭会", "明天3点闭会"),
    ("创建日程：活动明天3点收官", "明天3点收官"),
    ("创建日程：演出明天3点谢幕", "明天3点谢幕"),
    ("创建日程：比赛明天3点终场", "明天3点终场"),
    ("创建日程：课程明天3点完结", "明天3点完结"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND25_TEMPORAL_UNSAFE_CASES)
async def test_round25_temporal_roots_fail_closed_direct_and_context_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(direct_text, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        context_text,
        context=["创建日程：项目答辩"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        ("创建日程：会议3点开始", "会议开始"),
        ("创建日程：项目答辩3点开始", "项目答辩开始"),
        ("创建日程：请3点开会", "请开会"),
        ("创建日程：安排3点开会", "安排开会"),
        ("创建待办：作业3点提交", "作业提交"),
        ("创建日程：明天3点任务评审", "任务评审"),
        ("创建日程：会议3点图书馆见", "会议图书馆见"),
        ("创建日程：项目答辩3点图书馆", "项目答辩图书馆"),
    ],
)
async def test_round25_bare_time_controls_are_restored(
    text: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent in {IntentName.CREATE_TASK, IntentName.CREATE_EVENT}
    assert result.slots.title == expected_title
    assert result.slots.start_time == "03:00" or result.slots.due_time == "03:00"


_ROUND25_UNSAFE_REMINDER_CASES = (
    ("创建待办：交作业，免得提前一天提醒我", "免得提前一天提醒我"),
    ("创建待办：交作业，省得提前一天提醒我", "省得提前一天提醒我"),
    ("创建待办：交作业，甭提前一天提醒我", "甭提前一天提醒我"),
    ("创建待办：交作业，毋须提前一天提醒我", "毋须提前一天提醒我"),
    ("创建待办：交作业，莫要提前一天提醒我", "莫要提前一天提醒我"),
    ("创建待办：交作业，莫再提前一天提醒我", "莫再提前一天提醒我"),
    ("创建待办：交作业，劳烦小王提前一天提醒我", "劳烦小王提前一天提醒我"),
    ("创建待办：交作业，劳驾小王提前一天提醒我", "劳驾小王提前一天提醒我"),
    ("创建待办：交作业，嘱咐小王提前一天提醒我", "嘱咐小王提前一天提醒我"),
    ("创建待办：交作业，交给小王提前一天提醒我", "交给小王提前一天提醒我"),
    ("创建待办：交作业，指派小王提前一天提醒我", "指派小王提前一天提醒我"),
    ("创建待办：交作业，求小王提前一天提醒我", "求小王提前一天提醒我"),
    ("创建待办：记录作业而后提醒系统", "记录作业而后提醒系统"),
    ("创建待办：记录作业稍后提醒系统", "记录作业稍后提醒系统"),
    ("创建待办：记录作业过后提醒系统", "记录作业过后提醒系统"),
    ("创建待办：记录作业末了提醒系统", "记录作业末了提醒系统"),
    ("创建待办：记录作业待会提醒系统", "记录作业待会提醒系统"),
    ("创建待办：记录作业待会儿提醒系统", "记录作业待会儿提醒系统"),
    ("创建待办：记录作业并提醒系统", "记录作业并提醒系统"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND25_UNSAFE_REMINDER_CASES)
async def test_round25_reminder_roots_fail_closed_direct_and_context_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(direct_text, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        context_text,
        context=["创建待办：交作业"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        ("创建待办：拜访老师，提前一天提醒我", "拜访老师"),
        ("创建待办：交作业，劳烦你提前一天提醒我", "交作业"),
        ("创建待办：交作业，劳驾你提前一天提醒我", "交作业"),
        ("创建待办：交作业，求你提前一天提醒我", "交作业"),
        ("创建待办：设计并实现提醒系统", "设计并实现提醒系统"),
        ("创建待办：研究合并提醒系统", "研究合并提醒系统"),
        ("创建待办：整理需求说明，提前一天提醒我", "整理需求说明"),
    ],
)
async def test_round25_reminder_controls_remain_supported(
    text: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == expected_title
    if "提前一天" in text:
        assert result.slots.reminder_minutes == 1_440
    else:
        assert result.slots.reminder_minutes is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  提前一天提醒我", "  "),
        ("再再请你记得提前一天提醒我", ""),
        ("务必务必提前一天提醒我", "务必"),
        ("提前一天提醒我再提前两天提醒我", "提前一天提醒我"),
        ("A然后再请你提前一天提醒我。B", "A。B"),
    ],
)
def test_round25_linear_reminder_strip_preserves_behavior(text: str, expected: str) -> None:
    assert _without_reminder_phrases(text) == expected


@pytest.mark.asyncio
async def test_round25_adversarial_temporal_scans_remain_linear() -> None:
    texts = (
        ("创建日程：明天3点" + "再" * 10_000)[:9_998] + "之后",
        ("创建待办：交作业，提前一天提醒我" + "再" * 10_000)[:10_000],
    )
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))

    for text in texts:
        llm = RecordingMutationLlm()
        started = perf_counter()
        result = await IntentParser(llm).parse(text, now=now)
        elapsed = perf_counter() - started
        _assert_no_llm_unknown(result, llm)
        assert elapsed < 1.0


_ROUND25_SAFETY_ONLY_SIGNALS = ("清空", "作废", "抹掉", "注销", "销毁")


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", _ROUND25_SAFETY_ONLY_SIGNALS)
async def test_round25_unsupported_mutation_synonyms_fail_closed_without_llm(
    signal: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    mixed_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    mixed = await IntentParser(mixed_llm).parse(
        f"创建待办：A之后{signal}任务B",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        f"{signal}任务B",
        context=["创建待办：A"],
        now=now,
    )

    _assert_no_llm_unknown(mixed, mixed_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
async def test_round25_new_target_connector_exposes_unsupported_mutation_without_llm() -> None:
    llm = RecordingMutationLlm()

    result = await IntentParser(llm).parse(
        "创建待办：A外带清空任务B",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", _ROUND25_SAFETY_ONLY_SIGNALS)
async def test_round25_unsupported_mutation_words_remain_valid_in_meta_titles(
    signal: str,
) -> None:
    result = await IntentParser().parse(
        f"创建待办：研究{signal}任务的方法",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == f"研究{signal}任务的方法"


_ROUND25_ABORT_PHRASES = (
    "撤回这次修改",
    "取消这次修改",
    "收回修改",
    "当我没说",
    "当我没说过",
    "不作数了",
    "还是算了",
    "那就算了",
    "就当我没说",
    "先不作数了",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND25_ABORT_PHRASES)
async def test_round25_abort_variants_fail_closed_direct_and_context_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"更新任务：论文描述改为初稿{phrase}",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["更新任务：论文"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND25_ABORT_PHRASES)
async def test_round25_abort_text_inside_quotes_remains_metadata(phrase: str) -> None:
    text = f"更新任务：论文，描述改为“{phrase}”"
    llm = _round15_metadata_llm(IntentName.UPDATE_TASK, "description", phrase)

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.description == phrase


_ROUND25_AMBIGUOUS_TARGET_CASES = (
    ("更新任务：论文让报告优先级改为高", "论文让报告优先级改为高", "更新任务：论文"),
    ("更新任务：论文令报告优先级改为高", "论文令报告优先级改为高", "更新任务：论文"),
    ("更新任务：论文为报告优先级改为高", "论文为报告优先级改为高", "更新任务：论文"),
    ("更新任务：论文帮报告优先级改为高", "论文帮报告优先级改为高", "更新任务：论文"),
    ("删除任务：A另加B", "A另加B", "删除任务：A"),
    ("删除任务：A再加B", "A再加B", "删除任务：A"),
    ("删除任务：A外带B", "A外带B", "删除任务：A"),
    ("删除任务：A连带B", "A连带B", "删除任务：A"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direct_text", "context_text", "previous_text"),
    _ROUND25_AMBIGUOUS_TARGET_CASES,
)
async def test_round25_ambiguous_target_variants_fail_closed_direct_and_context_without_llm(
    direct_text: str,
    context_text: str,
    previous_text: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(direct_text, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        context_text,
        context=[previous_text],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
async def test_round25_conditional_target_controls_remain_supported() -> None:
    parser = IntentParser()
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    cases = (
        (
            "更新任务：论文为人民服务，优先级改为高",
            IntentName.UPDATE_TASK,
            "论文为人民服务",
        ),
        ("更新任务：让步方案，优先级改为高", IntentName.UPDATE_TASK, "让步方案"),
        (
            "更新任务：以论文为主题的报告，优先级改为高",
            IntentName.UPDATE_TASK,
            "以论文为主题的报告",
        ),
        ("更新任务：转让报告，优先级改为高", IntentName.UPDATE_TASK, "转让报告"),
        ("更新任务：帮扶报告，优先级改为高", IntentName.UPDATE_TASK, "帮扶报告"),
        ("更新任务：命令报告，优先级改为高", IntentName.UPDATE_TASK, "命令报告"),
        ("删除任务：A连带责任", IntentName.DELETE_TASK, "A连带责任"),
        ("删除任务：“课外带练”", IntentName.DELETE_TASK, "课外带练"),
        ("删除任务：“A另加B”", IntentName.DELETE_TASK, "A另加B"),
        ("创建待办：论文外带报告", IntentName.CREATE_TASK, "论文外带报告"),
        ("删除任务：“论文外带报告”", IntentName.DELETE_TASK, "论文外带报告"),
        ("创建待办：撤回这次修改方案", IntentName.CREATE_TASK, "撤回这次修改方案"),
    )

    for text, expected_intent, expected_title in cases:
        result = await parser.parse(text, now=now)
        assert result.intent == expected_intent
        assert result.slots.title == expected_title


_ROUND25_QUOTE_PAIRS = (
    ("〔", "〕"),
    ("〖", "〗"),
    ("〘", "〙"),
    ("〚", "〛"),
    ("⟪", "⟫"),
    ("⦅", "⦆"),
    ("❝", "❞"),
    ("„", "“"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("opener", "closer"), _ROUND25_QUOTE_PAIRS)
async def test_round25_additional_quote_pairs_are_atomic_and_fail_closed_when_unbalanced(
    opener: str,
    closer: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    atomic = await IntentParser().parse(f"删除任务：{opener}A另加B{closer}", now=now)

    assert atomic.intent == IntentName.DELETE_TASK
    assert atomic.slots.title == "A另加B"
    for text in (f"删除任务：{opener}A", f"删除任务：A{closer}"):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(("opener", "closer"), _ROUND25_QUOTE_PAIRS)
async def test_round25_additional_quote_pairs_ground_clean_metadata_only(
    opener: str,
    closer: str,
) -> None:
    text = f"更新日程：答辩，地点改为{opener}图书馆{closer}"
    polluted_llm = _round15_metadata_llm(
        IntentName.UPDATE_EVENT,
        "location",
        f"{opener}图书馆{closer}",
    )
    clean_llm = _round15_metadata_llm(IntentName.UPDATE_EVENT, "location", "图书馆")
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))

    polluted = await IntentParser(polluted_llm).parse(text, now=now)
    clean = await IntentParser(clean_llm).parse(text, now=now)

    assert polluted_llm.extract_calls == 1
    assert polluted.slots.location is None
    assert clean_llm.extract_calls == 1
    assert clean.slots.location == "图书馆"


@pytest.mark.asyncio
async def test_round25_nested_cross_pair_quotes_are_atomic_and_ground_clean_metadata() -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    deleted = await IntentParser().parse("删除任务：«「A另加B」»", now=now)
    text = "更新日程：答辩，地点改为«「图书馆」»"
    clean_llm = _round15_metadata_llm(IntentName.UPDATE_EVENT, "location", "图书馆")
    wrapped_llm = _round15_metadata_llm(IntentName.UPDATE_EVENT, "location", "«「图书馆」»")

    clean = await IntentParser(clean_llm).parse(text, now=now)
    wrapped = await IntentParser(wrapped_llm).parse(text, now=now)

    assert deleted.intent == IntentName.DELETE_TASK
    assert deleted.slots.title == "A另加B"
    assert clean.slots.location == "图书馆"
    assert wrapped.slots.location is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidate",
    ["图 书 馆", "图\u00a0书馆", "图\u3000书馆", "图\u0301书馆", "图\u200b书馆"],
)
async def test_round25_metadata_normalization_injections_are_rejected(
    candidate: str,
) -> None:
    text = "更新日程：答辩，地点改为图书馆"
    llm = _round15_metadata_llm(IntentName.UPDATE_EVENT, "location", candidate)

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UPDATE_EVENT
    assert result.slots.location is None


@pytest.mark.asyncio
async def test_round25_exact_source_spacing_remains_grounded() -> None:
    text = "更新日程：答辩，地点改为图 书 馆"
    llm = _round15_metadata_llm(IntentName.UPDATE_EVENT, "location", "图 书 馆")

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert result.intent == IntentName.UPDATE_EVENT
    assert result.slots.location == "图 书 馆"


_ROUND26_END_ONLY_ROOTS = (
    "3点散场",
    "3点结课",
    "3点打烊",
    "3点完毕",
    "3点告终",
    "3点退场",
    "3点散席",
    "3点闭店",
    "3点停业",
    "3点休会",
    "3点完赛",
    "3点赛毕",
    "3点收场",
    "3点终了",
    "3点闭市",
    "3点停赛",
    "3点停演",
    "3点收尾",
    "3点完工",
    "3点竣工",
    "3点歇业",
    "3点闭业",
    "3点停课",
    "3点停工",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("root", _ROUND26_END_ONLY_ROOTS)
async def test_round26_end_only_synonyms_fail_closed_direct_and_context_without_llm(
    root: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"创建日程：活动明天{root}",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        f"明天{root}",
        context=["创建日程：项目答辩"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "candidate"),
    [
        ("更新日程：答辩，地点改为room101", "ROOM101"),
        ("更新日程：答辩，地点改为Straße", "STRASSE"),
    ],
)
async def test_round26_casefold_only_metadata_candidates_are_rejected(
    text: str,
    candidate: str,
) -> None:
    llm = _round15_metadata_llm(IntentName.UPDATE_EVENT, "location", candidate)

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UPDATE_EVENT
    assert result.slots.location is None


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["room101", "Straße"])
async def test_round26_exact_latin_metadata_candidates_remain_grounded(
    value: str,
) -> None:
    text = f"更新日程：答辩，地点改为{value}"
    llm = _round15_metadata_llm(IntentName.UPDATE_EVENT, "location", value)

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert result.intent == IntentName.UPDATE_EVENT
    assert result.slots.location == value


_ROUND26_NEW_TEMPORAL_UNSAFE_CASES = (
    ("创建日程：明天夜晚8点开会", "明天夜晚8点"),
    ("创建日程：明天晚8点开会", "明天晚8点"),
    ("创建日程：明天夜间8点开会", "明天夜间8点"),
    ("创建日程：明天傍晚6点开会", "明天傍晚6点"),
    ("创建日程：明天3点朝前开会", "明天3点朝前"),
    ("创建日程：明天3点朝后开会", "明天3点朝后"),
    ("创建日程：明天3点赶忙到", "明天3点赶忙到"),
    ("创建日程：明天3点赶着到", "明天3点赶着到"),
    ("创建日程：明天3点火速到", "明天3点火速到"),
    ("创建日程：明天3点快速到", "明天3点快速到"),
    ("创建日程：明天3点快些到", "明天3点快些到"),
    ("创建日程：明天3点早点到", "明天3点早点到"),
    ("创建日程：明天3点直到", "明天3点直到"),
    ("创建日程：明天3点直至", "明天3点直至"),
    ("创建日程：明天3点持续到", "明天3点持续到"),
    ("创建日程：明天3点持续至", "明天3点持续至"),
    ("创建日程：明天3点延续到", "明天3点延续到"),
    ("创建日程：明天3点延续至", "明天3点延续至"),
    ("创建日程：明天3点开始到", "明天3点开始到"),
    ("创建日程：明天3点开始至", "明天3点开始至"),
    ("创建日程：明天3点起到", "明天3点起到"),
    ("创建日程：明天3点起至", "明天3点起至"),
    ("创建日程：明天3点过5分开会", "明天3点过5分"),
    ("创建日程：明天3点过五分开会", "明天3点过五分"),
    ("创建日程：明天3点差一刻开会", "明天3点差一刻"),
    ("创建日程：明天差一刻3点开会", "明天差一刻3点"),
    ("创建日程：明天3点又5分开会", "明天3点又5分"),
    ("创建日程：明天3点过几分开会", "明天3点过几分"),
    ("创建日程：明天3点过一会开会", "明天3点过一会"),
    ("创建日程：明天3点过一会儿开会", "明天3点过一会儿"),
    ("创建日程：明天3点过半小时开会", "明天3点过半小时"),
    ("创建日程：3点过半个小时开会", "3点过半个小时"),
    ("创建日程：3点差半小时开会", "3点差半小时"),
    ("创建日程：明天差半小时3点开会", "明天差半小时3点"),
    ("创建日程：3点又一刻开会", "3点又一刻"),
    ("创建日程：3点又半小时开会", "3点又半小时"),
    ("创建日程：3点过半刻开会", "3点过半刻"),
    ("创建日程：3点差半刻开会", "3点差半刻"),
    ("创建日程：差十分钟3点开会", "差十分钟3点"),
    ("创建日程：明天夜半12点开会", "明天夜半12点"),
    ("创建日程：夜半8点开会", "夜半8点"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND26_NEW_TEMPORAL_UNSAFE_CASES)
async def test_round26_new_temporal_roots_fail_closed_direct_and_context(
    direct_text: str,
    context_text: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(direct_text, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        context_text,
        context=["创建日程：项目答辩"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("root", ["3点闭展", "3点闭园", "3点关馆", "3点闭门"])
async def test_round26_additional_end_only_roots_fail_closed(root: str) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建日程：活动明天{root}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        f"明天{root}",
        context=["创建日程：项目答辩"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


_ROUND26_LEXICAL_POINT_ENUMERATION_NOUNS = (
    "事项",
    "目标",
    "步骤",
    "结果",
    "成果",
    "变化",
    "疑问",
    "材料",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("noun", _ROUND26_LEXICAL_POINT_ENUMERATION_NOUNS)
async def test_round26_lexical_point_nouns_do_not_silently_schedule_at_three(
    noun: str,
) -> None:
    text = f"整理明天的三点{noun}"
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建待办：{text}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        text,
        context=["创建待办：项目材料"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


_ROUND26_LEXICAL_POINT_COMPOUND_TITLES = (
    "研究三点式安全带",
    "学习三点水写法",
    "研究三点透视",
    "实现三点定位",
    "计算三点估计",
    "证明三点共线",
    "学习三点法",
    "检查三点支撑",
    "完成三点测量",
    "制作三点图",
    "分析三点数据",
    "记录三点坐标",
    "绘制三点曲线",
    "制作三点示意图",
    "完成三点校准",
    "执行三点采样",
    "绘制三点连线",
    "研究三点定理",
    "建立三点模型",
    "开展三点测试",
    "检查三点标记",
    "计算三点距离",
    "分析三点关系",
    "记录三点位置",
    "设计三点布局",
    "验证三点算法",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("title", _ROUND26_LEXICAL_POINT_COMPOUND_TITLES)
async def test_round26_lexical_point_compounds_fail_closed_instead_of_scheduling(
    title: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建待办：{title}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        title,
        context=["创建待办：安全研究"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
async def test_round26_temporal_false_positive_controls_remain_supported() -> None:
    parser = IntentParser()
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))

    evening = await parser.parse("创建日程：明天晚上8点开会", now=now)
    arrival = await parser.parse("创建日程：明天3点开始到场", now=now)
    early_discussion = await parser.parse("创建日程：明天3点早点讨论", now=now)

    assert evening.intent == IntentName.CREATE_EVENT
    assert evening.slots.start_time == "20:00"
    assert arrival.intent == IntentName.CREATE_EVENT
    assert arrival.slots.start_time == "03:00"
    assert arrival.slots.title == "开始到场"
    assert early_discussion.intent == IntentName.CREATE_EVENT
    assert early_discussion.slots.start_time == "03:00"
    assert early_discussion.slots.title == "早点讨论"


@pytest.mark.asyncio
@pytest.mark.parametrize("unit", ["3", "三"])
async def test_round26_long_hour_tokens_remain_under_one_second(unit: str) -> None:
    text = ("创建日程：" + unit * 10_000)[:9_997] + "点"
    llm = RecordingMutationLlm()
    started = perf_counter()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    elapsed = perf_counter() - started
    _assert_no_llm_unknown(result, llm)
    assert elapsed < 1.0


@pytest.mark.asyncio
async def test_round26_long_period_tokens_remain_under_one_second() -> None:
    text = ("创建日程：" + "下午" * 5_000)[:9_998]
    llm = RecordingMutationLlm()
    started = perf_counter()

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    elapsed = perf_counter() - started
    _assert_no_llm_unknown(result, llm)
    assert elapsed < 1.0


_ROUND26_UNSAFE_REMINDER_ROOTS = (
    "毋需提前一天提醒我",
    "免予提前一天提醒我",
    "免于提前一天提醒我",
    "无必要提前一天提醒我",
    "小王提前一天提醒我",
    "王老师提前一天提醒我",
    "导师提前一天提醒我",
    "他提前一天提醒我",
    "找小王提前一天提醒我",
    "喊小王提前一天提醒我",
    "派小王提前一天提醒我",
    "责成小王提前一天提醒我",
    "转告小王提前一天提醒我",
    "吩咐小王提前一天提醒我",
    "毋用提前一天提醒我",
    "休要提前一天提醒我",
    "何须提前一天提醒我",
    "何苦提前一天提醒我",
    "叮嘱小王提前一天提醒我",
    "交代小王提前一天提醒我",
    "命令小王提前一天提醒我",
    "同事提前一天提醒我",
    "舍友提前一天提醒我",
    "队长提前一天提醒我",
    "组员提前一天提醒我",
    "大家提前一天提醒我",
    "对方提前一天提醒我",
    "它提前一天提醒我",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("root", _ROUND26_UNSAFE_REMINDER_ROOTS)
async def test_round26_reminder_negation_and_external_agent_roots_fail_closed(
    root: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建待办：交作业，{root}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        root,
        context=["创建待办：交作业"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "connector",
    [
        "另外",
        "顺手",
        "顺带",
        "顺道",
        "随手",
        "额外",
        "再者",
        "还要",
        "还得",
        "顺势",
        "到时候",
        "届时",
        "此外",
        "顺路",
        "一同",
        "也请",
        "还请",
    ],
)
async def test_round26_nominal_reminder_connectors_fail_closed(connector: str) -> None:
    root = f"记录作业{connector}提醒系统"
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建待办：{root}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        root,
        context=["创建待办：交作业"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["请", "麻烦", "顺手", "劳烦", "劳驾", "烦请", "拜托"])
async def test_round26_bare_reminder_request_prefixes_do_not_pollute_title(
    prefix: str,
) -> None:
    result = await IntentParser().parse(
        f"创建待办：交作业，{prefix}提前一天提醒我",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "交作业"
    assert result.slots.reminder_minutes == 1_440


@pytest.mark.asyncio
@pytest.mark.parametrize("thanks", ["谢谢", "多谢", "感谢", "谢了"])
async def test_round26_reminder_gratitude_suffix_does_not_pollute_title(
    thanks: str,
) -> None:
    result = await IntentParser().parse(
        f"创建待办：交作业，提前一天提醒我，{thanks}",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "交作业"
    assert result.slots.reminder_minutes == 1_440


@pytest.mark.asyncio
async def test_round26_external_agent_false_positive_controls_remain_supported() -> None:
    parser = IntentParser()
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    for title in ("拜访小王", "寻找小王"):
        result = await parser.parse(f"创建待办：{title}，提前一天提醒我", now=now)
        assert result.intent == IntentName.CREATE_TASK
        assert result.slots.title == title
        assert result.slots.reminder_minutes == 1_440


_ROUND26_TARGET_SEPARATORS = (
    "高数连同英语",
    "高数并且英语",
    "高数以及英语",
    "高数和英语",
    "高数与英语",
    "高数或者英语",
    "高数或英语",
    "高数跟英语",
    "高数及英语",
    "高数还有英语",
    "A/B",
    "A&B",
    "A+B",
    "A|B",
    "A·B",
    "A、B",
    "高数另加英语",
    "高数再加英语",
    "高数外带英语",
    "高数连带英语",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("targets", _ROUND26_TARGET_SEPARATORS)
async def test_round26_all_target_separators_fail_closed_direct_and_context(
    targets: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"删除任务：{targets}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        targets,
        context=["删除任务：高数"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


_ROUND26_HIDDEN_TARGET_MARKERS = (
    "把",
    "将",
    "让",
    "令",
    "为",
    "帮",
    "使",
    "叫",
    "请",
    "由",
    "交由",
    "委托",
    "托付",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", _ROUND26_HIDDEN_TARGET_MARKERS)
async def test_round26_hidden_target_markers_fail_closed_direct_and_pure_context(
    marker: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"更新任务：高数{marker}英语优先级改为高",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        f"高数{marker}英语",
        context=["更新任务：高数"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["把", "将"])
@pytest.mark.parametrize("right_length", [64, 65, 128, 512])
async def test_round26_object_first_target_length_cannot_bypass_guard(
    marker: str,
    right_length: int,
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        f"更新任务：A{marker}{'B' * right_length}优先级改为高",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


_ROUND26_SINGLE_TARGET_CONTROLS = (
    "礼让行人",
    "谦让精神",
    "行为分析",
    "口令设计",
    "指令设计",
    "鞋帮修复",
    "帮派研究",
    "使命研究",
    "叫法设计",
    "申请报告",
    "理由分析",
    "门把手维修",
    "将来计划",
    "法律委托协议",
    "论文为人民服务",
    "让步方案",
    "转让报告",
    "帮扶报告",
    "命令报告",
    "以论文为主题的报告",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("title", _ROUND26_SINGLE_TARGET_CONTROLS)
async def test_round26_hidden_marker_lexical_titles_remain_supported(
    title: str,
) -> None:
    result = await IntentParser().parse(
        f"更新任务：{title}，优先级改为高",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == title
    assert result.slots.priority == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title",
    [
        "牛奶再加热",
        "A连带责任",
        "课外带练",
        "需求分析与设计",
        "研发和测试",
        "前端和后端联调",
        "理论与实践",
        "C/C++开发",
    ],
)
async def test_round26_conditional_and_natural_single_targets_remain_supported(
    title: str,
) -> None:
    result = await IntentParser().parse(
        f"删除任务：{title}",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.DELETE_TASK
    assert result.slots.title == title


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["高数和英语", "高数让英语"])
async def test_round26_quoted_target_connectors_remain_atomic(title: str) -> None:
    result = await IntentParser().parse(
        f"删除任务：“{title}”",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.DELETE_TASK
    assert result.slots.title == title


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title",
    [
        *_ROUND26_SINGLE_TARGET_CONTROLS,
        "A连带责任",
        "课外带练",
        "需求分析与设计",
        "研发和测试",
        "C/C++开发",
        "“高数和英语”",
        "“高数让英语”",
    ],
)
async def test_round26_valid_contextual_single_targets_still_reach_llm(
    title: str,
) -> None:
    llm = RecordingMutationLlm()

    await IntentParser(llm).parse(
        title,
        context=["更新任务：旧任务"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0


_ROUND26_TRAILING_COMMAND_CONNECTORS = (
    "顺带",
    "顺道",
    "另外",
    "顺手",
    "随手",
    "额外",
    "还要",
    "还得",
    "顺势",
    "再者",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("connector", _ROUND26_TRAILING_COMMAND_CONNECTORS)
@pytest.mark.parametrize("operation", ["删除", "清空"])
async def test_round26_unrecognized_trailing_command_connectors_fail_closed(
    connector: str,
    operation: str,
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        f"创建待办：A{connector}{operation}任务B",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


_ROUND26_NEW_SAFETY_ONLY_SIGNALS = (
    "清掉",
    "清零",
    "清理掉",
    "消除",
    "移走",
    "废除",
    "废弃",
    "抹去",
    "销掉",
    "废掉",
    "废止",
    "勾销",
    "抹除",
    "擦除",
    "归零",
    "重置",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", _ROUND26_NEW_SAFETY_ONLY_SIGNALS)
async def test_round26_additional_safety_only_signals_fail_closed(signal: str) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"创建待办：A顺带{signal}任务B",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        f"{signal}任务B",
        context=["创建待办：A"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", _ROUND26_NEW_SAFETY_ONLY_SIGNALS)
async def test_round26_safety_only_words_remain_valid_in_meta_titles(
    signal: str,
) -> None:
    result = await IntentParser().parse(
        f"创建待办：研究{signal}任务的方法",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == f"研究{signal}任务的方法"


@pytest.mark.asyncio
async def test_round26_trailing_command_quote_and_meta_controls_remain_supported() -> None:
    parser = IntentParser()
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))

    quoted = await parser.parse("创建待办：“A顺带删除任务B”", now=now)
    meta = await parser.parse("创建待办：研究A顺带删除任务B的方法", now=now)

    assert quoted.intent == IntentName.CREATE_TASK
    assert quoted.slots.title == "“A顺带删除任务B”"
    assert meta.intent == IntentName.CREATE_TASK
    assert meta.slots.title == "研究A顺带删除任务B的方法"


_ROUND26_ABORT_VARIANTS = (
    "算了啦",
    "算了哈",
    "算了呗",
    "当我没说了",
    "这次算了",
    "取消这次操作",
    "停止这次操作",
    "放弃这次操作",
    "停止本次变更",
    "不用继续了",
    "到此为止了",
    "算了！！",
    "还是算了。。。",
    "不作数了……",
    "算了～",
    "算了嘛",
    "算了咯",
    "算了喽",
    "算啦",
    "那算了",
    "要不算了",
    "要不然算了",
    "干脆算了",
    "算了算了",
    "取消这个操作",
    "停止当前操作",
    "撤回当前变更",
    "就先这样",
    "到这为止",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND26_ABORT_VARIANTS)
async def test_round26_abort_variants_fail_closed_direct_and_context(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"更新任务：论文标题改为初稿{phrase}",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["更新任务：论文"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    ["算了啦", "算了嘛", "取消这次操作", "停止当前操作", "就先这样"],
)
async def test_round26_abort_variants_inside_quotes_remain_metadata(
    phrase: str,
) -> None:
    text = f"更新任务：论文，描述改为“{phrase}”"
    llm = _round15_metadata_llm(IntentName.UPDATE_TASK, "description", phrase)

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.description == phrase


@pytest.mark.asyncio
async def test_round26_abort_false_positive_controls_remain_supported() -> None:
    parser = IntentParser()
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))

    quoted_rename = await parser.parse("更新任务：论文标题改为“算了啦”", now=now)
    guide = await parser.parse("更新任务：论文标题改为取消这次操作指南", now=now)
    literal = await parser.parse("创建待办：取消这次操作", now=now)
    meta = await parser.parse("创建待办：研究算了啦的语气", now=now)

    assert quoted_rename.intent == IntentName.UPDATE_TASK
    assert quoted_rename.slots.new_title == "算了啦"
    assert guide.intent == IntentName.UPDATE_TASK
    assert guide.slots.new_title == "取消这次操作指南"
    assert literal.intent == IntentName.CREATE_TASK
    assert literal.slots.title == "取消这次操作"
    assert meta.intent == IntentName.CREATE_TASK
    assert meta.slots.title == "研究算了啦的语气"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("intent", "field", "text", "candidate"),
    [
        (
            IntentName.CREATE_EVENT,
            "location",
            "创建日程：room101，地点改为ROOM101",
            "room101",
        ),
        (IntentName.CREATE_TASK, "description", "创建待办：abc，描述改为ABC", "abc"),
        (IntentName.CREATE_TASK, "course", "创建待办：math，课程改为MATH", "math"),
        (
            IntentName.CREATE_EVENT,
            "location",
            "创建日程：ROOM101，地点改为ＲＯＯＭ１０１",
            "ROOM101",
        ),
    ],
)
async def test_round26_metadata_exact_occurrence_must_be_at_field_cue(
    intent: IntentName,
    field: str,
    text: str,
    candidate: str,
) -> None:
    llm = _round15_metadata_llm(intent, field, candidate)

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == intent
    assert getattr(result.slots, field) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field_value",
    [
        "\u200b图书馆",
        "图书馆\u200b",
        "\u2060图书馆",
        "图书馆\u2060",
        "图\u200b书馆",
    ],
)
async def test_round26_metadata_format_controls_at_field_value_are_rejected(
    field_value: str,
) -> None:
    text = f"创建日程：答辩，地点改为{field_value}"
    llm = _round15_metadata_llm(IntentName.CREATE_EVENT, "location", "图书馆")

    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.location is None


_ROUND26_LEXICAL_ALLOWLIST_TARGET_BYPASSES = (
    "论文让步骤任务",
    "论文叫号码任务",
    "论文请求任务",
    "论文由来任务",
    "论文托付事项任务",
    "论文礼让任务",
    "论文鞋帮任务",
    "论文命令任务",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("targets", _ROUND26_LEXICAL_ALLOWLIST_TARGET_BYPASSES)
async def test_round26_lexical_allowlists_cannot_hide_explicit_target_signals(
    targets: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"更新任务：{targets}优先级改为高",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        targets,
        context=["更新任务：论文"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "targets",
    [
        "高数再加热任务",
        "高数连带责任任务",
        "高数外带服务任务",
        "高数再加热日程",
        "高数连带影响事件",
    ],
)
async def test_round26_conditional_lexical_allowlists_cannot_hide_target_signals(
    targets: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"删除任务：{targets}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        targets,
        context=["删除任务：高数"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("title", "expected_title"),
    [
        ("提交申请", "提交申请"),
        ("填写申请", "填写申请"),
        ("发出邀请", "发出邀请"),
        ("接受邀请", "接受邀请"),
        ("申请", "申请"),
        ("处理麻烦", "处理麻烦"),
    ],
)
async def test_round26_reminder_prefix_collisions_preserve_title(
    title: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        f"创建待办：{title}提前一天提醒我",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == expected_title
    assert result.slots.reminder_minutes == 1_440


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "connector",
    ["顺路", "回头", "待会儿", "过会儿", "顺便儿", "顺道儿"],
)
@pytest.mark.parametrize("operation", ["删", "改", "加"])
async def test_round26_arbitrary_text_cannot_hide_bare_trailing_mutation(
    connector: str,
    operation: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"创建待办：A{connector}{operation}任务B",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        f"{operation}任务B",
        context=["创建待办：A"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", ["废掉", "废止", "勾销", "抹除", "擦除", "归零", "重置"])
@pytest.mark.parametrize("connector", ["顺路", "过会儿"])
async def test_round26_arbitrary_text_cannot_hide_safety_mutation_morphology(
    signal: str,
    connector: str,
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        f"创建待办：A{connector}{signal}任务B",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


_ROUND29_ATTACHED_DATE_ACTION_POINT_TITLES = (
    "制作明天的三点示意图",
    "制作明天的3点示意图",
    "完成明天的三点校准",
    "完成明天的3点校准",
    "研究明天三点定理",
    "研究明天3点定理",
    "绘制后天的三点连线",
    "绘制后天的3点连线",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("title", _ROUND29_ATTACHED_DATE_ACTION_POINT_TITLES)
async def test_round29_attached_date_action_point_literals_fail_closed(
    title: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建待办：{title}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        title,
        context=["创建待办：安全研究"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title",
    [
        "关于三点诉求",
        "关于3点诉求",
        "提出三点诉求",
        "提出3点诉求",
        "基于三点事项",
        "基于3点事项",
    ],
)
async def test_round29_generic_lexical_point_quantities_fail_closed(
    title: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建待办：{title}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        title,
        context=["创建待办：需求整理"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
async def test_round29_attached_date_time_before_enumeration_noun_remains_supported() -> None:
    result = await IntentParser().parse(
        "创建待办：明天3点任务评审",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.due_date == "2026-07-29"
    assert result.slots.due_time == "03:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", ["去掉", "去除"])
async def test_round29_removal_morphology_cannot_hide_trailing_commands(
    signal: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"创建待办：A回头{signal}任务B",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        f"{signal}任务B",
        context=["创建待办：A"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title",
    [
        "研究A回头删除任务B",
        "研究A过会儿清空任务B",
        "研究加法和删除任务B",
    ],
)
async def test_round29_meta_prefix_cannot_exempt_later_mutation_commands(
    title: str,
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        f"创建待办：{title}",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title",
    [
        "学习创建任务和删除任务",
        "整理删除任务和创建任务文档",
        "阅读删除算法笔记",
        "研究A顺带删除任务B的方法",
        "研究去掉任务的方法",
    ],
)
async def test_round29_structural_meta_titles_remain_supported(title: str) -> None:
    result = await IntentParser().parse(
        f"创建待办：{title}",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == title


@pytest.mark.asyncio
@pytest.mark.parametrize("connector", ["外加", "另加", "再加", "外带", "连带", "反而"])
async def test_round29_nominal_reminder_connectors_fail_closed(
    connector: str,
) -> None:
    root = f"记录作业{connector}提醒系统"
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建待办：{root}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        root,
        context=["创建待办：交作业"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "root",
    [
        "小明提前一天提醒我",
        "张三提前一天提醒我",
        "给小王提前一天提醒我",
    ],
)
async def test_round29_arbitrary_external_reminder_agents_fail_closed(
    root: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"创建待办：交作业，{root}",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        root,
        context=["创建待办：交作业"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
async def test_round29_named_title_and_self_reminder_controls_remain_supported() -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    named = await IntentParser().parse(
        "创建待办：小明提前一天提醒我",
        now=now,
    )
    self_request = await IntentParser().parse(
        "创建待办：交作业，请你提前一天提醒我",
        now=now,
    )

    assert named.intent == IntentName.CREATE_TASK
    assert named.slots.title == "小明"
    assert named.slots.reminder_minutes == 1_440
    assert self_request.intent == IntentName.CREATE_TASK
    assert self_request.slots.title == "交作业"
    assert self_request.slots.reminder_minutes == 1_440


_ROUND29_SCOPE_LEAD_ABORTS = (
    "这次就算了",
    "这次就算了吧",
    "这次还是算了",
    "这回就算了",
    "这回还是算了",
    "本次就算了",
    "本回就算了",
    "此回还是算了",
    "那这次还是算了",
    "那本次就算了",
    "这次干脆算了",
    "这回要不算了",
    "这次不弄了",
    "撤回一下修改",
    "就这样吧",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND29_SCOPE_LEAD_ABORTS)
async def test_round29_scope_lead_aborts_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"更新任务：论文标题改为初稿{phrase}",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["更新任务：论文"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["删除任务A算了", "完成任务A算了", "删除任务：“算了”算了"])
async def test_round29_global_mutation_abort_tails_fail_closed(text: str) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round29_abort_text_inside_quoted_target_remains_literal() -> None:
    result = await IntentParser().parse(
        "删除任务：“算了”",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.DELETE_TASK
    assert result.slots.title == "算了"


@pytest.mark.asyncio
@pytest.mark.parametrize("operator", ["变成", "改作", "换作"])
async def test_round29_unsupported_field_assignment_operators_fail_closed(
    operator: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"更新任务：论文课程{operator}软件工程",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        f"课程{operator}软件工程",
        context=["更新任务：论文"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["地点", "位置"])
async def test_round29_task_location_assignments_fail_closed(
    field: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"更新任务：论文{field}改为图书馆",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        f"{field}改为图书馆",
        context=["更新任务：论文"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
async def test_round29_event_location_assignment_remains_supported() -> None:
    llm = _round15_metadata_llm(IntentName.UPDATE_EVENT, "location", "图书馆")
    result = await IntentParser(llm).parse(
        "更新日程：答辩地点改为图书馆",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UPDATE_EVENT
    assert result.slots.location == "图书馆"


@pytest.mark.asyncio
async def test_round29_quoted_location_words_remain_valid_task_description() -> None:
    value = "地点改为图书馆"
    llm = _round15_metadata_llm(IntentName.UPDATE_TASK, "description", value)
    result = await IntentParser(llm).parse(
        f"更新任务：论文，描述改为“{value}”",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0
    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.description == value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "修改论文，优先级改为高",
        "重命名论文为第二版",
    ],
)
async def test_round29_untyped_raw_mutations_fail_closed_without_llm(
    text: str,
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["修改论文，优先级改为高"])
async def test_round29_contextual_raw_mutations_still_reach_llm(text: str) -> None:
    llm = RecordingMutationLlm()
    await IntentParser(llm).parse(
        text,
        context=["更新任务：论文"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0


@pytest.mark.asyncio
async def test_round29_ordinary_unknown_text_still_reaches_llm() -> None:
    llm = RecordingMutationLlm()
    await IntentParser(llm).parse(
        "帮我想个点子",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert llm.extract_calls == 1
    assert llm.repair_calls == 0


def test_round29_dense_hidden_update_markers_remain_linear() -> None:
    dense_title = "行为" * 4_990
    direct = f"更新任务：{dense_title}，标题改为论文"
    contextual = f"{dense_title}，标题改为论文"
    assert len(direct) < 10_000
    assert len(contextual) < 10_000

    started = perf_counter()
    assert _has_ambiguous_update_target(direct, IntentName.UPDATE_TASK) is False
    direct_elapsed = perf_counter() - started

    started = perf_counter()
    assert _has_contextual_ambiguous_target(contextual, ("更新任务：论文",)) is False
    contextual_elapsed = perf_counter() - started

    assert direct_elapsed < 1.0
    assert contextual_elapsed < 1.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('“"论文" "报告"”', '"论文" "报告"'),
        ("“  《  论文  》  ”", "论文"),
        ("  “论文”  ", "  “论文”  "),
        ("“O'Reilly”", "O'Reilly"),
        ("„论文“", "论文"),
    ],
)
def test_round29_outer_quote_stripping_preserves_boundaries(
    value: str,
    expected: str,
) -> None:
    assert _strip_outer_quoted_literal(value) == expected


def test_round29_deeply_nested_outer_quotes_strip_in_linear_time() -> None:
    depth = 4_999
    value = "“" * depth + "论文" + "”" * depth
    assert len(value) == 10_000

    started = perf_counter()
    assert _strip_outer_quoted_literal(value) == "论文"
    assert perf_counter() - started < 1.0


_ROUND30_STRUCTURAL_LEXICAL_POINT_TITLES = (
    "关于三点办法",
    "提出3点条件",
    "基于三点原因",
    "围绕3点议题",
    "关于明天的三点诉求",
    "提出明天的3点诉求",
    "基于明天3点事项",
    "学习明天的三点定理",
    "讲解明天的3点材料",
    "编写明天的三点示意图",
    "生成明天3点示意图",
    "测量明天的三点距离",
    "连接明天三点线路",
    "阅读明天的三点材料",
    "实现明天3点功能",
    "关于明天的３点诉求",
    "关于明天的③点诉求",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("title", _ROUND30_STRUCTURAL_LEXICAL_POINT_TITLES)
async def test_round30_structural_lexical_points_fail_closed(title: str) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建待办：{title}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        title,
        context=["创建待办：安全研究"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：明天3点任务评审",
        "创建待办：明天３点任务评审",
        "创建待办：明天 3 点任务评审",
        "创建待办：明天15点任务评审",
        "创建日程：会议3点开始",
    ],
)
async def test_round30_structural_point_time_controls_remain_supported(
    text: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent in {IntentName.CREATE_TASK, IntentName.CREATE_EVENT}
    assert (result.slots.due_time or result.slots.start_time) in {"03:00", "15:00"}


_ROUND30_REMINDER_CONNECTORS = (
    "回头",
    "转头",
    "等会",
    "等会儿",
    "过会",
    "过会儿",
    "稍候",
    "稍等",
    "片刻后",
    "尔后",
    "其后",
    "待一会儿",
    "晚点",
    "后面",
    "不久后",
    "到时",
    "改天",
    "反倒",
    "加之",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("connector", _ROUND30_REMINDER_CONNECTORS)
async def test_round30_nominal_reminder_connectors_fail_closed(connector: str) -> None:
    root = f"记录作业{connector}提醒系统"
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建待办：{root}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        root,
        context=["创建待办：交作业"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


_ROUND30_EXTERNAL_REMINDER_ACTORS = (
    "Alice",
    "ALICE",
    "Amina",
    "Zoë",
    "Саша",
    "山田太郎",
    "迪丽热巴",
    "阿依·古丽",
    "杰克",
    "麦克",
    "爱丽丝",
    "product manager",
    "faculty Alice",
    "🤖",
    "要Alice",
    "Alice会",
    "Alice来",
    "Alice负责",
    "Alice帮忙",
    "給Alice",
    "讓Alice",
    "請Alice",
    "委託Alice",
    "麻煩Alice",
    "轉告Alice",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", _ROUND30_EXTERNAL_REMINDER_ACTORS)
async def test_round30_arbitrary_external_reminder_actors_fail_closed(
    actor: str,
) -> None:
    root = f"{actor}提前一天提醒我"
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"创建待办：交作业，{root}",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        root,
        context=["创建待办：交作业"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        ("创建待办：Alice提前一天提醒我", "Alice"),
        ("创建待办：山田太郎提前一天提醒我", "山田太郎"),
        ("创建待办：🤖提前一天提醒我", "🤖"),
        ("创建待办：Alice请你提前一天提醒我", "Alice"),
        ("创建待办：交作业，请你提前一天提醒我", "交作业"),
        ("创建待办：交作业，麻烦你提前一天提醒我", "交作业"),
        ("创建待办：交作业，给我提前一天提醒我", "交作业"),
        ("创建待办：交作业，替我提前一天提醒我", "交作业"),
        ("创建待办：交作业，您提前一天提醒我", "交作业"),
    ],
)
async def test_round30_named_titles_and_self_reminders_remain_supported(
    text: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == expected_title
    assert result.slots.reminder_minutes == 1_440


_ROUND30_ABORT_PHRASES = (
    "这次就先算了",
    "那这次就先算了",
    "这次还是先算了",
    "这次要不就算了",
    "本回干脆就算了",
    "那就这样吧",
    "这次就这样吧",
    "这回先这样吧",
    "那就先这样吧",
    "本次就这样",
    "这回别弄了",
    "麻烦撤回一下当前操作",
    "把这个修改撤回一下",
    "算了哟",
    "不搞了",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND30_ABORT_PHRASES)
async def test_round30_abort_compositions_fail_closed_without_llm(phrase: str) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"更新任务：论文标题改为初稿{phrase}",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["更新任务：论文"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：算了",
        "创建待办：论文算了",
        "创建待办：论文不弄了",
        "创建日程：明天3点开会算了",
    ],
)
async def test_round30_create_abort_tails_fail_closed_without_llm(text: str) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round30_explicit_create_abort_literals_remain_supported() -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    operation = await IntentParser().parse("创建待办：取消这次操作", now=now)
    quoted = await IntentParser().parse("创建待办：“论文算了”", now=now)

    assert operation.intent == IntentName.CREATE_TASK
    assert operation.slots.title == "取消这次操作"
    assert quoted.intent == IntentName.CREATE_TASK
    assert quoted.slots.title == "“论文算了”"


_ROUND30_CONTEXT_ORPHAN_MUTATIONS = (
    "A说完再删除任务B",
    "A最终又删掉任务B",
    "论文顺手移除任务B",
    "研究A稍后撤销任务B",
    "A然后撤掉任务B",
    "A然后更新任务B",
    "A然后创建任务B",
    "顺手把删除任务B",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _ROUND30_CONTEXT_ORPHAN_MUTATIONS)
async def test_round30_context_orphan_leading_mutations_fail_closed(text: str) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        text,
        context=["创建待办：A"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "删除任务B",
        "请删除任务B",
        "麻烦删除任务B",
        "把任务B删除",
        "删掉任务B",
        "移除任务B",
    ],
)
async def test_round30_supported_context_mutation_starts_remain_valid(
    text: str,
) -> None:
    result = await IntentParser().parse(
        text,
        context=["创建待办：A"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.DELETE_TASK


@pytest.mark.asyncio
async def test_round30_quoted_mutation_target_remains_atomic() -> None:
    result = await IntentParser().parse(
        "删除任务：“A然后删除任务B”",
        context=["创建待办：A"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.DELETE_TASK
    assert result.slots.title == "A然后删除任务B"


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", ["除掉", "拿掉", "剔除", "关掉"])
async def test_round30_mutation_morphology_cannot_hide_commands(signal: str) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"创建待办：A回头{signal}任务B",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        f"{signal}任务B",
        context=["创建待办：A"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operator",
    ["变作", "成为", "转作", "设作", "置成", "当作", "作为"],
)
async def test_round30_field_assignment_morphology_fails_closed(
    operator: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(
        f"更新任务：论文课程{operator}软件工程",
        now=now,
    )
    contextual = await IntentParser(contextual_llm).parse(
        f"课程{operator}软件工程",
        context=["更新任务：论文"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title",
    [
        "研究更新任务A回头删除任务B",
        "研究删除算法过会儿删除任务B",
        "学习创建任务文档回头清空任务B",
    ],
)
async def test_round30_meta_prefix_cannot_hide_noncoordinated_mutations(
    title: str,
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        f"创建待办：{title}",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title",
    [
        "学习创建任务和删除任务",
        "整理删除任务和创建任务文档",
    ],
)
async def test_round30_coordinated_meta_titles_remain_supported(title: str) -> None:
    result = await IntentParser().parse(
        f"创建待办：{title}",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == title


@pytest.mark.asyncio
async def test_round30_structural_guards_remain_linear_near_input_limit() -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    point_text = "创建待办：" + ("明天3点任务，" * 1_300)[:9_800]
    actor_text = "创建待办：交作业，" + "A" * 9_700 + "提前一天提醒我"
    orphan_text = "A" * 9_700 + "回头删除任务B"
    assert len(point_text) < 10_000
    assert len(actor_text) < 10_000
    assert len(orphan_text) < 10_000

    started = perf_counter()
    point_llm = RecordingMutationLlm()
    point = await IntentParser(point_llm).parse(point_text, now=now)
    point_elapsed = perf_counter() - started

    started = perf_counter()
    actor_llm = RecordingMutationLlm()
    actor = await IntentParser(actor_llm).parse(actor_text, now=now)
    actor_elapsed = perf_counter() - started

    started = perf_counter()
    orphan_llm = RecordingMutationLlm()
    orphan = await IntentParser(orphan_llm).parse(
        orphan_text,
        context=["创建待办：A"],
        now=now,
    )
    orphan_elapsed = perf_counter() - started

    _assert_no_llm_unknown(point, point_llm)
    _assert_no_llm_unknown(actor, actor_llm)
    _assert_no_llm_unknown(orphan, orphan_llm)
    assert point_elapsed < 1.0
    assert actor_elapsed < 1.0
    assert orphan_elapsed < 1.0


_ROUND31_LONG_DELEGATED_REMINDER_PREFIXES = (
    "给" + "A" * 33,
    "让" + "山田" * 40,
    "委托" + "🤖" * 40,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", _ROUND31_LONG_DELEGATED_REMINDER_PREFIXES)
async def test_round31_long_delegated_reminders_fail_closed_without_llm(
    prefix: str,
) -> None:
    root = f"{prefix}提前一天提醒我"
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建待办：{root}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        root,
        context=["创建待办：交作业"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


_ROUND31_INVALID_DATE_RANGE_BRIDGES = (
    "明天从从3点到4点任务",
    "明天从从从三点到四点任务",
    "明天从到3点到4点任务",
    "明天至从3点到4点任务",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("root", _ROUND31_INVALID_DATE_RANGE_BRIDGES)
async def test_round31_invalid_date_range_bridges_fail_closed_without_llm(
    root: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建日程：{root}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        root,
        context=["创建日程：安全会议"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
async def test_round31_single_from_date_range_remains_supported() -> None:
    result = await IntentParser().parse(
        "创建日程：明天从三点到四点组会",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.start_time == "03:00"
    assert result.slots.end_time == "04:00"


_ROUND31_CONTEXTUAL_NARRATIVE_MUTATIONS = (
    "任务B说完再删除",
    "任务B稍后删除",
    "任务B最终又删除",
    "把任务B说完再删除",
    "把任务B稍后删除",
    "把要删除任务B",
    "顺手把要删除任务B",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _ROUND31_CONTEXTUAL_NARRATIVE_MUTATIONS)
async def test_round31_context_requires_a_strict_mutation_object(text: str) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        text,
        context=["创建待办：任务B"],
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "创建待办：论文撤回当前操作一下",
        "创建待办：论文不处理了",
    ],
)
async def test_round31_create_abort_tails_fail_closed_without_llm(text: str) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


_ROUND31_ABORT_COMPOSITIONS = (
    "麻烦帮我撤回当前操作",
    "麻烦撤回当前操作一下",
    "这次还是不处理了",
    "那就先别折腾了",
    "这次还是要不就先算了",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND31_ABORT_COMPOSITIONS)
async def test_round31_abort_compositions_fail_closed_without_llm(phrase: str) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(phrase, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["更新任务：论文"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "title",
    [
        "研究删除任务A回头删任务B",
        "研究创建任务A回头和删除任务B",
        "学习更新任务和再删任务B",
    ],
)
async def test_round31_meta_titles_cannot_hide_raw_or_non_nominal_mutations(
    title: str,
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        f"创建待办：{title}",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round31_long_delegated_reminder_guard_remains_linear() -> None:
    text = "创建待办：给" + "A" * 9_700 + "提前一天提醒我"
    assert len(text) < 10_000
    llm = RecordingMutationLlm()

    started = perf_counter()
    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    elapsed = perf_counter() - started

    _assert_no_llm_unknown(result, llm)
    assert elapsed < 1.0


_ROUND32_ABORT_NEGATORS = (
    "不",
    "别",
    "不要",
    "不用",
    "无需",
    "无须",
    "不必",
    "不再",
    "别再",
    "不要再",
    "不用再",
    "无需再",
    "无须再",
    "不必再",
    "请勿",
    "勿",
    "甭",
    "甭再",
)
_ROUND32_ABORT_ACTIONS = (
    "弄",
    "搞",
    "做",
    "干",
    "办",
    "改",
    "处理",
    "折腾",
    "继续",
    "动",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("negator", _ROUND32_ABORT_NEGATORS)
@pytest.mark.parametrize("action", _ROUND32_ABORT_ACTIONS)
async def test_round32_abort_negator_action_product_fails_closed_without_llm(
    negator: str,
    action: str,
) -> None:
    phrase = f"{negator}{action}了"
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()
    create_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(phrase, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["更新任务：论文"],
        now=now,
    )
    create = await IntentParser(create_llm).parse(
        f"创建待办：论文，{phrase}",
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)
    _assert_no_llm_unknown(create, create_llm)


_ROUND33_ABORT_SOFTENER_ACTIONS = (
    "弄",
    "搞",
    "做",
    "干",
    "办",
    "改",
    "管",
    "动",
    "处理",
    "折腾",
    "继续",
    "操作",
    "执行",
    "推进",
    "删除",
    "更新",
)
_ROUND33_ABORT_POLITE_LEADS = (
    "",
    "请",
    "麻烦",
    "麻烦你",
    "拜托你",
    "劳驾您",
    "请帮我",
    "麻烦你帮忙",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("negator", _ROUND32_ABORT_NEGATORS)
@pytest.mark.parametrize("action", _ROUND33_ABORT_SOFTENER_ACTIONS)
async def test_round33_abort_softener_product_fails_closed_without_llm(
    negator: str,
    action: str,
) -> None:
    matrix_index = _ROUND32_ABORT_NEGATORS.index(negator) + _ROUND33_ABORT_SOFTENER_ACTIONS.index(
        action
    )
    contextual_lead = _ROUND33_ABORT_POLITE_LEADS[matrix_index % len(_ROUND33_ABORT_POLITE_LEADS)]
    create_lead = _ROUND33_ABORT_POLITE_LEADS[(matrix_index + 3) % len(_ROUND33_ABORT_POLITE_LEADS)]
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()
    create_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"{negator}{action}一下", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        f"{contextual_lead}{negator}{action}一下",
        context=["更新任务：论文"],
        now=now,
    )
    create = await IntentParser(create_llm).parse(
        f"创建待办：论文，{create_lead}{negator}{action}一下",
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)
    _assert_no_llm_unknown(create, create_llm)


_ROUND32_STANDALONE_ABORTS = (
    "撤回一下",
    "撤销一下",
    "放弃一下",
    "收回一下",
    "打住",
    "麻烦你撤回当前操作一下",
    "请您帮忙把当前操作撤回一下",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND32_STANDALONE_ABORTS)
async def test_round32_standalone_abort_compositions_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(phrase, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["更新任务：论文"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


_ROUND32_OBJECT_FIRST_RESULT_STATES = (
    "失败",
    "失败了",
    "已失败",
    "成功",
    "成功啦",
    "已经成功",
    "尚未成功",
    "还没成功",
    "没有成功",
    "中",
    "中。",
    "进行中",
    "正在进行中",
    "还在进行中",
    "仍在进行中",
    "未果",
    "不了",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["删除", "更新", "修改", "撤销"])
@pytest.mark.parametrize("state", _ROUND32_OBJECT_FIRST_RESULT_STATES)
async def test_round32_object_first_result_states_are_narrative(
    mutation: str,
    state: str,
) -> None:
    text = f"把任务B{mutation}{state}"
    now = datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(text, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        text,
        context=["创建待办：任务B"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    [
        "无需处理了",
        "不再继续了",
        "请勿处理了",
        "甭弄了",
        "不要管一下",
        "麻烦你不做一下",
    ],
)
async def test_round32_quoted_abort_phrases_remain_literal_titles(
    phrase: str,
) -> None:
    result = await IntentParser().parse(
        f"创建待办：“{phrase}”",
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == f"“{phrase}”"


@pytest.mark.asyncio
async def test_round32_abort_lead_run_remains_linear_near_input_limit() -> None:
    text = "请" * 9_000 + "算了"
    assert len(text) < 10_000
    llm = RecordingMutationLlm()

    started = perf_counter()
    result = await IntentParser(llm).parse(
        text,
        now=datetime(2026, 7, 28, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    elapsed = perf_counter() - started

    _assert_no_llm_unknown(result, llm)
    assert elapsed < 1.0


_ROUND35_DANGLING_FROM_CASES = (
    ("创建日程：组会，明天从3点到4点从", "明天3点从"),
    ("创建日程：组会，明天3点然后从", "明天3点然后从"),
    ("创建日程：组会，明天3点从从", "明天3点从从"),
    ("创建日程：组会，明天3点从呢", "明天3点从呢"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direct_text", "context_text"), _ROUND35_DANGLING_FROM_CASES)
async def test_round35_terminal_from_connectors_fail_closed_without_llm(
    direct_text: str,
    context_text: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(direct_text, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        context_text,
        context=["创建日程：组会"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


_ROUND35_EXTERNAL_REMINDER_PREFIXES = (
    "小王负责",
    "Alice负责",
    "山田太郎帮忙",
    "责令" + "A" * 40,
    "督促" + "🤖" * 40,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", _ROUND35_EXTERNAL_REMINDER_PREFIXES)
async def test_round35_delegated_reminder_predicates_fail_closed_without_llm(
    prefix: str,
) -> None:
    phrase = f"{prefix}提前一天提醒我"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(f"创建待办：{phrase}", now=now)
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["创建待办：交作业"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


_ROUND35_ABORT_VARIANTS = (
    "不弄了呢",
    "算了吧呢",
    "停止呢",
    "不弄了，",
    "不弄了；",
    "别删了",
    "请勿再删一下吧？！",
    "甭删一下",
    "撤回了",
    "取消了",
    "取消这次操作了",
    "这次操作撤回吧",
    "撤了",
    "撤回了一下",
    "这次操作撤回了呢",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND35_ABORT_VARIANTS)
async def test_round35_abort_particles_punctuation_and_aspects_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()
    create_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(phrase, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["更新任务：论文"],
        now=now,
    )
    create = await IntentParser(create_llm).parse(f"创建待办：论文，{phrase}", now=now)

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)
    _assert_no_llm_unknown(create, create_llm)


_ROUND35_NARRATIVE_MARKERS = (
    "已经",
    "已",
    "刚刚",
    "刚才",
    "曾经",
    "此前",
    "早就",
    "正在",
    "还在",
    "仍在",
)
_ROUND35_NARRATIVE_MUTATIONS = ("删除", "移除", "更新", "修改")
_ROUND35_NARRATIVE_TAILS = ("", "了", "完了", "完毕了", "好了")


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", _ROUND35_NARRATIVE_MARKERS)
@pytest.mark.parametrize("mutation", _ROUND35_NARRATIVE_MUTATIONS)
@pytest.mark.parametrize("tail", _ROUND35_NARRATIVE_TAILS)
async def test_round35_aspect_marked_mutation_narratives_fail_closed_without_llm(
    marker: str,
    mutation: str,
    tail: str,
) -> None:
    phrase = f"把任务B{marker}{mutation}{tail}"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()
    create_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(phrase, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["创建待办：任务B"],
        now=now,
    )
    create = await IntentParser(create_llm).parse(f"创建待办：论文，{phrase}", now=now)

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)
    _assert_no_llm_unknown(create, create_llm)


_ROUND35_COMPLETED_MUTATIONS = ("删除", "删掉", "移除", "更新", "修改", "撤销", "创建")
_ROUND35_COMPLETION_TAILS = ("了", "完了", "完毕了", "好了", "过了")


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", _ROUND35_COMPLETED_MUTATIONS)
@pytest.mark.parametrize("tail", _ROUND35_COMPLETION_TAILS)
async def test_round35_completed_mutation_narratives_fail_closed_without_llm(
    mutation: str,
    tail: str,
) -> None:
    phrase = f"把任务B{mutation}{tail}"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()
    create_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(phrase, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["创建待办：任务B"],
        now=now,
    )
    create = await IntentParser(create_llm).parse(f"创建待办：论文，{phrase}", now=now)

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)
    _assert_no_llm_unknown(create, create_llm)


_ROUND35_RESULT_CONTINUATION_MUTATIONS = ("删除", "更新", "修改", "撤销")
_ROUND35_RESULT_CONTINUATION_STATES = ("失败", "成功", "未果")
_ROUND35_RESULT_CONTINUATION_BRIDGES = ("后又", "后", "之后又", "但又", "然后")
_ROUND35_RESULT_CONTINUATION_TAILS = ("恢复了", "归档了", "重试了")


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", _ROUND35_RESULT_CONTINUATION_MUTATIONS)
@pytest.mark.parametrize("state", _ROUND35_RESULT_CONTINUATION_STATES)
@pytest.mark.parametrize("bridge", _ROUND35_RESULT_CONTINUATION_BRIDGES)
@pytest.mark.parametrize("tail", _ROUND35_RESULT_CONTINUATION_TAILS)
async def test_round35_result_state_continuations_fail_closed_without_llm(
    mutation: str,
    state: str,
    bridge: str,
    tail: str,
) -> None:
    phrase = f"把任务B{mutation}{state}{bridge}{tail}"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(phrase, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["创建待办：任务B"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    [
        "创建待办：论文，任务B已经删掉了",
        "把论文已经添加到待办了",
        "请把任务B昨天取消了",
        "把任务B给删除了",
        "把任务B都删除了",
    ],
)
async def test_round35_additional_mutation_narratives_fail_closed_without_llm(
    phrase: str,
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        phrase,
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    _assert_no_llm_unknown(result, llm)


_ROUND35_SAFETY_ONLY_MUTATION_SYNONYMS = (
    "新增",
    "增添",
    "增设",
    "建立",
    "设立",
    "录入",
    "登记",
    "补录",
    "增补",
    "预约",
    "预定",
    "变更",
    "编辑",
    "修订",
    "修正",
    "延期",
    "顺延",
    "延后",
    "推后",
    "调期",
    "移期",
    "重排",
    "重设",
    "迁移",
    "清理",
    "放弃",
    "终止",
    "停止",
    "撤回",
    "撤下",
    "移出",
    "丢掉",
    "丢弃",
    "抛弃",
    "销除",
    "关闭",
    "结束",
    "解散",
    "下架",
    "撤单",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("synonym", _ROUND35_SAFETY_ONLY_MUTATION_SYNONYMS)
@pytest.mark.parametrize("target", ["待办", "任务", "日程"])
async def test_round35_unsupported_mutation_synonyms_fail_closed_without_llm(
    synonym: str,
    target: str,
) -> None:
    phrase = f"{synonym}{target}：论文"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(phrase, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["创建待办：论文"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


_ROUND35_IMPLICIT_TEMPORAL_TARGETS = (
    "删除明天的任务",
    "删除明天的日程",
    "删除今天下午三点的会议",
    "取消后天的考试",
    "移除7月30日的事件",
    "完成明天的任务",
    "更新明天的任务，优先级改为高",
    "更新明天的日程，地点改为图书馆",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND35_IMPLICIT_TEMPORAL_TARGETS)
async def test_round35_implicit_temporal_target_selectors_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    direct_llm = RecordingMutationLlm()
    contextual_llm = RecordingMutationLlm()

    direct = await IntentParser(direct_llm).parse(phrase, now=now)
    contextual = await IntentParser(contextual_llm).parse(
        phrase,
        context=["创建待办：任务B"],
        now=now,
    )

    _assert_no_llm_unknown(direct, direct_llm)
    _assert_no_llm_unknown(contextual, contextual_llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    [
        "不弄了呢",
        "撤回了",
        "这次操作撤回吧",
        "把任务B已经删除了",
        "新增任务B",
    ],
)
async def test_round35_quoted_safety_phrases_remain_literal_titles(phrase: str) -> None:
    result = await IntentParser().parse(
        f"创建待办：“{phrase}”",
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == f"“{phrase}”"


@pytest.mark.asyncio
async def test_round35_positive_controls_remain_supported() -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    valid_range = await IntentParser().parse("创建日程：明天从3点到4点组会", now=now)
    lexical_from = await IntentParser().parse("创建日程：明天3点从容讨论", now=now)
    delete_object = await IntentParser().parse("把任务B删除", now=now)
    delete_colloquial = await IntentParser().parse("把任务B删掉", now=now)
    update_temporal_field = await IntentParser().parse("把任务B推迟到明天", now=now)
    explicit_temporal_target = await IntentParser().parse("删除任务：明天的任务", now=now)
    quoted_temporal_target = await IntentParser().parse("删除任务《明天的任务》", now=now)
    explicit_update_target = await IntentParser().parse(
        "更新任务：明天的任务，优先级改为高",
        now=now,
    )
    synonym_meta_title = await IntentParser().parse("创建待办：研究新增任务的方法", now=now)
    synonym_title = await IntentParser().parse("创建待办：预约牙医", now=now)
    safety_title = await IntentParser().parse("创建待办：清理桌面", now=now)

    assert valid_range.intent == IntentName.CREATE_EVENT
    assert valid_range.slots.start_time == "03:00"
    assert valid_range.slots.end_time == "04:00"
    assert lexical_from.intent == IntentName.CREATE_EVENT
    assert lexical_from.slots.title == "从容讨论"
    assert delete_object.intent == IntentName.DELETE_TASK
    assert delete_object.slots.title == "B"
    assert delete_colloquial.intent == IntentName.DELETE_TASK
    assert delete_colloquial.slots.title == "B"
    assert update_temporal_field.intent == IntentName.UPDATE_TASK
    assert update_temporal_field.slots.title == "B"
    assert update_temporal_field.slots.due_date == "2026-07-31"
    assert explicit_temporal_target.intent == IntentName.DELETE_TASK
    assert explicit_temporal_target.slots.title == "明天的任务"
    assert quoted_temporal_target.intent == IntentName.DELETE_TASK
    assert quoted_temporal_target.slots.title == "明天的任务"
    assert explicit_update_target.intent == IntentName.UPDATE_TASK
    assert explicit_update_target.slots.title == "明天的任务"
    assert explicit_update_target.slots.priority == "high"
    assert synonym_meta_title.intent == IntentName.CREATE_TASK
    assert synonym_meta_title.slots.title == "研究新增任务的方法"
    assert synonym_title.intent == IntentName.CREATE_TASK
    assert synonym_title.slots.title == "预约牙医"
    assert safety_title.intent == IntentName.CREATE_TASK
    assert safety_title.slots.title == "清理桌面"


@pytest.mark.asyncio
async def test_round35_new_guards_remain_linear_near_input_limit() -> None:
    cases = (
        "创建待办：责令" + "A" * 9_700 + "提前一天提醒我",
        "明天3点" + "从" * 9_000,
        "任务B" + "已经" * 3_000 + "删除了",
    )
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    for text in cases:
        assert len(text) < 10_000
        llm = RecordingMutationLlm()
        started = perf_counter()
        result = await IntentParser(llm).parse(
            text,
            context=["创建日程：组会"],
            now=now,
        )
        elapsed = perf_counter() - started

        _assert_no_llm_unknown(result, llm)
        assert elapsed < 1.0


_ROUND36_RELATIVE_TEMPORAL_TARGETS = (
    "删除昨天的任务",
    "删除前天的任务",
    "删除上周一的任务",
    "删除周五的日程",
    "删除本周的日程",
    "删除下个月的日程",
    "删除上午的会议",
    "删除明早的日程",
    "删除明晚的日程",
    "删除月底的任务",
    "删除近期的任务",
    "删除明年的日程",
    "完成昨天的任务",
    "更新周五的任务，优先级改为高",
    "更新上午的日程，地点改为图书馆",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND36_RELATIVE_TEMPORAL_TARGETS)
async def test_round36_relative_temporal_targets_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：任务B",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    ["删除明天的任务：", "删除明天的任务：理由", "更新周五的任务：优先级改为高"],
)
async def test_round36_temporal_selector_before_delimiter_fails_closed_without_llm(
    phrase: str,
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        phrase, now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    _assert_no_llm_unknown(result, llm)


_ROUND36_SAFETY_ONLY_SYNONYMS = (
    "增加",
    "新设",
    "创立",
    "创设",
    "添上",
    "添入",
    "写入",
    "导入",
    "导进",
    "插入",
    "纳入",
    "收录",
    "建个",
    "记下",
    "录下",
    "排入",
    "约上",
    "更改",
    "改动",
    "换掉",
    "替换",
    "换成",
    "挪动",
    "挪到",
    "延迟",
    "推延",
    "展期",
    "前移",
    "后移",
    "刷新",
    "重做",
    "校正",
    "校订",
    "舍弃",
    "遗弃",
    "弃置",
    "砍掉",
    "扔掉",
    "摘除",
    "踢出",
    "划掉",
    "勾掉",
    "清退",
    "消掉",
    "移开",
    "丢开",
    "住手",
    "停手",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("synonym", _ROUND36_SAFETY_ONLY_SYNONYMS)
async def test_round36_additional_unsupported_synonyms_fail_closed_without_llm(
    synonym: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    phrases = (
        (f"{synonym}任务：论文", ()),
        (f"{synonym}任务：论文", ("创建待办：论文",)),
        (f"不要{synonym}任务：论文", ()),
        (f"把任务B已经{synonym}了", ()),
        (f"创建待办：论文，{synonym}任务：报告", ()),
    )
    for phrase, context in phrases:
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND36_ABORT_ROOTS = (
    "不弄",
    "别删",
    "不要删除",
    "不用更新",
    "无需处理",
    "无须继续",
    "不必执行",
    "请勿撤销",
    "勿创建",
    "甭改",
    "请勿再添上",
    "勿再写入",
    "别再换掉",
    "不要再住手",
)
_ROUND36_ABORT_ENDINGS = (
    "",
    "了",
    "一下",
    "吧",
    "了吧",
    "一下吧",
    "啦",
    "了呢",
    "一下哦！",
    "了嘛？",
    "咯……",
    "了欸~",
    "了！",
    "一下？！",
    "了。",
    "了，",
    "了；",
    "了一下",
    "了一下呢",
    "一下了",
    "下去了",
    "起来了",
    "过了",
    "完了",
    "好啦",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("root", _ROUND36_ABORT_ROOTS)
@pytest.mark.parametrize("ending", _ROUND36_ABORT_ENDINGS)
async def test_round36_negated_action_aspect_orders_fail_closed_without_llm(
    root: str, ending: str
) -> None:
    phrase = f"{root}{ending}"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in (
        (phrase, ()),
        (phrase, ("更新任务：论文",)),
        (f"创建待办：论文，{phrase}", ()),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lead",
    ["", "请", "麻烦", "麻烦你", "拜托你", "劳烦您", "烦请您", "请帮我", "麻烦你帮忙"],
)
async def test_round36_polite_abort_leads_fail_closed_without_llm(lead: str) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        f"{lead}别再继续下去了",
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    _assert_no_llm_unknown(result, llm)


_ROUND36_NARRATIVE_MARKERS = (
    "方才",
    "当时",
    "那时",
    "当初",
    "早前",
    "先前",
    "上周",
    "上个月",
    "前阵子",
    "不久前",
    "如今",
    "现今",
    "眼下",
    "当前",
)
_ROUND36_NARRATIVE_MUTATIONS = (
    "删除",
    "移除",
    "撤销",
    "更新",
    "修改",
    "调整",
    "创建",
    "新增",
    "变更",
    "清理",
    "撤回",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", _ROUND36_NARRATIVE_MARKERS)
@pytest.mark.parametrize("mutation", _ROUND36_NARRATIVE_MUTATIONS)
async def test_round36_additional_temporal_narratives_fail_closed_without_llm(
    marker: str, mutation: str
) -> None:
    phrase = f"把任务B{marker}{mutation}"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in (
        (phrase, ()),
        (phrase, ("创建待办：任务B",)),
        (f"创建待办：论文，{phrase}", ()),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND36_COMPLETION_MUTATIONS = (
    "删除",
    "更新",
    "修改",
    "新增",
    "写入",
    "换掉",
    "刷新",
    "删",
    "改",
    "加",
)
_ROUND36_COMPLETION_TAILS = (
    "完啦",
    "完咯",
    "好啦",
    "好咯",
    "完喽",
    "完嘞",
    "妥了",
    "结束",
    "完事",
    "完事了",
    "过",
    "过啦",
    "着",
    "着呢",
    "到一半",
    "到一半了",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", _ROUND36_COMPLETION_MUTATIONS)
@pytest.mark.parametrize("tail", _ROUND36_COMPLETION_TAILS)
async def test_round36_completion_narratives_fail_closed_without_llm(
    mutation: str, tail: str
) -> None:
    phrase = f"把任务B{mutation}{tail}"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in (
        (phrase, ()),
        (phrase, ("更新任务：任务B",)),
        (f"创建待办：论文，{phrase}", ()),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND36_RESULT_SEPARATORS = ("", "：", ":", "，", ",", "；", ";", "——", "\n")
_ROUND36_RESULT_STATES = ("失败", "成功", "未果", "进行中")


@pytest.mark.asyncio
@pytest.mark.parametrize("separator", _ROUND36_RESULT_SEPARATORS)
@pytest.mark.parametrize("state", _ROUND36_RESULT_STATES)
@pytest.mark.parametrize("mutation", ["删除", "更新", "创建"])
async def test_round36_result_state_separators_fail_closed_without_llm(
    separator: str, state: str, mutation: str
) -> None:
    phrase = f"把任务B{mutation}{separator}{state}了"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in (
        (phrase, ()),
        (phrase, ("更新任务：任务B",)),
        (f"创建待办：论文，{phrase}", ()),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND36_RESULT_CONNECTORS = (
    "随即",
    "接下来",
    "紧接着",
    "转而",
    "后来",
    "但是",
    "不过",
    "可是",
    "而",
    "并",
    "且",
    "随后又",
    "之后才",
    "再后来",
    "结果又",
    "所以",
)
_ROUND36_RESULT_CONTINUATIONS = (
    "恢复成功",
    "恢复正常",
    "仍未恢复",
    "恢复中",
    "重试成功",
    "已经恢复",
    "改回原样",
    "重新建立",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("connector", _ROUND36_RESULT_CONNECTORS)
@pytest.mark.parametrize("continuation", _ROUND36_RESULT_CONTINUATIONS)
@pytest.mark.parametrize("mutation", ["删除", "修改", "更新"])
async def test_round36_result_continuations_fail_closed_without_llm(
    connector: str, continuation: str, mutation: str
) -> None:
    phrase = f"把任务B{mutation}失败{connector}{continuation}"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：任务B",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("left_quote", "right_quote"),
    [("“", "”"), ("「", "」"), ("《", "》"), ('"', '"'), ("'", "'"), ("‘", "’")],
)
@pytest.mark.parametrize("mutation", ["删除", "更新", "撤销"])
async def test_round36_partially_quoted_object_narratives_fail_closed_without_llm(
    left_quote: str, right_quote: str, mutation: str
) -> None:
    phrase = f"把{left_quote}任务B{right_quote}已经{mutation}了"
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        f"创建待办：论文，{phrase}",
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["小王会", "Alice将", "小王负责着", "小王将会", "山田太郎正在"])
async def test_round36_external_reminder_tense_prefixes_fail_closed_without_llm(
    prefix: str,
) -> None:
    phrase = f"{prefix}提前一天提醒我"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in ((f"创建待办：{phrase}", ()), (phrase, ("创建待办：交作业",))):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", ["从——", "从……", "从---", "从呢——"])
async def test_round36_decorated_dangling_from_fails_closed_without_llm(
    tail: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in (
        (f"创建日程：组会，明天3点{tail}", ()),
        (f"明天3点{tail}", ("创建日程：组会",)),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_intent"),
    [
        ("请把任务B删除吧", IntentName.DELETE_TASK),
        ("把任务B删除！", IntentName.DELETE_TASK),
        ("把任务B删掉吧", IntentName.DELETE_TASK),
        ("把任务B移除。", IntentName.DELETE_TASK),
        ("把任务B删除掉", IntentName.DELETE_TASK),
        ("把任务B修改好", IntentName.UPDATE_TASK),
        ("把任务B更新完", IntentName.UPDATE_TASK),
        ("把任务B删除干净", IntentName.DELETE_TASK),
    ],
)
async def test_round36_object_first_commands_keep_exact_target(
    phrase: str, expected_intent: IntentName
) -> None:
    result = await IntentParser().parse(
        phrase, now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assert result.intent == expected_intent
    assert result.slots.title == "B"


@pytest.mark.asyncio
async def test_round36_contextual_object_first_create_acceptance_controls() -> None:
    now = datetime(2026, 7, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    cases = (
        (
            "把机器学习考试加到日历，2026年7月18日上午九点",
            ("帮我创建一个日程：机器学习考试",),
            "机器学习考试",
            "2026-07-18",
            "09:00",
        ),
        (
            "把人工智能导论考试加到日历，2026年7月20日上午十点",
            ("人工智能导论考试安排在2026年7月20日上午十点，地点为教学楼B201。",),
            "人工智能导论考试",
            "2026-07-20",
            "10:00",
        ),
    )
    for text, context, title, date_value, time_value in cases:
        result = await IntentParser().parse(text, context=context, now=now)
        assert result.intent == IntentName.CREATE_EVENT
        assert result.missing_fields == []
        assert result.slots.title == title
        assert result.slots.date == date_value
        assert result.slots.start_time == time_value


@pytest.mark.asyncio
async def test_round36_explicit_and_quoted_positive_controls_remain_supported() -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    explicit = await IntentParser().parse("删除任务：昨天的任务", now=now)
    quoted = await IntentParser().parse("删除任务《周五的日程》", now=now)
    reschedule = await IntentParser().parse("把任务B推迟到明天", now=now)
    quoted_narrative = await IntentParser().parse("创建待办：“把任务B更新完啦”", now=now)
    assert explicit.intent == IntentName.DELETE_TASK
    assert explicit.slots.title == "昨天的任务"
    assert quoted.intent == IntentName.DELETE_TASK
    assert quoted.slots.title == "周五的日程"
    assert reschedule.intent == IntentName.UPDATE_TASK
    assert reschedule.slots.title == "B"
    assert reschedule.slots.due_date == "2026-07-31"
    assert quoted_narrative.intent == IntentName.CREATE_TASK
    assert quoted_narrative.slots.title == "“把任务B更新完啦”"


@pytest.mark.asyncio
async def test_round36_long_safety_guards_remain_linear() -> None:
    cases = (
        "创建待办：" + "A" * 9_700 + "将提前一天提醒我",
        "把任务B删除失败后又" + "等" * 4_800 + "恢复了",
        "删除" + "上周一" * 1_900 + "的任务",
    )
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text in cases:
        assert len(text) < 10_000
        llm = RecordingMutationLlm()
        started = perf_counter()
        result = await IntentParser(llm).parse(text, now=now)
        elapsed = perf_counter() - started
        _assert_no_llm_unknown(result, llm)
        assert elapsed < 1.0


_ROUND38_EXTERNAL_REMINDER_PREFIXES = (
    "小王会再",
    "小王打算",
    "小王准备",
    "小王正准备",
    "小王已经",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", _ROUND38_EXTERNAL_REMINDER_PREFIXES)
async def test_round38_external_reminder_plans_fail_closed_without_llm(
    prefix: str,
) -> None:
    phrase = f"{prefix}提前一天提醒我"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in (
        (phrase, ()),
        (phrase, ("创建待办：交作业",)),
        (f"创建待办：{phrase}", ()),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round38_colon_delimited_event_title_is_not_an_external_actor() -> None:
    result = await IntentParser().parse(
        "创建日程：明天下午三点项目组会，提前半小时提醒我。",
        now=datetime(2026, 7, 14, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "项目组会"
    assert result.slots.reminder_minutes == 30


@pytest.mark.asyncio
async def test_round38_polite_self_reminder_with_tense_marker_remains_supported() -> None:
    result = await IntentParser().parse(
        "创建待办：请你将提前一天提醒我",
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.reminder_minutes == 1_440


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", ["从哈", "从呗", "从___", "从——", "从……", "从---"])
async def test_round38_unrecognized_dangling_from_tails_fail_closed_without_llm(
    tail: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in (
        (f"创建日程：组会，明天3点{tail}", ()),
        (f"明天3点{tail}", ("创建日程：组会",)),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round38_lexical_from_continuation_remains_supported() -> None:
    result = await IntentParser().parse(
        "创建日程：明天3点从容讨论",
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == IntentName.CREATE_EVENT
    assert result.slots.title == "从容讨论"


_ROUND38_IMPLICIT_TEMPORAL_TARGETS = (
    "删除深夜的日程",
    "删除晚间的日程",
    "删除未来三天的任务",
    "删除这两天的任务",
    "删除下旬的任务",
    "删除昨日的任务",
    "删除次日的任务",
    "删除两天前的任务",
    "删除当天的任务",
    "把任务B昨日删除",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND38_IMPLICIT_TEMPORAL_TARGETS)
async def test_round38_implicit_temporal_target_families_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：任务B",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round38_explicit_temporal_titles_remain_supported() -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    explicit = await IntentParser().parse("删除任务：昨日的任务", now=now)
    quoted = await IntentParser().parse("删除任务《昨日的任务》", now=now)
    update = await IntentParser().parse("更新任务：明天的任务，优先级改为高", now=now)

    assert explicit.intent == IntentName.DELETE_TASK
    assert explicit.slots.title == "昨日的任务"
    assert quoted.intent == IntentName.DELETE_TASK
    assert quoted.slots.title == "昨日的任务"
    assert update.intent == IntentName.UPDATE_TASK
    assert update.slots.title == "明天的任务"
    assert update.slots.priority == "high"


_ROUND38_INVALID_OBJECT_FIRST_ORDERS = (
    "顺手把删除任务B",
    "把任务B说完再删除",
    "把要删除任务B",
    "顺手把要删除任务B",
    "把任务B准备删除",
    "把删除任务B",
    "把删除算法任务B删除",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND38_INVALID_OBJECT_FIRST_ORDERS)
async def test_round38_invalid_object_first_orders_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：任务B",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_intent"),
    [
        ("把任务B给删除吧", IntentName.DELETE_TASK),
        ("把任务B也更新哦", IntentName.UPDATE_TASK),
        ("把任务B都删除！", IntentName.DELETE_TASK),
        ("把任务B删除一下呗", IntentName.DELETE_TASK),
        ("把任务B删除彻底", IntentName.DELETE_TASK),
        ("把任务B修改妥当", IntentName.UPDATE_TASK),
        ("把任务B删除完毕", IntentName.DELETE_TASK),
        ("把任务B更新咯", IntentName.UPDATE_TASK),
    ],
)
async def test_round38_object_first_predicates_keep_exact_target(
    phrase: str,
    expected_intent: IntentName,
) -> None:
    result = await IntentParser().parse(
        phrase,
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert result.intent == expected_intent
    assert result.slots.title == "B"


_ROUND38_SAFETY_ONLY_SYNONYMS = (
    "补充",
    "设个",
    "预订",
    "置入",
    "编入",
    "存入",
    "载入",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("synonym", _ROUND38_SAFETY_ONLY_SYNONYMS)
@pytest.mark.parametrize("target", ["待办", "任务", "日程"])
async def test_round38_additional_mutation_synonyms_fail_closed_without_llm(
    synonym: str,
    target: str,
) -> None:
    phrase = f"{synonym}{target}：论文"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("更新任务：论文",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)

    negated = f"不要再{synonym}{target}了"
    negated_llm = RecordingMutationLlm()
    negated_result = await IntentParser(negated_llm).parse(negated, now=now)
    _assert_no_llm_unknown(negated_result, negated_llm)

    literal = await IntentParser().parse(f"创建待办：“{phrase}”", now=now)
    assert literal.intent == IntentName.CREATE_TASK
    assert literal.slots.title == f"“{phrase.replace('：', ':')}”"


_ROUND38_ABORT_VARIANTS = (
    "就到这里吧",
    "就到这儿吧",
    "停下来吧",
    "先停下来吧",
    "暂停一下吧",
    "先暂停吧",
    "先停一下",
    "先停一停",
    "停一停吧",
    "收手吧",
    "先收手吧",
    "别再往下做了",
    "不要继续往下做了",
    "不必继续做下去了",
    "不要再登记任务了",
    "别再登记待办了",
    "请勿登记任务",
    "不要再置入待办了",
    "别再编入任务了",
    "不要再存入待办了",
    "到这里为止吧",
    "到这儿为止吧",
    "到这里为止",
    "到这儿为止",
    "不用登记日程了",
    "不用载入日程了",
    "不用再继续了",
    "无需继续下去了",
    "别再折腾下去了",
    "不要再弄下去了",
    "不再做下去了",
    "不要弄完了",
    "不要再撤去任务了",
    "别再清走待办了",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND38_ABORT_VARIANTS)
async def test_round38_abort_variants_fail_closed_in_all_scopes_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in (
        (phrase, ()),
        (phrase, ("更新任务：论文",)),
        (f"创建待办：论文，{phrase}", ()),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND38_RESULT_REPORTS = (
    "删除任务B—成功了",
    "删除任务B-成功了",
    "删除任务B（成功了）",
    "删除任务B(成功了)",
    "删除任务B\t成功了",
    "更新任务B—失败了",
    "创建待办论文（失败了）",
    "删除任务B/成功了",
    "删除任务B｜成功了",
    "删除任务B|成功了",
    "删除任务B…成功了",
    "删除任务B · 成功了",
    "更新任务B/失败了",
    "移除任务B/还没成功",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND38_RESULT_REPORTS)
async def test_round38_result_reports_fail_closed_without_llm(phrase: str) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("更新任务：论文",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round38_result_state_literal_titles_remain_supported() -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    explicit = await IntentParser().parse("删除任务：成功了", now=now)
    quoted_delete = await IntentParser().parse("删除任务：“成功了”", now=now)
    quoted_create = await IntentParser().parse("创建待办：“失败了”", now=now)

    assert explicit.intent == IntentName.DELETE_TASK
    assert explicit.slots.title == "成功了"
    assert quoted_delete.intent == IntentName.DELETE_TASK
    assert quoted_delete.slots.title == "成功了"
    assert quoted_create.intent == IntentName.CREATE_TASK
    assert quoted_create.slots.title == "“失败了”"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    [
        "把任务B删除失败最终恢复了",
        "把任务B删除失败故而恢复了",
        "把任务B删除完毕所以恢复了",
    ],
)
async def test_round38_result_narrative_connectors_fail_closed_without_llm(
    phrase: str,
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        phrase,
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round38_new_safety_scans_remain_linear_near_input_limit() -> None:
    cases = (
        "创建待办：论文，" + "先" * 9_700 + "到这里为止吧",
        "创建待办：论文，先到这里为止" + "吧" * 9_700,
        "把任务B" + "已更新" * 3_000 + "尾",
    )
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text in cases:
        assert len(text) < 10_000
        llm = RecordingMutationLlm()
        started = perf_counter()
        result = await IntentParser(llm).parse(text, now=now)
        elapsed = perf_counter() - started
        _assert_no_llm_unknown(result, llm)
        assert elapsed < 1.0


_ROUND39_OBJECT_FIRST_POSITIVES = (
    ("把机器学习作业优先级改为高", IntentName.UPDATE_TASK, "机器学习作业"),
    ("把项目组会改到明天下午三点", IntentName.UPDATE_EVENT, "项目组会"),
    ("把任务论文优先级改为高", IntentName.UPDATE_TASK, "论文"),
    ("把任务“普通论文”重命名为“第二版”", IntentName.UPDATE_TASK, "普通论文"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_intent", "expected_title"),
    _ROUND39_OBJECT_FIRST_POSITIVES,
)
async def test_round39_structured_object_first_commands_remain_supported(
    phrase: str,
    expected_intent: IntentName,
    expected_title: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        result = await IntentParser().parse(phrase, context=context, now=now)
        assert result.intent == expected_intent
        assert result.slots.title == expected_title


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    (
        "把任务B说完再优先级改为高",
        "把任务B准备优先级改为高",
        "把任务B已经优先级改为高",
        "把任务B昨日优先级改为高",
        "把任务B说完再改到明天",
        "把任务B准备改到明天",
        "把任务B打算优先级改为高",
        "把任务B计划改到明天",
    ),
)
async def test_round39_object_first_field_narratives_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：任务B",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND39_ABORT_FINALIZER_VARIANTS = (
    "算啦",
    "算咯",
    "算吧",
    "算啦吧",
    "算咯吧",
    "算啦呢",
    "算咯呢",
    "算啦啊",
    "算啦！",
    "算啦算啦",
    "就算啦",
    "那就算啦",
    "就此作罢",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND39_ABORT_FINALIZER_VARIANTS)
async def test_round39_abort_finalizers_fail_closed_in_all_positions_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    cases = (
        (f"更新任务：论文标题改为初稿{phrase}", ()),
        (phrase, ("更新任务：论文",)),
        (f"创建待办：论文，{phrase}", ()),
    )
    for text, context in cases:
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round39_quoted_abort_finalizers_remain_literal() -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    created = await IntentParser().parse("创建待办：“论文算啦”", now=now)
    renamed = await IntentParser().parse(
        "更新任务：论文标题改为“初稿算啦”",
        now=now,
    )
    targeted = await IntentParser().parse(
        "更新任务：“论文算啦”标题改为初稿",
        now=now,
    )

    assert created.intent == IntentName.CREATE_TASK
    assert created.slots.title == "“论文算啦”"
    assert renamed.intent == IntentName.UPDATE_TASK
    assert renamed.slots.new_title == "初稿算啦"
    assert targeted.intent == IntentName.UPDATE_TASK
    assert targeted.slots.title == "论文算啦"


@pytest.mark.asyncio
async def test_round39_abort_suffix_scan_remains_bounded_near_input_limit() -> None:
    texts = (
        "更新任务：" + "A" * 9_700 + "标题改为初稿算啦吧",
        "更新任务：论文标题改为初稿算啦" + "吧" * 9_700,
    )
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text in texts:
        assert len(text) < 10_000
        llm = RecordingMutationLlm()
        started = perf_counter()
        result = await IntentParser(llm).parse(text, now=now)
        elapsed = perf_counter() - started

        _assert_no_llm_unknown(result, llm)
        assert elapsed < 1.0


_ROUND40_UNSAFE_OBJECT_PREFIXES = (
    "刚才",
    "刚才的",
    "刚刚",
    "方才",
    "当时",
    "那时",
    "当初",
    "早前",
    "先前",
    "之前",
    "之前那个",
    "此前",
    "当前",
    "目前",
    "现在",
    "如今",
    "现今",
    "眼下",
    "已经",
    "已",
    "现已",
    "曾经",
    "早就",
    "正在",
    "正",
    "还在",
    "仍在",
    "尚在",
    "随后",
    "之后",
    "后来",
    "最终",
    "故而",
    "所以",
    "然后",
    "接着",
    "准备",
    "打算",
    "计划",
    "预计",
    "决定",
    "考虑",
    "想要",
    "试图",
    "企图",
    "尝试",
    "说完再",
    "做完再",
    "弄完再",
    "处理完再",
    "待会",
    "等会",
    "马上",
    "立即",
    "将要",
    "即将",
    "可能",
    "应该",
    "需要",
    "由小王",
    "被小王",
    "成功的",
    "失败",
    "已成功",
    "进行中",
    "所有",
    "全部",
    "这些",
    "若干",
    "多个",
    "每个",
    "任意",
    "剩余",
    "上述",
    "以下",
    "大部分",
    "两个",
    "哪个",
    "哪一个",
    "什么",
    "刚才学习",
    "说完再项目",
    "准备学习",
    "所有项目",
    "哪个项目",
    "成功的项目",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", _ROUND40_UNSAFE_OBJECT_PREFIXES)
async def test_round40_object_prefix_selectors_fail_closed_without_llm(
    prefix: str,
) -> None:
    phrase = f"把{prefix}任务优先级改为高"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：任务B",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    (
        "把刚才的日程改到明天下午三点",
        "把当前项目组会改到明天下午三点",
        "把说完再项目组会改到明天下午三点",
    ),
)
async def test_round40_object_prefix_event_selectors_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建日程：项目组会",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND40_OBJECT_FIRST_POSITIVES = (
    ("把机器学习作业优先级改为高", IntentName.UPDATE_TASK, "机器学习作业"),
    ("把项目组会改到明天下午三点", IntentName.UPDATE_EVENT, "项目组会"),
    ("把软件工程课程改到明天下午三点", IntentName.UPDATE_EVENT, "软件工程课程"),
    ("把高等数学考试改到明天下午三点", IntentName.UPDATE_EVENT, "高等数学考试"),
    ("把安全培训讲座改到明天下午三点", IntentName.UPDATE_EVENT, "安全培训讲座"),
    ("把毕业答辩改到明天下午三点", IntentName.UPDATE_EVENT, "毕业答辩"),
    ("把人工智能导论考试改到明天下午三点", IntentName.UPDATE_EVENT, "人工智能导论考试"),
    ("把化学实验课程改到明天下午三点", IntentName.UPDATE_EVENT, "化学实验课程"),
    ("把课题组会改到明天下午三点", IntentName.UPDATE_EVENT, "课题组会"),
    ("把论文任务优先级改为高", IntentName.UPDATE_TASK, "论文"),
    ("把任务论文优先级改为高", IntentName.UPDATE_TASK, "论文"),
    ("把任务“普通论文”重命名为“第二版”", IntentName.UPDATE_TASK, "普通论文"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_intent", "expected_title"),
    _ROUND40_OBJECT_FIRST_POSITIVES,
)
async def test_round40_lexical_object_first_titles_remain_supported(
    phrase: str,
    expected_intent: IntentName,
    expected_title: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        result = await IntentParser().parse(phrase, context=context, now=now)
        assert result.intent == expected_intent
        assert result.slots.title == expected_title
        if expected_intent == IntentName.UPDATE_EVENT:
            assert result.slots.date == "2026-07-31"
            assert result.slots.start_time == "15:00"


_ROUND40_REPEATED_ABORTS = (
    "算了算了算了",
    "算啦算啦算啦",
    "算吧算吧算吧",
    "算了算啦算咯",
    "算啦算吧算了",
    "罢了罢了",
    "作罢作罢",
    "拉倒拉倒",
    "不要了不要了",
    "打住打住",
    "停下来停下来",
    "收手收手",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND40_REPEATED_ABORTS)
async def test_round40_repeated_aborts_fail_closed_in_all_positions_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    cases = (
        (phrase, ()),
        (phrase, ("更新任务：论文",)),
        (f"创建待办：论文，{phrase}", ()),
    )
    for text, context in cases:
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND40_UNQUOTED_RENAME_TAILS = (
    "了",
    "啦",
    "咯",
    "哈",
    "呗",
    "成功",
    "成功了",
    "失败了",
    "未果",
    "进行中",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("tail", _ROUND40_UNQUOTED_RENAME_TAILS)
async def test_round40_unquoted_rename_narrative_tails_fail_closed_without_llm(
    tail: str,
) -> None:
    phrase = f"把任务B标题改为初稿{tail}"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：任务B",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)

    quoted = await IntentParser().parse(f"把任务B标题改为“初稿{tail}”", now=now)
    assert quoted.intent == IntentName.UPDATE_TASK
    assert quoted.slots.title == "B"
    assert quoted.slots.new_title == f"初稿{tail}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    (
        "把任务B更新后优先级改为高",
        "把任务B更新之后优先级改为高",
        "把任务B修改然后优先级改为高",
        "把任务B调整随后优先级改为高",
        "把任务B更新最终优先级改为高",
    ),
)
async def test_round40_object_mutation_continuations_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：任务B",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lead",
    ("然后", "接着", "随后", "之后", "顺便", "再", "接下来", "最后", "先", "首先"),
)
async def test_round40_unsupported_object_command_leads_fail_closed_without_llm(
    lead: str,
) -> None:
    phrase = f"{lead}把任务B状态改为完成"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：任务B",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round40_supported_object_command_lead_remains_valid() -> None:
    result = await IntentParser().parse(
        "请把任务B状态改为完成",
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == "B"
    assert result.slots.status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_scope",
    (
        "汇报后项目任务",
        "沟通完毕项目任务",
        "讨论结束项目任务",
        "老师通知项目任务",
        "复盘结论项目任务",
    ),
)
async def test_round41_unanchored_object_titles_fail_closed_without_llm(
    target_scope: str,
) -> None:
    phrase = f"把{target_scope}优先级改为高"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：任务B",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_scope",
    (
        "汇报后软件工程课程",
        "沟通完毕化学实验课程",
    ),
)
async def test_round41_unanchored_object_event_titles_fail_closed_without_llm(
    target_scope: str,
) -> None:
    phrase = f"把{target_scope}改到明天下午三点"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：任务B",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lead",
    ("请", "帮我", "麻烦", "替我", "给我", "我想", "我要", "我需要"),
)
@pytest.mark.parametrize("marker", ("把", "将"))
async def test_round41_supported_object_command_leads_remain_valid(
    lead: str,
    marker: str,
) -> None:
    phrase = f"{lead}{marker}任务B状态改为完成"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        result = await IntentParser().parse(phrase, context=context, now=now)
        assert result.intent == IntentName.UPDATE_TASK
        assert result.slots.title == "B"
        assert result.slots.status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lead",
    (
        "请请",
        "帮我帮我",
        "汇报" * 40,
    ),
)
async def test_round41_unsupported_object_lead_lengths_fail_closed_without_llm(
    lead: str,
) -> None:
    phrase = f"{lead}把任务B状态改为完成"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("second_marker", ("把", "将"))
async def test_round41_second_object_marker_remains_ambiguous_without_llm(
    second_marker: str,
) -> None:
    phrase = f"请把任务A{second_marker}任务B状态改为完成"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    (
        "算了，算了",
        "罢了！罢了。",
        "作罢；作罢",
        "停下来、停下来",
    ),
)
async def test_round41_punctuated_repeated_aborts_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    cases = (
        (phrase, ()),
        (phrase, ("更新任务：论文",)),
        (f"创建待办：论文，{phrase}", ()),
    )
    for text, context in cases:
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_intent", "expected_title"),
    (
        ("创建待办：“算了算了算了”", IntentName.CREATE_TASK, "“算了算了算了”"),
        ("创建待办：算了算了算了方案", IntentName.CREATE_TASK, "算了算了算了方案"),
        (
            "把任务“算了算了算了”优先级改为高",
            IntentName.UPDATE_TASK,
            "算了算了算了",
        ),
    ),
)
async def test_round41_repeated_abort_literals_inside_titles_remain_valid(
    phrase: str,
    expected_intent: IntentName,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        phrase,
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert result.intent == expected_intent
    assert result.slots.title == expected_title


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_intent", "expected_title", "expected_new_title"),
    (
        ("创建待办：预算吧复盘", IntentName.CREATE_TASK, "预算吧复盘", None),
        ("创建待办：算吧算吧复盘", IntentName.CREATE_TASK, "算吧算吧复盘", None),
        (
            "创建待办：论文，算吧算吧复盘",
            IntentName.CREATE_TASK,
            "论文,算吧算吧复盘",
            None,
        ),
        (
            "更新任务：预算吧复盘优先级改为高",
            IntentName.UPDATE_TASK,
            "预算吧复盘",
            None,
        ),
        ("删除任务：算吧算吧复盘", IntentName.DELETE_TASK, "算吧算吧复盘", None),
        ("创建待办：“论文算吧算吧”", IntentName.CREATE_TASK, "“论文算吧算吧”", None),
        (
            "更新任务：论文标题改为“初稿算吧算吧”",
            IntentName.UPDATE_TASK,
            "论文",
            "初稿算吧算吧",
        ),
        (
            "更新任务：“算吧算吧复盘”优先级改为高",
            IntentName.UPDATE_TASK,
            "算吧算吧复盘",
            None,
        ),
    ),
)
async def test_round42_repeated_abort_scanner_does_not_match_title_content(
    phrase: str,
    expected_intent: IntentName,
    expected_title: str,
    expected_new_title: str | None,
) -> None:
    result = await IntentParser().parse(
        phrase,
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert result.intent == expected_intent
    assert result.slots.title == expected_title
    assert result.slots.new_title == expected_new_title


@pytest.mark.asyncio
async def test_round42_mixed_punctuated_repeated_abort_fails_closed_without_llm() -> None:
    phrase = "算了、算啦、算咯"
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in (
        (phrase, ()),
        (phrase, ("更新任务：论文",)),
        (f"创建待办：论文，{phrase}", ()),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_intent"),
    (
        ("把任务B修改好", IntentName.UPDATE_TASK),
        ("把任务B更新完", IntentName.UPDATE_TASK),
        ("把任务B给删除吧", IntentName.DELETE_TASK),
        ("把任务B也更新哦", IntentName.UPDATE_TASK),
        ("把任务B都删除！", IntentName.DELETE_TASK),
        ("把任务B修改妥当", IntentName.UPDATE_TASK),
        ("把任务B更新咯", IntentName.UPDATE_TASK),
    ),
)
async def test_round43_object_predicates_keep_exact_target(
    phrase: str,
    expected_intent: IntentName,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        result = await IntentParser().parse(phrase, context=context, now=now)
        assert result.intent == expected_intent
        assert result.slots.title == "B"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_title"),
    (
        ("把任务B课程改为软件工程", "B"),
        ("更新任务：论文，课程改为软件工程", "论文"),
    ),
)
async def test_round43_real_course_fields_keep_existing_boundary(
    phrase: str,
    expected_title: str,
) -> None:
    result = await IntentParser().parse(
        phrase,
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert result.intent == IntentName.UPDATE_TASK
    assert result.slots.title == expected_title


_ROUND44_PREFIXED_OR_EXTENDED_ABORTS = (
    "那就算了，算啦",
    "请罢了作罢",
    "麻烦不要了、收手",
    "请你停下来！打住",
    "，算了、算啦",
    "算了：算啦",
    "算了——算啦",
    "算了/算啦",
    "当我没说，算了",
    "到此为止，罢了",
    "这样，算了",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND44_PREFIXED_OR_EXTENDED_ABORTS)
async def test_round44_prefixed_or_extended_aborts_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in (
        (phrase, ()),
        (phrase, ("更新任务：论文",)),
        (f"创建待办：论文，{phrase}", ()),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat_count", (257, 4_999))
async def test_round44_long_repeated_aborts_fail_closed_without_llm(
    repeat_count: int,
) -> None:
    phrase = "算了" * repeat_count
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("更新任务：论文",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_intent"),
    (
        ("请 把任务B删除", IntentName.DELETE_TASK),
        ("麻烦 把任务B状态改为完成", IntentName.UPDATE_TASK),
        ("把任务B给 删除吧", IntentName.DELETE_TASK),
        ("把任务B也 更新哦", IntentName.UPDATE_TASK),
    ),
)
async def test_round44_spaced_object_commands_keep_exact_target(
    phrase: str,
    expected_intent: IntentName,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        result = await IntentParser().parse(phrase, context=context, now=now)
        assert result.intent == expected_intent
        assert result.slots.title == "B"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    (
        "把任务B也给删除",
        "把任务B都给删除",
        "把任务B给都删除",
        "把任务B给给给删除",
        "把任务B都都都删除",
        "把软件工程课程也都更新",
        "把任务都删除",
        "把待办都删除",
        "把项目任务都删除",
    ),
)
async def test_round44_ambiguous_object_predicates_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("total_length", (10_000, 10_001))
async def test_round44_parse_text_length_boundary_fails_closed_without_exception(
    total_length: int,
) -> None:
    suffix = "把任务B状态改为完成"
    phrase = "甲" * (total_length - len(suffix)) + suffix
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)
        assert len(result.source_text) == min(total_length, 10_000)
        assert result.source_text == phrase[:10_000]


_ROUND45_EXACT_OBJECT_TARGETS = (
    ("把任务“Project X”删除", IntentName.DELETE_TASK, "Project X", None),
    ("把任务Project X删除", IntentName.DELETE_TASK, "Project X", None),
    ("把任务“普通 论文”优先级改为高", IntentName.UPDATE_TASK, "普通 论文", "high"),
    ("把任务“B 给”优先级改为高", IntentName.UPDATE_TASK, "B 给", "high"),
    ("把任务“①”删除", IntentName.DELETE_TASK, "1", None),
    ("把任务“Ⅳ”删除", IntentName.DELETE_TASK, "IV", None),
    ("把任务“B\nC”删除", IntentName.DELETE_TASK, "B\nC", None),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_intent", "expected_title", "expected_priority"),
    _ROUND45_EXACT_OBJECT_TARGETS,
)
async def test_round45_object_commands_map_compact_boundaries_to_semantic_text(
    phrase: str,
    expected_intent: IntentName,
    expected_title: str,
    expected_priority: str | None,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        result = await IntentParser().parse(phrase, context=context, now=now)
        assert result.intent == expected_intent
        assert result.slots.title == expected_title
        assert result.slots.priority == expected_priority


_ROUND45_LEADING_PUNCTUATION_MUTATIONS = (
    "，删除任务B",
    "。更新任务B优先级改为高",
    "！完成任务B",
    "，请把任务B删除",
    "，创建待办：论文",
    "。创建日程：组会，明天下午三点",
    ";把任务B删除",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND45_LEADING_PUNCTUATION_MUTATIONS)
async def test_round45_leading_punctuation_mutations_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND45_SEPARATOR_WRAPPED_ABORTS = (
    "请，算了",
    "那就、算了、算啦",
    "麻烦！罢了作罢",
    "，算了",
    "！算了算啦",
    "算了，算啦：",
    "算了、算啦、",
    "罢了/作罢/",
    "算了——算啦——",
    "算了－算啦",
    "算了‒算啦",
    "嗯，算了",
    "好吧，算了、算啦",
    "行，当我没说",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND45_SEPARATOR_WRAPPED_ABORTS)
async def test_round45_separator_wrapped_aborts_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in (
        (phrase, ()),
        (phrase, ("更新任务：论文",)),
        (f"创建待办：论文，{phrase}", ()),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND45_ABORT_PREFIX_MUTATIONS = (
    "算了，创建待办：论文",
    "算了、算啦，创建待办：论文",
    "到此为止，创建待办：论文",
    "那就算了，创建待办：论文",
    "嗯，算了，创建待办：论文",
    "算了，创建日程：组会，明天下午3点",
    "算了，把任务B删除",
    "请，算了，更新任务B优先级改为高",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND45_ABORT_PREFIX_MUTATIONS)
async def test_round45_abort_prefix_before_mutation_fails_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND46_BOUNDARY_WHITESPACE = (
    "\t",
    "\v",
    "\f",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x1f",
    "\x85",
    "\u1680",
    "\u2028",
    "\u2029",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", _ROUND46_BOUNDARY_WHITESPACE)
async def test_round46_object_title_boundary_whitespace_is_not_preserved(
    boundary: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    cases = (
        (f"把任务{boundary}B{boundary}删除", IntentName.DELETE_TASK, "B", None),
        (f"请把事件{boundary}E1{boundary}删除", IntentName.DELETE_EVENT, "E1", None),
        (
            f"把任务{boundary}B{boundary}优先级改为高",
            IntentName.UPDATE_TASK,
            "B",
            "high",
        ),
    )
    for phrase, expected_intent, expected_title, expected_priority in cases:
        for context in ((), ("创建待办：无关上下文",)):
            result = await IntentParser().parse(phrase, context=context, now=now)
            assert result.intent == expected_intent
            assert result.slots.title == expected_title
            assert result.slots.priority == expected_priority


_ROUND46_COMMAND_GRAMMAR_WHITESPACE_CASES = (
    ("删除 任务B", IntentName.DELETE_TASK, "B", None),
    ("删除\t任务B", IntentName.DELETE_TASK, "B", None),
    ("更新 任务B 优先级改为高", IntentName.UPDATE_TASK, "B", "high"),
    ("请 删除 任务 B", IntentName.DELETE_TASK, "B", None),
    ("创建\t待办：论文", IntentName.CREATE_TASK, "论文", None),
    ("创建\u2028待办：论文", IntentName.CREATE_TASK, "论文", None),
    ("请 创建 待办：论文", IntentName.CREATE_TASK, "论文", None),
    ("请\t创建\v待办\u2028：论文", IntentName.CREATE_TASK, "论文", None),
    ("请\t把\v任务 B\u2028删除", IntentName.DELETE_TASK, "B", None),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_intent", "expected_title", "expected_priority"),
    _ROUND46_COMMAND_GRAMMAR_WHITESPACE_CASES,
)
async def test_round46_command_grammar_whitespace_does_not_leak_into_titles(
    phrase: str,
    expected_intent: IntentName,
    expected_title: str,
    expected_priority: str | None,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        result = await IntentParser().parse(phrase, context=context, now=now)
        assert result.intent == expected_intent
        assert result.slots.title == expected_title
        assert result.slots.priority == expected_priority


_ROUND46_ABORT_DISCOURSE_FILLERS = (
    "唉，算了",
    "好的，当我没说",
    "哎呀，算了",
    "对的，算了",
    "是的，算了",
)


_ROUND46_OBFUSCATED_ABORTS = (
    "算/了",
    "算_了",
    "算+了",
    "算😀了",
    "当/我/没/说",
    "当_我_没_说",
    "到\u2014此\u2014为\u2014止",
    "到_此_为_止",
    "罢/了",
    "停/下/来",
    "请/不/要/删/除/任/务/B",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND46_ABORT_DISCOURSE_FILLERS + _ROUND46_OBFUSCATED_ABORTS)
async def test_round46_aborts_with_discourse_or_separators_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text, context in (
        (phrase, ()),
        (phrase, ("更新任务：论文",)),
        (f"创建待办：论文，{phrase}", ()),
        (f"创建日程：组会，明天下午三点，{phrase}", ()),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND46_LEADING_MUTATION_PREFIXES = (
    "嗯，",
    "好吧，",
    "行，",
    "那个，",
    "这个，",
    "请，",
    "麻烦，",
    "帮我，",
    "替我，",
    "给我，",
    "我想，",
    "我要，",
    "我需要，",
    "首先，",
    "马上，",
    "顺便，",
    "还有，",
    "喂，",
    "#!",
    "#,",
    "$.",
    "%!",
    "*!",
    "+，",
    "<;",
    "💥#",
)


_ROUND46_MUTATION_PAYLOADS = (
    "创建待办：论文",
    "创建日程：组会，明天下午三点",
    "把任务B删除",
    "更新任务B优先级改为高",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", _ROUND46_LEADING_MUTATION_PREFIXES)
@pytest.mark.parametrize("payload", _ROUND46_MUTATION_PAYLOADS)
async def test_round46_leading_discourse_or_symbol_noise_fails_closed_without_llm(
    prefix: str,
    payload: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    phrase = f"{prefix}{payload}"
    for context in ((), ("创建待办：无关上下文",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


def test_round46_abort_prefix_scan_is_linear_without_a_mutation() -> None:
    phrase = "算了，" * 3_333 + "甲"
    started = perf_counter()
    assert _has_abort_prefix_before_mutation(phrase) is False
    assert perf_counter() - started < 1.0


_ROUND47_NFKC_EXPANDING_ABORT_SYMBOLS = ("™", "℠", "№", "℃", "℉", "㏄", "㍱")


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", _ROUND47_NFKC_EXPANDING_ABORT_SYMBOLS)
async def test_round47_nfkc_expanding_abort_symbols_fail_closed_without_llm(
    symbol: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    aborts = (f"算{symbol}了", f"当{symbol}我{symbol}没{symbol}说")
    for abort in aborts:
        for text, context in (
            (abort, ()),
            (abort, ("创建待办：无关上下文",)),
            (f"创建待办：论文，{abort}", ()),
            (f"{abort}，创建待办：论文", ()),
            (f"创建日程：组会，明天下午三点，{abort}", ()),
        ):
            llm = RecordingMutationLlm()
            result = await IntentParser(llm).parse(text, context=context, now=now)
            _assert_no_llm_unknown(result, llm)


_ROUND47_NFKC_EXPANDING_LEADING_SYMBOLS = (
    "¨",
    "¯",
    "´",
    "¸",
    "˘",
    "ˇ",
    "‾",
    "゛",
    "﹉",
    "￣",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", _ROUND47_NFKC_EXPANDING_LEADING_SYMBOLS)
async def test_round47_nfkc_expanding_leading_symbols_fail_closed_without_llm(
    symbol: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for payload in ("创建待办：论文", "删除任务B", "把任务B删除"):
        phrase = f"{symbol}{payload}"
        for context in ((), ("创建待办：无关上下文",)):
            llm = RecordingMutationLlm()
            result = await IntentParser(llm).parse(phrase, context=context, now=now)
            _assert_no_llm_unknown(result, llm)


_ROUND47_LEGAL_PUNCTUATED_OBJECT_TITLES = (
    ("把任务Project-X删除", IntentName.DELETE_TASK, "Project-X", None),
    ("把任务Project_X删除", IntentName.DELETE_TASK, "Project_X", None),
    ("把任务Project-X优先级改为高", IntentName.UPDATE_TASK, "Project-X", "high"),
    ("把任务“算™了”删除", IntentName.DELETE_TASK, "算TM了", None),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_intent", "expected_title", "expected_priority"),
    _ROUND47_LEGAL_PUNCTUATED_OBJECT_TITLES,
)
async def test_round47_legal_punctuated_object_titles_are_not_leading_noise(
    phrase: str,
    expected_intent: IntentName,
    expected_title: str,
    expected_priority: str | None,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("创建待办：无关上下文",)):
        result = await IntentParser().parse(phrase, context=context, now=now)
        assert result.intent == expected_intent
        assert result.slots.title == expected_title
        assert result.slots.priority == expected_priority


_ROUND48_NFKC_APOSTROPHE_COMPATIBILITY_CASES = (
    (
        "\u5220\u9664\u4efb\u52a1Brand\u2122's",
        IntentName.DELETE_TASK,
        "BrandTM's",
    ),
    (
        "\u521b\u5efa\u5f85\u529e\uff1aBrand\u2122's",
        IntentName.CREATE_TASK,
        "BrandTM's",
    ),
    (
        "\u521b\u5efa\u5f85\u529e\uff1a\u201cBrand\u2122's\u201d",
        IntentName.CREATE_TASK,
        "\u201cBrandTM's\u201d",
    ),
    (
        "\u5220\u9664\u4efb\u52a1Brand'\u2122s",
        IntentName.DELETE_TASK,
        "Brand'TMs",
    ),
    (
        "\u5220\u9664\u4efb\u52a1Brand\u2122\u200d's",
        IntentName.DELETE_TASK,
        "BrandTM's",
    ),
    (
        "\u521b\u5efa\u5f85\u529e\uff1aBrand\u2122\uff07s",
        IntentName.CREATE_TASK,
        "BrandTM's",
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("phrase", "expected_intent", "expected_title"),
    _ROUND48_NFKC_APOSTROPHE_COMPATIBILITY_CASES,
)
async def test_round48_nfkc_symbols_preserve_ascii_word_apostrophes(
    phrase: str,
    expected_intent: IntentName,
    expected_title: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in (
        (),
        ("\u521b\u5efa\u5f85\u529e\uff1a\u65e0\u5173\u4e0a\u4e0b\u6587",),
    ):
        result = await IntentParser().parse(phrase, context=context, now=now)
        assert result.intent == expected_intent
        assert result.slots.title == expected_title


_ROUND48_COLLOQUIAL_PREFIX_CASES = (
    "\u52a0\u6cb9\uff0c\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587",
    "\u6539\u5929\uff0c\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587",
    "\u5220\u7e41\u5c31\u7b80\uff0c\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND48_COLLOQUIAL_PREFIX_CASES)
async def test_round48_colloquial_prefixes_fail_closed_without_llm(phrase: str) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in (
        (),
        ("\u521b\u5efa\u5f85\u529e\uff1a\u65e0\u5173\u4e0a\u4e0b\u6587",),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND48_APOSTROPHE_WRAPPED_ABORT_CASES = (
    ("\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587\uff0c\u7b97\u2122'\u2120\u4e86", ()),
    (
        "\u521b\u5efa\u65e5\u7a0b\uff1a\u7ec4\u4f1a\uff0c\u660e\u5929\u4e0b\u5348\u4e09\u70b9\uff0c\u7b97\u2122'\u2120\u4e86",
        (),
    ),
    ("\u7b97\u2122'\u2120\u4e86", ("\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587",)),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("phrase", "context"), _ROUND48_APOSTROPHE_WRAPPED_ABORT_CASES)
async def test_round48_apostrophe_wrapped_aborts_fail_closed_without_llm(
    phrase: str,
    context: tuple[str, ...],
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        phrase,
        context=context,
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    _assert_no_llm_unknown(result, llm)


_ROUND48_EMPTY_QUOTE_ABORT_CASES = (
    ("\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587\uff0c\u7b97\u201c\u201d\u4e86", ()),
    (
        "\u521b\u5efa\u65e5\u7a0b\uff1a\u7ec4\u4f1a\uff0c\u660e\u5929\u4e0b\u5348\u4e09\u70b9\uff0c\u7b97\u201c\u201d\u4e86",
        (),
    ),
    ("\u7b97\u201c\u201d\u4e86\uff0c\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587", ()),
    ("\u7b97\u201c\u201d\u4e86", ("\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587",)),
    ("\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587\uff0c\u7b97\u0022\u0022\u4e86", ()),
    ("\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587\uff0c\u7b97\u0027\u0027\u4e86", ()),
    ("\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587\uff0c\u7b97\u300a\u300b\u4e86", ()),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("phrase", "context"), _ROUND48_EMPTY_QUOTE_ABORT_CASES)
async def test_round48_empty_quote_aborts_fail_closed_without_llm(
    phrase: str,
    context: tuple[str, ...],
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        phrase,
        context=context,
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round48_nonempty_quoted_title_is_not_an_abort() -> None:
    result = await IntentParser().parse(
        "\u521b\u5efa\u5f85\u529e\uff1a\u201c\u7b97\u4e86\u201d",
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert result.intent == IntentName.CREATE_TASK


_ROUND48_LEADING_COLLOQUIAL_CONFLICT_CASES = (
    "\u5220\u4efb\u52a1B\uff0c\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587",
    "\u6539\u4efb\u52a1B\uff0c\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587",
    "\u52a0\u4efb\u52a1B\uff0c\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587",
    "\u5220\u8fd9\u4e2a\u4efb\u52a1B\uff0c\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND48_LEADING_COLLOQUIAL_CONFLICT_CASES)
async def test_round48_leading_colloquial_conflicts_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in (
        (),
        ("\u521b\u5efa\u5f85\u529e\uff1a\u65e0\u5173\u4e0a\u4e0b\u6587",),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND48_UNRECOGNIZED_LEADING_COLLOQUIAL_CASES = (
    "\u5220\u4efb\u52a1B#\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587",
    "\u5220\u4efb\u52a1B\U0001f4a5\u521b\u5efa\u5f85\u529e\uff1a\u8bba\u6587",
    "\u5220\u4efb\u52a1B",
    "\u6539\u4efb\u52a1B",
    "\u52a0\u4efb\u52a1B",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND48_UNRECOGNIZED_LEADING_COLLOQUIAL_CASES)
async def test_round48_unrecognized_leading_colloquial_text_stays_unknown_without_llm(
    phrase: str,
) -> None:
    llm = RecordingMutationLlm()
    result = await IntentParser(llm).parse(
        phrase,
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    _assert_no_llm_unknown(result, llm)


_ROUND48_QUOTE_SEPARATED_WITHDRAW_ABORT_CASES = (
    "\u64a4\u300a\u300b\u9500\u300a\u300b\u8fd9\u300a\u300b\u6b21\u300a\u300b\u64cd\u300a\u300b\u4f5c",
    (
        "\u64a4\u201c \u201d\u9500\u201c \u201d\u8fd9"
        "\u201c \u201d\u6b21\u201c \u201d\u64cd"
        "\u201c \u201d\u4f5c"
    ),
    "\u64a4\u201c\u2122\u201d\u9500\u201c\u2122\u201d\u8fd9\u201c\u2122\u201d\u6b21\u201c\u2122\u201d\u64cd\u201c\u2122\u201d\u4f5c",
    "\u64a4\u201c\u200d\u201d\u9500\u201c\u200d\u201d\u8fd9\u201c\u200d\u201d\u6b21\u201c\u200d\u201d\u64cd\u201c\u200d\u201d\u4f5c",
    "\u64a4\u201c\u0301\u201d\u9500\u201c\u0301\u201d\u8fd9\u201c\u0301\u201d\u6b21\u201c\u0301\u201d\u64cd\u201c\u0301\u201d\u4f5c",
    (
        "\u64a4\u201c\u0000\u201d\u9500\u201c\u0000\u201d\u8fd9"
        "\u201c\u0000\u201d\u6b21\u201c\u0000\u201d\u64cd"
        "\u201c\u0000\u201d\u4f5c"
    ),
    (
        "\u64a4\u201c\u001b\u201d\u9500\u201c\u001b\u201d\u8fd9"
        "\u201c\u001b\u201d\u6b21\u201c\u001b\u201d\u64cd"
        "\u201c\u001b\u201d\u4f5c"
    ),
    (
        "\u64a4\u201c\ue000\u201d\u9500\u201c\ue000\u201d\u8fd9"
        "\u201c\ue000\u201d\u6b21\u201c\ue000\u201d\u64cd"
        "\u201c\ue000\u201d\u4f5c"
    ),
    (
        "\u64a4\u201c\U000f0000\u201d\u9500\u201c\U000f0000\u201d\u8fd9"
        "\u201c\U000f0000\u201d\u6b21\u201c\U000f0000\u201d\u64cd"
        "\u201c\U000f0000\u201d\u4f5c"
    ),
    (
        "\u64a4\u201c\u0378\u201d\u9500\u201c\u0378\u201d\u8fd9"
        "\u201c\u0378\u201d\u6b21\u201c\u0378\u201d\u64cd"
        "\u201c\u0378\u201d\u4f5c"
    ),
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND48_QUOTE_SEPARATED_WITHDRAW_ABORT_CASES)
async def test_round48_quote_separated_withdraw_aborts_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in (
        (),
        ("\u521b\u5efa\u5f85\u529e\uff1a\u65e0\u5173\u4e0a\u4e0b\u6587",),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round48_nonempty_quoted_withdraw_title_is_not_an_abort() -> None:
    result = await IntentParser().parse(
        "\u521b\u5efa\u5f85\u529e\uff1a\u201c\u64a4\u9500\u8fd9\u6b21\u64cd\u4f5c\u201d",
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "\u201c\u64a4\u9500\u8fd9\u6b21\u64cd\u4f5c\u201d"


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", ("\u5220\u6389\u4efb\u52a1B", "\u628a\u4efb\u52a1B\u5220\u6389"))
async def test_round48_recognized_first_clause_commands_are_not_colloquial_conflicts(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in (
        (),
        ("\u521b\u5efa\u5f85\u529e\uff1a\u65e0\u5173\u4e0a\u4e0b\u6587",),
    ):
        result = await IntentParser().parse(phrase, context=context, now=now)
        assert result.intent == IntentName.DELETE_TASK


_ROUND48_ADJACENT_ABORT_PREFIX_MUTATION_CASES = (
    "\u7b97\u4e86\u505c\u6b62\u4e00\u4e0b\u7136\u540e\u4efb\u52a1B\u4e5f\u5220\u9664",
    "\u505c\u6b62\u505c\u6b62\u7136\u540e\u4efb\u52a1B\u4e5f\u5220\u9664",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", _ROUND48_ADJACENT_ABORT_PREFIX_MUTATION_CASES)
async def test_round48_adjacent_abort_prefix_mutations_fail_closed_without_llm(
    phrase: str,
) -> None:
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in (
        (),
        ("\u521b\u5efa\u5f85\u529e\uff1a\u65e0\u5173\u4e0a\u4e0b\u6587",),
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


@pytest.mark.asyncio
async def test_round48_quoted_abort_and_mutation_words_remain_a_literal_title() -> None:
    result = await IntentParser().parse(
        "\u521b\u5efa\u5f85\u529e\uff1a\u201c\u7b97\u4e86\u5220\u9664\u4efb\u52a1B\u201d",
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == "\u201c\u7b97\u4e86\u5220\u9664\u4efb\u52a1B\u201d"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ("\u0000", "\ue000", "\u0378"))
async def test_round48_nonsemantic_quoted_titles_remain_literal(content: str) -> None:
    result = await IntentParser().parse(
        f"\u521b\u5efa\u5f85\u529e\uff1a\u201c{content}\u201d",
        now=datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    assert result.intent == IntentName.CREATE_TASK
    assert result.slots.title == f"\u201c{content}\u201d"


_ROUND48_NONSEMANTIC_ABORT_SEPARATORS = (
    "\u0000",
    "\u0001",
    "\u001b",
    "\ue000",
    "\U000f0000",
    "\u0378",
    "\u0301",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("separator", _ROUND48_NONSEMANTIC_ABORT_SEPARATORS)
async def test_round48_nonsemantic_separated_withdraw_aborts_fail_closed_without_llm(
    separator: str,
) -> None:
    phrase = separator.join("\u64a4\u9500\u8fd9\u6b21\u64cd\u4f5c")
    now = datetime(2026, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    for context in ((), ("\u521b\u5efa\u5f85\u529e\uff1a\u65e0\u5173\u4e0a\u4e0b\u6587",)):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(phrase, context=context, now=now)
        _assert_no_llm_unknown(result, llm)


_ROUND49_UNSAFE_NONSEMANTIC_MUTATION_SEPARATORS = (
    "\x00",
    "\u200d",
    "\ue000",
    "\u0378",
    "\u0301",
    "\u0903",
    "\u20dd",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("separator", _ROUND49_UNSAFE_NONSEMANTIC_MUTATION_SEPARATORS)
async def test_round49_nonsemantic_mutation_injections_fail_closed_without_llm(
    separator: str,
) -> None:
    now = datetime(2026, 8, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    for text in (
        f"\u521b{separator}\u5efa\u5f85\u529e\uff1a\u8bba\u6587",
        f"\u5220{separator}\u9664\u4efb\u52a1B",
    ):
        llm = RecordingMutationLlm()
        result = await IntentParser(llm).parse(text, now=now)
        _assert_no_llm_unknown(result, llm)

    contextual_llm = RecordingMutationLlm()
    contextual = await IntentParser(contextual_llm).parse(
        f"\u660e{separator}\u5929\u4e0b\u5348\u4e09\u70b9",
        context=("\u521b\u5efa\u65e5\u7a0b\uff1a\u7ec4\u4f1a",),
        now=now,
    )
    _assert_no_llm_unknown(contextual, contextual_llm)

    polluted_context_llm = RecordingMutationLlm()
    polluted_context = await IntentParser(polluted_context_llm).parse(
        "\u660e\u5929\u4e0b\u5348\u4e09\u70b9",
        context=(f"\u521b{separator}\u5efa\u65e5\u7a0b\uff1a\u7ec4\u4f1a",),
        now=now,
    )
    _assert_no_llm_unknown(polluted_context, polluted_context_llm)
