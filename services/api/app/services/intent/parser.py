import json
import re
import unicodedata
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, NamedTuple, Protocol
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.core.metrics import InMemoryMetrics, observe_component
from app.schemas.intent import IntentName, IntentResult, IntentSlots


class IntentParseError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class IntentLlmClient(Protocol):
    async def extract(self, text: str, context: Sequence[str]) -> str: ...

    async def repair(self, text: str, invalid_output: str, validation_error: str) -> str: ...


_SYSTEM_PROMPT = """你是 CampusVoice 的结构化意图抽取器。只返回一个 JSON 对象，不要 Markdown。
intent 只能是 create_task, update_task, delete_task, create_event, update_event,
delete_event, search_notice, query_schedule, unknown。日期用 YYYY-MM-DD，时间用 HH:MM。
不要猜测用户没说的信息；缺失值使用 null。slots 只允许已声明字段。
顶层必须且只能包含 intent, confidence, slots, missing_fields, ambiguities, source_text,
requires_confirmation；slots 只能包含 Schema 声明的字段，即使为空也必须返回对象。
你只负责抽取，程序会独立计算缺失字段、风险和是否确认。"""


class OpenAICompatibleIntentClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_seconds: float = 20,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds

    async def _complete(self, messages: list[dict[str, str]]) -> str:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        payload = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
            return str(body["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise IntentParseError(
                "llm_unavailable",
                "意图理解服务暂时不可用，请稍后重试或编辑文本后再试。",
            ) from exc

    async def extract(self, text: str, context: Sequence[str]) -> str:
        context_text = "\n".join(context[-5:]) if context else "（无）"
        return await self._complete(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"上下文：\n{context_text}\n\n当前用户文本：\n{text}",
                },
            ]
        )

    async def repair(self, text: str, invalid_output: str, validation_error: str) -> str:
        return await self._complete(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "上一次 JSON 不符合 Schema。只修复结构，不添加用户未提供的信息。\n"
                        f"原始文本：{text}\n"
                        f"无效输出：{invalid_output}\n"
                        f"校验错误：{validation_error[:2000]}"
                    ),
                },
            ]
        )


_MUTATING_INTENTS = {
    IntentName.CREATE_TASK,
    IntentName.UPDATE_TASK,
    IntentName.DELETE_TASK,
    IntentName.CREATE_EVENT,
    IntentName.UPDATE_EVENT,
    IntentName.DELETE_EVENT,
}

_REQUIRED_SLOTS: dict[IntentName, tuple[str, ...]] = {
    IntentName.CREATE_TASK: ("title",),
    IntentName.UPDATE_TASK: ("task_id_or_title",),
    IntentName.DELETE_TASK: ("task_id_or_title",),
    IntentName.CREATE_EVENT: ("title", "date", "start_time"),
    IntentName.UPDATE_EVENT: ("event_id_or_title",),
    IntentName.DELETE_EVENT: ("event_id_or_title",),
    IntentName.SEARCH_NOTICE: ("query",),
}


def _json_object(raw: str) -> dict[str, Any]:
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("top-level LLM output must be an object")
    return parsed


def _find_date(text: str, today: date) -> str | None:
    relative = {"今天": 0, "今晚": 0, "明天": 1, "后天": 2}
    for token, offset in relative.items():
        if token in text:
            return (today + timedelta(days=offset)).isoformat()
    full = re.search(r"(?P<year>20\d{2})[年\-/](?P<month>\d{1,2})[月\-/](?P<day>\d{1,2})日?", text)
    if full:
        try:
            return date(
                int(full.group("year")),
                int(full.group("month")),
                int(full.group("day")),
            ).isoformat()
        except ValueError:
            return None
    short = re.search(r"(?P<month>\d{1,2})月(?P<day>\d{1,2})[日号]", text)
    if short:
        try:
            candidate = date(today.year, int(short.group("month")), int(short.group("day")))
            return candidate.isoformat()
        except ValueError:
            return None
    return None


_CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _chinese_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[value]
    if "十" not in value:
        return None
    tens_text, ones_text = value.split("十", 1)
    tens = 1 if not tens_text else _CHINESE_DIGITS.get(tens_text)
    ones = 0 if not ones_text else _CHINESE_DIGITS.get(ones_text)
    if tens is None or ones is None:
        return None
    return tens * 10 + ones


def _hour_number(value: str) -> int | None:
    return _chinese_number(value)


_REMINDER_PATTERN = re.compile(
    r"提前\s*(?:"
    r"(?P<half>半)\s*(?:个)?\s*小时|"
    r"(?P<amount>\d{1,4}|[零一二两三四五六七八九十]{1,3})\s*(?:个)?\s*"
    r"(?P<unit>分钟|小时|天)"
    r")\s*(?:提醒|通知)(?:我)?"
)


def _find_reminder_minutes(text: str) -> int | None:
    match = _REMINDER_PATTERN.search(text)
    if match is None:
        return None
    if match.group("half"):
        return 30
    amount = _chinese_number(match.group("amount"))
    if amount is None:
        return None
    multiplier = {"分钟": 1, "小时": 60, "天": 1440}[match.group("unit")]
    minutes = amount * multiplier
    return minutes if 0 <= minutes <= 525_600 else None


def _without_reminder_phrases(text: str) -> str:
    return _REMINDER_PATTERN.sub("", text)


_TIME_FRAGMENT_PATTERN = re.compile(
    r"(?:(?P<period>凌晨|早上|上午|中午|下午|晚上|今晚))?"
    r"(?P<hour>\d{1,2}|[零一二两三四五六七八九十]{1,3})"
    r"(?:[:点时](?P<minute>\d{1,2})?分?)"
)
_CONTEXT_DATE_FRAGMENT_PATTERN = re.compile(
    r"(?:今天|今晚|明天|后天|20\d{2}[年\-/]\d{1,2}[月\-/]\d{1,2}日?|"
    r"\d{1,2}月\d{1,2}[日号])"
)


def _is_pure_temporal_clarification(text: str) -> bool:
    remainder = _without_reminder_phrases(text)
    if len(list(_CONTEXT_DATE_FRAGMENT_PATTERN.finditer(remainder))) > 1:
        return False
    remainder = _CONTEXT_DATE_FRAGMENT_PATTERN.sub("", remainder)
    remainder = _TIME_FRAGMENT_PATTERN.sub("", remainder)
    return re.fullmatch(r"[从到至和及、，,。.!！?？:：]*", remainder) is not None


def _find_times(text: str) -> tuple[str | None, str | None]:
    text = _without_reminder_phrases(text)
    matches = list(_TIME_FRAGMENT_PATTERN.finditer(text))
    values: list[str] = []
    first_period = ""
    for index, match in enumerate(matches[:2]):
        hour = _hour_number(match.group("hour"))
        if hour is None:
            continue
        minute = int(match.group("minute") or 0)
        explicit_period = match.group("period") or ""
        if index == 0:
            first_period = explicit_period
        period = explicit_period or (first_period if index > 0 else "")
        if period in {"下午", "晚上", "今晚"} and hour < 12:
            hour += 12
        if period == "中午" and hour < 11:
            hour += 12
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            continue
        values.append(f"{hour:02d}:{minute:02d}")
    return (values[0] if values else None, values[1] if len(values) > 1 else None)


_QUOTED_LITERAL_PATTERN = re.compile(
    r'(《[^》]*》|“[^”]*”|「[^」]*」|『[^』]*』|"[^"]*"|\'[^\']*\')'
)
_UPDATE_FIELD_MODIFIER_PATTERN = re.compile(
    r"(?:的)?(?:标题|优先级|状态|截止时间|截止日期|时间|日期|地点)?"
    r"(?:改名为|重命名为|改为|改成|更新为|调整为|设为|标记为|改到|推迟到|提前到)"
)
_TEMPORAL_META_TITLE_PATTERN = re.compile(
    r"^(?:研究|学习|讨论|比较|分析|整理|记录|了解|阅读).+"
    r"(?:的)?(?:含义|概念|(?:这个)?表达|语义|字样|说法|用法|词句|词语|短语|"
    r"意思|区别|差异|原因|原理|方法|教程|示例|例子)$"
)


def _without_temporal_phrases_outside_quotes(text: str) -> str:
    parts = _QUOTED_LITERAL_PATTERN.split(text)
    cleaned_parts: list[str] = []
    for part in parts:
        if _QUOTED_LITERAL_PATTERN.fullmatch(part) is not None:
            cleaned_parts.append(part)
            continue
        time_matches = list(_TIME_FRAGMENT_PATTERN.finditer(part))
        range_spans = [
            (left.end(), right.start())
            for left, right in zip(time_matches, time_matches[1:], strict=False)
            if re.fullmatch(
                r"(?:到|至|[-~～—–－])",
                part[left.end() : right.start()],
            )
            is not None
        ]
        cleaned = part
        for start, end in reversed(range_spans):
            cleaned = cleaned[:start] + cleaned[end:]
        cleaned = _without_reminder_phrases(cleaned)
        cleaned = _CONTEXT_DATE_FRAGMENT_PATTERN.sub("", cleaned)
        cleaned = _TIME_FRAGMENT_PATTERN.sub("", cleaned)
        cleaned = re.sub(
            r"(^|[，,。.!！?？；;])(?:截止|开始)(?=$|[，,。.!！?？；;])",
            r"\1",
            cleaned,
        )
        cleaned_parts.append(cleaned)
    return "".join(cleaned_parts)


def _split_temporal_meta_title(text: str) -> tuple[str, str] | None:
    match = re.match(r"(?P<title>[^，,。.!！?？；;]+)(?P<trailing>.*)$", text)
    if match is None:
        return None
    title = match.group("title")
    has_temporal_literal = (
        _REMINDER_PATTERN.search(title) is not None
        or _CONTEXT_DATE_FRAGMENT_PATTERN.search(title) is not None
        or _TIME_FRAGMENT_PATTERN.search(title) is not None
    )
    if not has_temporal_literal or _TEMPORAL_META_TITLE_PATTERN.search(title) is None:
        return None
    return title, match.group("trailing")


def _without_create_title_temporal_phrases(text: str) -> str:
    meta_title = _split_temporal_meta_title(text)
    if meta_title is None:
        return _without_temporal_phrases_outside_quotes(text)
    title, trailing = meta_title
    return title + _without_temporal_phrases_outside_quotes(trailing)


def _temporal_slot_scope(text: str, intent: IntentName) -> str:
    normalized = _compact_semantic_text(text)
    if intent in {IntentName.DELETE_TASK, IntentName.DELETE_EVENT}:
        return ""
    if intent in {IntentName.UPDATE_TASK, IntentName.UPDATE_EVENT}:
        title_delimiter = _title_delimiter_position(normalized)
        if title_delimiter is None:
            return normalized
        explicit_title = normalized[title_delimiter + 1 :]
        modifier = _UPDATE_FIELD_MODIFIER_PATTERN.search(explicit_title)
        return explicit_title[modifier.start() :] if modifier is not None else ""
    if intent in {IntentName.CREATE_TASK, IntentName.CREATE_EVENT}:
        title_delimiter = _title_delimiter_position(normalized)
        if title_delimiter is not None:
            title = normalized[title_delimiter + 1 :]
            meta_title = _split_temporal_meta_title(title)
            if meta_title is not None:
                normalized = meta_title[1]
        return _QUOTED_LITERAL_PATTERN.sub("", normalized)
    return normalized


def _has_multiple_date_fragments(text: str) -> bool:
    return len(list(_CONTEXT_DATE_FRAGMENT_PATTERN.finditer(text))) > 1


def _has_non_increasing_time_range(text: str) -> bool:
    start_time, end_time = _find_times(text)
    return start_time is not None and end_time is not None and end_time <= start_time


def _has_ambiguous_mutation_temporal_scope(text: str) -> bool:
    return _has_multiple_date_fragments(text) or _has_non_increasing_time_range(text)


def _extract_title(text: str, intent: IntentName) -> str | None:
    candidates: list[str] = []
    object_first = re.match(r"^(?:(?:请|帮我|麻烦|替我|给我|我想|我要|我需要))*(?:把|将)", text)
    if object_first is not None:
        after = text[object_first.end() :]
        candidates.append(re.split(r"(?:加到|加入|添加到|放到|设为|创建成)", after, maxsplit=1)[0])
    candidates.append(
        re.sub(
            r"^(?:(?:请|帮我|麻烦|替我|给我|我想|我要|我需要|先|首先|马上|"
            r"立刻|立即|稍后|再次|分别|别忘了|不要忘记|记得))*"
            r"(?:创建|新建|添加|记一个|安排)"
            r"(?:(?:(?:一个)|(?:1|一)(?:个|项|条|门|份|场|次|节))"
            r"(?:待办|任务|作业|日历事件|日历|日程|事件|组会|会议|考试|答辩|讲座|课程)"
            r"|(?:待办|任务|作业|日历事件|日历|日程|事件|组会|会议|考试|答辩|讲座|课程)"
            r"(?=[：:，, ])|(?:待办|任务|作业|日历事件|日历|日程|事件))?"
            r"[：:，, ]*",
            "",
            text,
        )
    )
    for candidate in candidates:
        cleaned = _without_create_title_temporal_phrases(candidate)
        cleaned = re.sub(
            r"(?:加到|加入|添加到|放到)(?:我的)?(?:日历|日程|待办)(?:里)?",
            "",
            cleaned,
        )

        cleaned = cleaned.strip("，,。.!！?？ ：:")
        if intent == IntentName.CREATE_TASK:
            cleaned = re.sub(r"^(?:待办|任务)[：:，, ]*", "", cleaned).strip()
        if cleaned and cleaned not in {"待办", "任务", "日程", "事件", "日历"}:
            return cleaned
    return None


_UPDATE_TRAILING_SINGLE_CHARACTERS = frozenset("，,。.!！?？；;：:")
_UPDATE_TRAILING_TOKENS = ("然后", "并且", "接着", "随后", "之后", "顺便", "再", "把", "将")


def _strip_update_trailing_connectors(text: str) -> str:
    end = len(text)
    while end:
        previous_end = end
        while end and (
            text[end - 1] in _UPDATE_TRAILING_SINGLE_CHARACTERS or text[end - 1].isspace()
        ):
            end -= 1
        for token in _UPDATE_TRAILING_TOKENS:
            if text.endswith(token, 0, end):
                end -= len(token)
                break
        if end == previous_end:
            break
    return text[:end]


def _extract_target_title(text: str, intent: IntentName) -> str | None:
    explicit_title = _title_delimiter_position(_compact_semantic_text(text)) is not None
    candidate = text.strip()
    object_first = re.match(
        r"^(?:(?:请|帮我|麻烦|替我|给我|我想|我要|我需要))*(?:把|将)", candidate
    )
    if object_first is not None:
        candidate = candidate[object_first.end() :]
    candidate = re.sub(
        r"^(?:(?:请|帮我|麻烦|替我|给我|我想|我要|我需要|先|首先|马上|"
        r"立刻|立即|稍后|再次|分别))*"
        r"(?:删除|删掉|移除|取消|修改|更新|调整|完成)",
        "",
        candidate,
    )
    candidate = re.sub(
        r"^(?:这个|那个|上次的|之前的)?"
        r"(?:待办|任务|作业|日历事件|日历|日程|事件|组会|会议|考试|答辩|讲座|课程)[：:，, ]*",
        "",
        candidate,
    )
    if object_first is not None:
        candidate = re.sub(
            r"(?:删除|删掉|移除|取消|修改|更新|调整|完成)(?:一下|一遍|了)?$",
            "",
            candidate,
        )
    if intent in {IntentName.UPDATE_TASK, IntentName.UPDATE_EVENT}:
        candidate = _UPDATE_FIELD_MODIFIER_PATTERN.split(candidate, maxsplit=1)[0]
        candidate = re.split(
            r"到(?:今天|今晚|明天|后天|20\d{2}[年\-/]|\d{1,2}月)", candidate, maxsplit=1
        )[0]
        candidate = _strip_update_trailing_connectors(candidate)
    if not explicit_title:
        candidate = _without_temporal_phrases_outside_quotes(candidate)
        candidate = re.sub(r"(?:这个)?(?:待办|任务|日历事件|日程|事件)$", "", candidate)
    cleaned = candidate.strip("，,。.!！?？ ：:")
    return cleaned or None


def _new_title(text: str) -> str | None:
    match = re.search(r"(?:改名为|重命名为|标题改为)[：:，, ]*(?P<title>[^，,。.!！?？]+)", text)
    return match.group("title").strip() if match else None


class _IntentSignals(NamedTuple):
    create: bool
    update: bool
    delete: bool
    query: bool
    explicit_event: bool
    explicit_task: bool
    mutation_commands: int
    raw_mutation: bool
    non_imperative: bool

    @property
    def conflicting(self) -> bool:
        mutation_kinds = sum((self.create, self.update, self.delete))
        return (
            mutation_kinds > 1
            or (self.explicit_event and self.explicit_task)
            or (self.raw_mutation and (mutation_kinds != 1 or self.mutation_commands != 1))
            or self.non_imperative
        )

    @property
    def query_mutation_conflict(self) -> bool:
        return self.query and self.raw_mutation


_CREATE_SIGNALS = ("创建", "新建", "添加", "加到", "加入", "安排", "记一个")
_UPDATE_SIGNALS = (
    "修改",
    "更新",
    "改为",
    "改成",
    "改到",
    "调整",
    "推迟",
    "提前",
    "改名",
    "重命名",
    "标记",
    "完成",
)
_DELETE_SIGNALS = ("删除", "删掉", "移除", "取消")
_QUERY_SIGNALS = (
    "查询",
    "查看",
    "搜索",
    "查找",
    "查一下",
    "找一下",
    "看一下",
    "列一下",
    "列出",
    "显示",
    "看看",
    "找找",
    "想知道",
    "想看",
    "想了解",
    "了解一下",
    "告诉我",
    "什么时候",
    "有哪些",
    "有什么",
)
_MUTATION_SIGNAL_PATTERN = "|".join(
    re.escape(signal)
    for signal in sorted(
        {*_CREATE_SIGNALS, *_UPDATE_SIGNALS, *_DELETE_SIGNALS},
        key=len,
        reverse=True,
    )
)
_INDEPENDENT_MUTATION_SIGNALS = (
    *_CREATE_SIGNALS,
    "修改",
    "更新",
    "调整",
    "推迟",
    "提前",
    *_DELETE_SIGNALS,
)
_INDEPENDENT_MUTATION_SIGNAL_PATTERN = "|".join(
    re.escape(signal)
    for signal in sorted(set(_INDEPENDENT_MUTATION_SIGNALS), key=len, reverse=True)
)
_RAW_MUTATION_SIGNAL_PATTERN = rf"(?:{_MUTATION_SIGNAL_PATTERN}|删|改|加)"
_CREATE_SIGNAL_PATTERN = "|".join(
    re.escape(signal) for signal in sorted(_CREATE_SIGNALS, key=len, reverse=True)
)
_DELETE_SIGNAL_PATTERN = "|".join(
    re.escape(signal) for signal in sorted(_DELETE_SIGNALS, key=len, reverse=True)
)
_EXISTING_TARGET_MUTATION_SIGNAL_PATTERN = "|".join(
    re.escape(signal)
    for signal in sorted({*_UPDATE_SIGNALS, *_DELETE_SIGNALS}, key=len, reverse=True)
)
_COLLOQUIAL_VERB_PATTERN = r"(?:删(?!除)|改(?!为|成|到|名)|加(?!入|到))"
_TARGET_SIGNAL_PATTERN = (
    r"(?:待办|任务|作业|日历事件|日历|日程|事件|组会|会议|考试|答辩|讲座|课程|它|这个|那个)"
)
_NOMINAL_ARRANGEMENTS = ("日程安排", "会议安排", "课程安排", "工作安排", "考试安排")
_NEGATED_IMPERATIVE_MUTATION_PATTERN = re.compile(
    rf"(?:我|我们)?(?:请)?"
    rf"(?:不需要|不可以|不要|不用|无需|不必|请勿|勿|禁止|不能|不准|不可|"
    rf"不得|不应|不想|不愿|不再|不(?!要|用|需|必|可|准|得|应|想|愿|再)|别)"
    rf"(?!忘)[^：:]*?{_RAW_MUTATION_SIGNAL_PATTERN}"
)
_NEGATED_STATEMENT_MUTATION_PATTERN = re.compile(
    rf"(?:没有|还没|尚未|未(?!来)|没|不曾)"
    rf"[^。.!！?？；;：:]*?{_RAW_MUTATION_SIGNAL_PATTERN}"
)
_COMMAND_PREFIX_PATTERN = (
    r"(?:(?:请|帮我|麻烦|替我|给我|我想|我要|我需要|先|首先|马上|立刻|立即|"
    r"稍后|再次|分别|别忘了|不要忘记|记得|顺手|顺便|最后|也))*"
)
_SEQUENTIAL_COMMAND_SEPARATOR_PATTERN = (
    rf"(?:[，,。.!！?？；;、]+|并且|然后|同时|接着|接下来|随后|之后|随即|随之|"
    rf"继而|顺便|最后|再|又|且|后(?={_COMMAND_PREFIX_PATTERN}(?:(?:把|将)|"
    rf"(?:{_MUTATION_SIGNAL_PATTERN})|{_TARGET_SIGNAL_PATTERN})))"
)
_STRONG_COMMAND_SEPARATOR_PATTERN = re.compile(_SEQUENTIAL_COMMAND_SEPARATOR_PATTERN)
_COMMAND_SEPARATOR_PATTERN = re.compile(
    rf"(?:{_SEQUENTIAL_COMMAND_SEPARATOR_PATTERN}|以及|或者|和|与|并)"
)
_DIRECT_MUTATION_COMMAND_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}(?:{_MUTATION_SIGNAL_PATTERN})"
)
_OBJECT_FIRST_MUTATION_COMMAND_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}(?:把|将)"
    rf"[^，,。.!！?？；;：:]{{0,128}}?(?:{_MUTATION_SIGNAL_PATTERN})"
)
_TARGET_FIRST_NARRATIVE_MARKER_PATTERN = (
    r"(?:由|被|让|昨天|前天|刚刚?|已经|已|曾经|此前|之前|不能|不要|无需|不用|"
    r"不必|别|失败)"
)
_TARGET_FIRST_MUTATION_COMMAND_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}{_TARGET_SIGNAL_PATTERN}"
    rf"(?![^，,。.!！?？；;：:]{{0,64}}{_TARGET_FIRST_NARRATIVE_MARKER_PATTERN})"
    rf"[^，,。.!！?？；;：:]{{0,64}}?(?:给|也|都)?"
    rf"(?:{_MUTATION_SIGNAL_PATTERN})(?:掉|了)?"
)
_MULTIPLE_TARGET_MUTATION_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}(?:{_MUTATION_SIGNAL_PATTERN})"
    rf"[^：:]*?{_TARGET_SIGNAL_PATTERN}[^：:]*?"
    rf"(?:以及|和|与|、|或者|或|跟|及|还有|/|&|\+|\||｜|·|•)"
    rf"[^：:]*?{_TARGET_SIGNAL_PATTERN}"
)
_BULK_TARGET_QUANTIFIER_PATTERN = (
    r"(?:多个|所有|全部|这些|若干|前几个|一批|每个|任意|剩余|上述|以下|一切|"
    r"大部分|部分|(?:\d+|[零〇一二两三四五六七八九十百千万几]+)"
    r"(?:个|项|条|门|份|场|次|节)|(?:各|每一?|任意一?)(?:个|项|条|门|份|场|次|节)?)"
)
_BULK_TARGET_MUTATION_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}(?:{_EXISTING_TARGET_MUTATION_SIGNAL_PATTERN})"
    rf"(?:(?!{_TARGET_SIGNAL_PATTERN})[^：:]){{0,32}}?"
    rf"{_BULK_TARGET_QUANTIFIER_PATTERN}{_TARGET_SIGNAL_PATTERN}"
)
_CREATE_BULK_TARGET_MUTATION_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}(?:{_CREATE_SIGNAL_PATTERN})"
    rf"(?:(?!{_TARGET_SIGNAL_PATTERN})[^：:]){{0,32}}?"
    rf"(?!(?:1|一)(?:个|项|条|门|份|场|次|节){_TARGET_SIGNAL_PATTERN})"
    rf"{_BULK_TARGET_QUANTIFIER_PATTERN}{_TARGET_SIGNAL_PATTERN}"
)
_IMPLICIT_MULTIPLE_TARGET_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}(?:{_DELETE_SIGNAL_PATTERN})"
    rf"[^：:]*?{_TARGET_SIGNAL_PATTERN}[^，,。.!！?？；;：:、]{{1,64}}"
    rf"(?:、|，|,|以及|和|与|或者|或|跟|及|还有|/|&|\+|\||｜|·|•)[^：:]+"
)
_REPEATED_INDEPENDENT_MUTATION_PATTERN = re.compile(
    rf"(?:{_MUTATION_SIGNAL_PATTERN})[^：:]*?"
    rf"(?:{_INDEPENDENT_MUTATION_SIGNAL_PATTERN})[^：:]*?{_TARGET_SIGNAL_PATTERN}"
)
_REPEATED_MUTATION_PREDICATE_PATTERN = re.compile(
    rf"(?P<repeated_mutation>{_MUTATION_SIGNAL_PATTERN})"
    rf"[^：:]{{0,96}}?{_TARGET_SIGNAL_PATTERN}[^：:]{{0,96}}?"
    rf"(?P=repeated_mutation)[^：:]{{0,96}}?{_TARGET_SIGNAL_PATTERN}"
)

_COLLOQUIAL_MUTATION_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}(?:"
    rf"(?:把|将)?{_TARGET_SIGNAL_PATTERN}"
    rf"(?![^，,。.!！?？；;：:]{{0,24}}{_TARGET_FIRST_NARRATIVE_MARKER_PATTERN})"
    rf"[^，,。.!！?？；;：:]{{0,24}}?{_COLLOQUIAL_VERB_PATTERN}"
    rf"(?:掉|一下|一遍|了)?|{_COLLOQUIAL_VERB_PATTERN}(?:掉|一下|一遍|了)?"
    rf"[^，,。.!！?？；;：:]{{0,24}}?{_TARGET_SIGNAL_PATTERN})"
)
_NON_IMPERATIVE_MUTATION_PATTERN = re.compile(
    r"(?:只是|仅仅是|不过是|属于|系|由[^：:]{0,32}(?:负责|操作|完成)|"
    r"为[^：:]{0,32}(?:例子|示例)|是[^：:]{0,32}(?:的|记录|例子|示例)|"
    r"为什么|为何|何时|是否|能否|可否|是什么意思|什么(?:意思|含义)|的原因|"
    r"(?:要|该)?(?:怎么|如何)(?:操作|做|弄|恢复|处理)|的方法|的步骤|会(?:怎样|如何)|"
    r"怎么回事|(?:了|过)?(?:吗|么|呢)[。.!！?？]?$)"
)
_TITLE_META_PREFIX_PATTERN = re.compile(r"^(?:研究|学习|讨论|比较|分析|整理|记录|了解|阅读)")
_TITLE_META_SUFFIX_PATTERN = re.compile(
    r"(?:的(?:区别|差异|含义|概念|方法|教程|示例|例子|原因|原理)|"
    r"是什么意思|什么(?:意思|含义))$"
)
_ABORT_PATTERN = re.compile(
    r"^(?:算了|不要了|不用了|取消|停止|别动(?:它|这个|任务|待办|日程|事件)?|"
    r"别动了|不弄了|先这样)(?:吧)?[。.!！?？]?$"
)


def _semantic_intent_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(character for character in normalized if unicodedata.category(character) != "Cf")


def _compact_semantic_text(text: str) -> str:
    normalized = _semantic_intent_text(text)
    normalized = re.sub(r"[\r\n]+", ";", normalized)
    return "".join(character for character in normalized if not character.isspace())


def _normalized_intent_text(text: str) -> str:
    return _without_reminder_phrases(_compact_semantic_text(text))


def _mutation_signal_scope(scope: str) -> str:
    for phrase in _NOMINAL_ARRANGEMENTS:
        scope = scope.replace(phrase, phrase.removesuffix("安排"))
    return scope


def _title_delimiter_position(normalized: str) -> int | None:
    for match in re.finditer(r"[：:]", normalized):
        position = match.start()
        if (
            position > 0
            and position + 1 < len(normalized)
            and normalized[position - 1].isdigit()
            and normalized[position + 1].isdigit()
        ):
            continue
        prefix = normalized[:position]
        signal_scope = _mutation_signal_scope(prefix)
        if (
            any(target in prefix for target in ("待办", "任务", "日历", "日程", "事件"))
            and re.search(_MUTATION_SIGNAL_PATTERN, signal_scope) is not None
        ):
            return position
    return None


def _is_meta_title_description(title: str) -> bool:
    if _TITLE_META_PREFIX_PATTERN.search(title) is not None:
        return True
    return (
        _TITLE_META_SUFFIX_PATTERN.search(title) is not None
        and len(re.findall(_MUTATION_SIGNAL_PATTERN, title)) > 1
    )


def _command_scope(normalized: str) -> str:
    title_delimiter = _title_delimiter_position(normalized)
    if title_delimiter is None:
        return normalized
    prefix = normalized[:title_delimiter]
    title = normalized[title_delimiter + 1 :]
    separator = (
        _STRONG_COMMAND_SEPARATOR_PATTERN
        if _is_meta_title_description(title)
        else _COMMAND_SEPARATOR_PATTERN
    )
    title_clauses = separator.split(title)
    trailing_commands = [
        clause for clause in title_clauses[1:] if _is_explicit_mutation_clause(clause)
    ]
    return ";".join((prefix, *trailing_commands))


def _explicit_target_scope(normalized: str) -> str:
    return re.split(r"[，,。.!！?？；;、]", _command_scope(normalized), maxsplit=1)[0]


def _mutation_command_count(command_scope: str) -> int:
    clauses = [clause for clause in _COMMAND_SEPARATOR_PATTERN.split(command_scope) if clause]
    count = sum(
        _DIRECT_MUTATION_COMMAND_PATTERN.search(clause) is not None
        or (
            _OBJECT_FIRST_MUTATION_COMMAND_PATTERN.search(clause) is not None
            and re.search(_TARGET_SIGNAL_PATTERN, clause) is not None
        )
        or (
            index > 0
            and (
                _TARGET_FIRST_MUTATION_COMMAND_PATTERN.search(clause) is not None
                or _COLLOQUIAL_MUTATION_PATTERN.search(clause) is not None
            )
        )
        for index, clause in enumerate(clauses)
    )
    if (
        _MULTIPLE_TARGET_MUTATION_PATTERN.search(command_scope) is not None
        or _BULK_TARGET_MUTATION_PATTERN.search(command_scope) is not None
        or _CREATE_BULK_TARGET_MUTATION_PATTERN.search(command_scope) is not None
        or _IMPLICIT_MULTIPLE_TARGET_PATTERN.search(command_scope) is not None
        or _REPEATED_INDEPENDENT_MUTATION_PATTERN.search(command_scope) is not None
        or _REPEATED_MUTATION_PREDICATE_PATTERN.search(command_scope) is not None
    ):
        return max(count, 2)
    return count


def _is_explicit_mutation_clause(clause: str) -> bool:
    return re.search(_TARGET_SIGNAL_PATTERN, clause) is not None and (
        _DIRECT_MUTATION_COMMAND_PATTERN.search(clause) is not None
        or _OBJECT_FIRST_MUTATION_COMMAND_PATTERN.search(clause) is not None
        or _TARGET_FIRST_MUTATION_COMMAND_PATTERN.search(clause) is not None
        or _COLLOQUIAL_MUTATION_PATTERN.search(clause) is not None
    )


def _has_create_signal(command_scope: str) -> bool:
    signal_scope = _mutation_signal_scope(command_scope)
    return any(signal in signal_scope for signal in _CREATE_SIGNALS)


def _has_raw_mutation(command_scope: str) -> bool:
    signal_scope = _mutation_signal_scope(command_scope)
    return re.search(_RAW_MUTATION_SIGNAL_PATTERN, signal_scope) is not None


def _explicitly_negates_mutation(normalized: str) -> bool:
    command_scope = _command_scope(normalized)
    return (
        _NEGATED_IMPERATIVE_MUTATION_PATTERN.search(command_scope) is not None
        or _NEGATED_STATEMENT_MUTATION_PATTERN.search(command_scope) is not None
    )


def _explicitly_aborts(normalized: str) -> bool:
    return _ABORT_PATTERN.fullmatch(normalized) is not None


def _intent_signals(normalized: str) -> _IntentSignals:
    command_scope = _command_scope(normalized)
    signal_scope = _mutation_signal_scope(command_scope)
    target_scope = _explicit_target_scope(normalized)
    return _IntentSignals(
        create=_has_create_signal(command_scope),
        update=any(word in signal_scope for word in _UPDATE_SIGNALS),
        delete=any(word in signal_scope for word in _DELETE_SIGNALS),
        query=any(word in command_scope for word in _QUERY_SIGNALS),
        explicit_event=any(word in target_scope for word in ("日历", "日程", "事件")),
        explicit_task=any(word in target_scope for word in ("待办", "任务")),
        mutation_commands=_mutation_command_count(command_scope),
        raw_mutation=_has_raw_mutation(command_scope),
        non_imperative=(
            _has_raw_mutation(command_scope)
            and _NON_IMPERATIVE_MUTATION_PATTERN.search(command_scope) is not None
        ),
    )


def _classify_intent(text: str) -> IntentName:
    normalized = _normalized_intent_text(text)
    signals = _intent_signals(normalized)
    event_signal = any(
        word in normalized
        for word in ("日历", "日程", "事件", "会议", "组会", "考试", "答辩", "讲座", "课程")
    )
    task_signal = any(word in normalized for word in ("待办", "任务", "作业", "复习"))

    notice_signal = any(word in normalized for word in ("通知", "公告", "报名", "奖学金", "教务"))
    if (
        _explicitly_aborts(normalized)
        or _explicitly_negates_mutation(normalized)
        or signals.conflicting
        or signals.query_mutation_conflict
    ):
        return IntentName.UNKNOWN

    # An explicitly named target type must outrank words in the title. For
    # example, "创建待办：整理日程安排" is a task even though "日程" is also
    # a broad event signal.
    if signals.explicit_event != signals.explicit_task:
        if signals.delete:
            return IntentName.DELETE_EVENT if signals.explicit_event else IntentName.DELETE_TASK
        if signals.create:
            return IntentName.CREATE_EVENT if signals.explicit_event else IntentName.CREATE_TASK
        if signals.update:
            return IntentName.UPDATE_EVENT if signals.explicit_event else IntentName.UPDATE_TASK

    if signals.delete and event_signal:
        return IntentName.DELETE_EVENT
    if signals.delete and task_signal:
        return IntentName.DELETE_TASK
    if signals.create and event_signal:
        return IntentName.CREATE_EVENT
    if signals.create and task_signal:
        return IntentName.CREATE_TASK
    if signals.update and event_signal:
        return IntentName.UPDATE_EVENT
    if signals.update and task_signal:
        return IntentName.UPDATE_TASK
    if notice_signal and (
        signals.query or not (signals.create or signals.update or signals.delete)
    ):
        return IntentName.SEARCH_NOTICE
    if event_signal and signals.query:
        return IntentName.QUERY_SCHEDULE
    return IntentName.UNKNOWN


def _unknown_result(source_text: str) -> IntentResult:
    return IntentResult(
        intent=IntentName.UNKNOWN,
        confidence=0.25,
        slots=IntentSlots(),
        missing_fields=[],
        ambiguities=[],
        source_text=source_text,
        requires_confirmation=False,
    )


def _fallback_parse_single(text: str, now: datetime) -> IntentResult:
    semantic_text = _semantic_intent_text(text)
    normalized = re.sub(r"\s+", "", semantic_text)
    intent = _classify_intent(semantic_text)
    if intent == IntentName.UNKNOWN:
        return _unknown_result(text)

    temporal_scope = _temporal_slot_scope(semantic_text, intent)
    if intent in _MUTATING_INTENTS and _has_ambiguous_mutation_temporal_scope(temporal_scope):
        return _unknown_result(text)
    parsed_date = _find_date(temporal_scope, now.date())
    start_time, end_time = _find_times(temporal_scope)
    reminder_minutes = _find_reminder_minutes(temporal_scope)
    if intent in {IntentName.CREATE_TASK, IntentName.CREATE_EVENT}:
        title = _extract_title(semantic_text, intent)
    elif intent in {
        IntentName.UPDATE_TASK,
        IntentName.DELETE_TASK,
        IntentName.UPDATE_EVENT,
        IntentName.DELETE_EVENT,
    }:
        title = _extract_target_title(semantic_text, intent)
    else:
        title = None
    slots = IntentSlots(
        title=title,
        new_title=(
            _new_title(semantic_text)
            if intent in {IntentName.UPDATE_TASK, IntentName.UPDATE_EVENT}
            else None
        ),
        reminder_minutes=reminder_minutes,
    )
    if intent in {IntentName.CREATE_EVENT, IntentName.UPDATE_EVENT}:
        slots.date = parsed_date
        slots.start_time = start_time
        slots.end_time = end_time
    elif intent in {IntentName.CREATE_TASK, IntentName.UPDATE_TASK}:
        slots.due_date = parsed_date
        slots.due_time = start_time
        slot_signal_scope = (
            _command_scope(_normalized_intent_text(semantic_text))
            if intent == IntentName.CREATE_TASK
            else normalized
        )
        if "未完成" in slot_signal_scope or "恢复为待办" in slot_signal_scope:
            slots.status = "pending"
        elif "完成" in slot_signal_scope:
            slots.status = "completed"
        if "高优先级" in slot_signal_scope or re.search(
            r"优先级(?:改为|改成|调整为|设为)?高", slot_signal_scope
        ):
            slots.priority = "high"
        elif "低优先级" in slot_signal_scope or re.search(
            r"优先级(?:改为|改成|调整为|设为)?低", slot_signal_scope
        ):
            slots.priority = "low"
    elif intent == IntentName.SEARCH_NOTICE:
        slots.query = semantic_text.strip()
    elif intent == IntentName.QUERY_SCHEDULE:
        slots.date = parsed_date
    return IntentResult(
        intent=intent,
        confidence=0.80,
        slots=slots,
        missing_fields=[],
        ambiguities=[],
        source_text=text,
        requires_confirmation=intent in _MUTATING_INTENTS,
    )


def _continue_from_context(
    text: str,
    context: Sequence[str],
    now: datetime,
) -> IntentResult | None:
    semantic_text = _semantic_intent_text(text)
    normalized = re.sub(r"\s+", "", semantic_text)
    if _explicitly_aborts(_normalized_intent_text(semantic_text)):
        return None
    parsed_date = _find_date(normalized, now.date())
    start_time, end_time = _find_times(normalized)
    reminder_minutes = _find_reminder_minutes(normalized)
    if not _is_pure_temporal_clarification(normalized):
        return None
    for previous_text in reversed(context):
        if _explicitly_aborts(_normalized_intent_text(previous_text)):
            return None
        previous = _enforce_policy(_fallback_parse_single(previous_text, now), None)
        if previous.intent == IntentName.UNKNOWN:
            return None
        if previous.intent not in _MUTATING_INTENTS:
            return None
        missing_fields = set(previous.missing_fields)
        if not missing_fields:
            return None
        supplies_missing_field = ("date" in missing_fields and parsed_date is not None) or (
            "start_time" in missing_fields and start_time is not None
        )
        if not supplies_missing_field:
            return None

        slots = previous.slots.model_copy(deep=True)
        if previous.intent in {IntentName.CREATE_EVENT, IntentName.UPDATE_EVENT}:
            slots.date = parsed_date or slots.date
            slots.start_time = start_time or slots.start_time
            slots.end_time = end_time or slots.end_time
            slots.reminder_minutes = (
                reminder_minutes if reminder_minutes is not None else slots.reminder_minutes
            )
        elif previous.intent in {IntentName.CREATE_TASK, IntentName.UPDATE_TASK}:
            slots.due_date = parsed_date or slots.due_date
            slots.due_time = start_time or slots.due_time
            slots.reminder_minutes = (
                reminder_minutes if reminder_minutes is not None else slots.reminder_minutes
            )
            if "未完成" in normalized:
                slots.status = "pending"
            elif "完成" in normalized:
                slots.status = "completed"

        return IntentResult(
            intent=previous.intent,
            confidence=0.74,
            slots=slots,
            missing_fields=[],
            ambiguities=[],
            source_text=text,
            requires_confirmation=True,
        )
    return None


def _fallback_parse(text: str, now: datetime, context: Sequence[str] = ()) -> IntentResult:
    current = _fallback_parse_single(text, now)
    if current.intent != IntentName.UNKNOWN:
        return current
    normalized = _normalized_intent_text(text)
    signals = _intent_signals(normalized)
    if (
        _explicitly_aborts(normalized)
        or _explicitly_negates_mutation(normalized)
        or signals.conflicting
        or signals.query_mutation_conflict
    ):
        return current
    return _continue_from_context(text, context, now) or current


def _enforce_policy(result: IntentResult, asr_confidence: float | None) -> IntentResult:
    slots = result.slots
    missing: list[str] = []
    for required in _REQUIRED_SLOTS.get(result.intent, ()):
        if required == "task_id_or_title":
            if not slots.task_id and not slots.title:
                missing.append(required)
        elif required == "event_id_or_title":
            if not slots.event_id and not slots.title:
                missing.append(required)
        elif getattr(slots, required) is None:
            missing.append(required)

    ambiguities = list(dict.fromkeys(result.ambiguities))
    if re.search(r"(?:那个|这个|上次|之前的|这个考试)", result.source_text) and not (
        slots.task_id or slots.event_id
    ):
        ambiguities.append("指代不明确，需要确认具体对象")
    if asr_confidence is not None and asr_confidence < 0.65 and result.intent in _MUTATING_INTENTS:
        ambiguities.append("语音识别置信度较低，请确认关键字段")

    return result.model_copy(
        update={
            "missing_fields": list(dict.fromkeys(missing)),
            "ambiguities": list(dict.fromkeys(ambiguities)),
            "requires_confirmation": result.intent in _MUTATING_INTENTS,
        }
    )


def _enrich_deterministically(
    result: IntentResult,
    text: str,
    now: datetime,
) -> IntentResult:
    slots = result.slots.model_copy(deep=True)
    temporal_scope = _temporal_slot_scope(text, result.intent)
    parsed_date = _find_date(temporal_scope, now.date())
    start_time, end_time = _find_times(temporal_scope)
    reminder_minutes = _find_reminder_minutes(temporal_scope)

    if reminder_minutes is not None:
        slots.reminder_minutes = reminder_minutes
    if result.intent in {IntentName.CREATE_EVENT, IntentName.UPDATE_EVENT}:
        slots.date = parsed_date or slots.date
        slots.start_time = start_time or slots.start_time
        slots.end_time = end_time or slots.end_time
    elif result.intent in {IntentName.CREATE_TASK, IntentName.UPDATE_TASK}:
        slots.due_date = parsed_date or slots.due_date
        slots.due_time = start_time or slots.due_time
    return result.model_copy(update={"slots": slots})


def _metadata_value_has_explicit_cue(
    field: str,
    candidate: str,
    source_text: str,
) -> bool:
    escaped = re.escape(candidate)
    negated_pattern = (
        rf"(?:不要|不用|无需|不必|请勿|勿|禁止|不能|不准|不可|不得|不应|"
        rf"不想|不愿|不再|别|不是|并非|非|不在|没有|尚未|未(?!来)|没|不曾)"
        rf"[^，,。.!！?？；;:]{{0,16}}{escaped}"
    )
    if re.search(negated_pattern, source_text) is not None:
        return False
    if field == "description":
        pattern = (
            rf"(?:描述|备注|说明|内容)(?:为|是|:)?{escaped}|{escaped}(?:作为)?(?:描述|备注|说明)"
        )
    elif field == "course":
        pattern = rf"(?:课程|科目)(?:为|是|:)?{escaped}|{escaped}(?:课程|课)"
    elif field == "location":
        pattern = (
            rf"(?:地点|位置)(?:为|是|:)?{escaped}|(?:在|位于)[^，,。.!！?？；;:]{{0,8}}?{escaped}"
        )
    else:
        return False
    return re.search(pattern, source_text) is not None


def _grounded_llm_metadata_value(
    field: str,
    value: str,
    source_text: str,
    *,
    min_length: int,
    max_length: int,
) -> str | None:
    candidate = value.strip()
    if not min_length <= len(candidate) <= max_length:
        return None
    if any(unicodedata.category(character).startswith("C") for character in candidate):
        return None
    normalized_candidate = re.sub(r"\s+", "", _semantic_intent_text(candidate)).casefold()
    normalized_source = re.sub(r"\s+", "", _semantic_intent_text(source_text)).casefold()
    if not normalized_candidate or normalized_candidate not in normalized_source:
        return None
    if not _metadata_value_has_explicit_cue(field, normalized_candidate, normalized_source):
        return None
    return candidate


def _merge_safe_llm_mutation_metadata(
    deterministic: IntentResult,
    parsed: IntentResult,
) -> IntentResult:
    candidates: list[tuple[str, str | None, int, int]] = []
    if deterministic.intent in {
        IntentName.CREATE_TASK,
        IntentName.UPDATE_TASK,
        IntentName.CREATE_EVENT,
        IntentName.UPDATE_EVENT,
    }:
        candidates.extend(
            [
                ("description", parsed.slots.description, 2, 4_000),
                ("course", parsed.slots.course, 2, 160),
            ]
        )
    if deterministic.intent in {IntentName.CREATE_EVENT, IntentName.UPDATE_EVENT}:
        candidates.append(("location", parsed.slots.location, 2, 240))

    metadata: dict[str, str] = {}
    for field, value, min_length, max_length in candidates:
        if value is None:
            continue
        grounded = _grounded_llm_metadata_value(
            field,
            value,
            deterministic.source_text,
            min_length=min_length,
            max_length=max_length,
        )
        if grounded is not None:
            metadata[field] = grounded
    if not metadata:
        return deterministic
    return deterministic.model_copy(
        update={"slots": deterministic.slots.model_copy(update=metadata)}
    )


class IntentParser:
    def __init__(
        self,
        llm_client: IntentLlmClient | None = None,
        *,
        timezone_name: str = "Asia/Shanghai",
        metrics: InMemoryMetrics | None = None,
    ) -> None:
        self._llm = llm_client
        self._timezone = ZoneInfo(timezone_name)
        self._metrics = metrics

    async def parse(
        self,
        text: str,
        *,
        context: Sequence[str] = (),
        asr_confidence: float | None = None,
        now: datetime | None = None,
        timezone_name: str | None = None,
    ) -> IntentResult:
        with observe_component(self._metrics, "intent", "parse"):
            return await self._parse(
                text,
                context=context,
                asr_confidence=asr_confidence,
                now=now,
                timezone_name=timezone_name,
            )

    async def _parse(
        self,
        text: str,
        *,
        context: Sequence[str] = (),
        asr_confidence: float | None = None,
        now: datetime | None = None,
        timezone_name: str | None = None,
    ) -> IntentResult:
        cleaned = text.strip()
        if not cleaned:
            raise IntentParseError("empty_text", "请输入或转写一段文本后再解析。")
        semantic_text = _semantic_intent_text(cleaned).strip()
        if not semantic_text:
            raise IntentParseError("empty_text", "请输入或转写一段文本后再解析。")
        timezone = ZoneInfo(timezone_name) if timezone_name is not None else self._timezone
        if now is None:
            current = datetime.now(timezone)
        elif now.tzinfo is None:
            current = now.replace(tzinfo=timezone)
        else:
            current = now.astimezone(timezone)
        normalized = _normalized_intent_text(semantic_text)
        signals = _intent_signals(normalized)
        classified_intent = _classify_intent(semantic_text)
        ambiguous_mutation_temporal_scope = (
            classified_intent in _MUTATING_INTENTS
            and _has_ambiguous_mutation_temporal_scope(
                _temporal_slot_scope(semantic_text, classified_intent)
            )
        )
        deterministic_safety_conflict = (
            _explicitly_aborts(normalized)
            or _explicitly_negates_mutation(normalized)
            or signals.conflicting
            or signals.query_mutation_conflict
            or ambiguous_mutation_temporal_scope
        )
        deterministic_fallback = _enrich_deterministically(
            _fallback_parse(cleaned, current, context), semantic_text, current
        )
        if self._llm is None or deterministic_safety_conflict:
            return _enforce_policy(deterministic_fallback, asr_confidence)

        with observe_component(self._metrics, "llm", "complete"):
            raw = await self._llm.extract(semantic_text, context)
        try:
            parsed = IntentResult.model_validate(_json_object(raw))
        except (json.JSONDecodeError, ValueError, ValidationError) as first_error:
            with observe_component(self._metrics, "llm", "complete"):
                repaired = await self._llm.repair(semantic_text, raw, str(first_error))
            try:
                parsed = IntentResult.model_validate(_json_object(repaired))
            except (json.JSONDecodeError, ValueError, ValidationError) as second_error:
                raise IntentParseError(
                    "invalid_model_output",
                    "意图理解结果格式无效，未执行任何操作。请修改文本后重试。",
                ) from second_error
        if parsed.source_text != cleaned:
            parsed = parsed.model_copy(update={"source_text": cleaned})
        parsed = _enrich_deterministically(parsed, semantic_text, current)
        if parsed.intent in _MUTATING_INTENTS:
            if parsed.intent != deterministic_fallback.intent:
                return _enforce_policy(_unknown_result(cleaned), asr_confidence)
            safe_result = _merge_safe_llm_mutation_metadata(deterministic_fallback, parsed)
            return _enforce_policy(safe_result, asr_confidence)
        return _enforce_policy(parsed, asr_confidence)


def build_intent_parser(
    settings: Settings,
    *,
    metrics: InMemoryMetrics | None = None,
) -> IntentParser:
    if settings.llm_base_url and settings.llm_model:
        client: IntentLlmClient | None = OpenAICompatibleIntentClient(
            base_url=settings.llm_base_url,
            api_key=(
                settings.llm_api_key.get_secret_value()
                if settings.llm_api_key is not None
                else None
            ),
            model=settings.llm_model,
        )
    else:
        client = None
    return IntentParser(client, timezone_name=settings.timezone, metrics=metrics)
