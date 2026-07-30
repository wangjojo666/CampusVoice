from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.schemas.intent import IntentName, IntentResult
from app.services.intent import IntentParseError, IntentParser
from app.services.intent.parser import _strip_update_trailing_connectors


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
        ("更新任务：2026-07-30计划，优先级改为高", IntentName.UPDATE_TASK, "2026-07-30计划"),
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
        "创建日程：明天下午15:00-16:00组会",
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
async def test_llm_mutation_requires_matching_deterministic_authority(text: str) -> None:
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
