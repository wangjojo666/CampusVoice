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
_MAX_PARSE_TEXT_CHARACTERS = 10_000
_MAX_CONTEXT_ITEM_CHARACTERS = 10_000
_MAX_CONTEXT_TOTAL_CHARACTERS = 50_000


def _context_exceeds_limits(context: Sequence[str]) -> bool:
    total = 0
    for item in context:
        item_length = len(item)
        if item_length > _MAX_CONTEXT_ITEM_CHARACTERS:
            return True
        total += item_length
        if total > _MAX_CONTEXT_TOTAL_CHARACTERS:
            return True
    return False


def _json_object(raw: str) -> dict[str, Any]:
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("top-level LLM output must be an object")
    return parsed


_RELATIVE_DATE_OFFSETS = {"今天": 0, "今晚": 0, "明天": 1, "后天": 2}
_ALLOWED_APPROXIMATE_TEMPORAL_PREFIX_ALTERNATION = r"大约|大概|约|差不多|可能|也许|或许|大致"
_UNSUPPORTED_TEMPORAL_PREFIX_ALTERNATION = (
    r"预计|预估|估计|估摸|约莫|约摸|将近|接近(?:于)?|靠近|近乎|临近|近|"
    r"刚(?:刚)?过|快(?:要到|要|到)?|准时|正好|最晚|最早|最迟|至迟|至早|"
    r"最少|最多|少于|多于|小于|大于|不到|不晚于|不早于|不低于|不高于|"
    r"不少于|不多于|早于|晚于|迟于|不迟于|不超过|超过|低于|高于|至少|"
    r"至多|差(?:一)?点儿?(?:到)?"
)
_TEMPORAL_PREFIX_BRIDGE_PATTERN = (
    r"(?:(?>仍然|依然|将会|需要|最好|应当|应该|必须|务必|计划|打算|准备|将|会|"
    r"要|需|能|在|于|到|至|还|仍|也|就|才|得|等|是|着)|"
    r"[，,。.!！?？；;、/\|｜·•()（）~～—–－−‑])*+"
)
_TEMPORAL_RELATION_SUFFIX_PATTERN = (
    r"(?:之前|之后|(?:往|向|朝)(?:前|后)|之内(?!存|容|核)|之外(?!语|贸)|过后|不到|"
    r"以前(?!端)|以后(?!端)|以内(?!存|容|核)|以外(?!语|贸)|"
    r"上下(?!文|游)|左右(?!手|脑)|附近|出头(?!鸟)|冒(?:个)?头(?!鸟)|"
    r"来钟(?!楼|表|声|摆|点)|刚(?:刚)?过|(?:稍(?:微|稍)?|略(?:微|略)?|微微)过|开外|"
    r"内(?!存|容|核|务)|许(?!愿|可|诺)|"
    r"前(?!后端|端|台|往|景|沿|瞻)|后(?!端|台|门|续|勤|期|备))"
)
_TEMPORAL_RELATION_BRIDGE_PATTERN = (
    r"(?:(?>请|务必|需要|要|然后|接着|随后|再|又|还|才|在|于)|[\W_])*+"
)
_DETACHED_TEMPORAL_RELATION_PATTERN = re.compile(
    rf"^{_TEMPORAL_RELATION_BRIDGE_PATTERN}"
    rf"(?:的)?[\W_]*+"
    rf"{_TEMPORAL_RELATION_SUFFIX_PATTERN}"
)
_DATE_FRAGMENT_PATTERN = re.compile(
    r"(?<![今明后大])(?P<relative>今天|今晚|明天|后天)(?![今明后](?!端|台|门|续|勤))|"
    r"(?<!\d)(?P<year>[+-]?(?>\d+))[年\-/](?P<month>(?>\d+))[月\-/]"
    r"(?P<day>(?>\d+))[日号]?(?!\d)|"
    r"(?<!\d)(?P<short_month>(?>\d+))月(?P<short_day>(?>\d+))[日号](?!\d)"
)
_DATE_CANDIDATE_NUMBER_PATTERN = r"[+.\-]?(?>\d+)(?:\.(?>\d+))?"
_DATE_CANDIDATE_TRAILING_PATTERN = r"[日号]?[+.\-/\d]*"
_DATE_CANDIDATE_PATTERN = re.compile(
    rf"(?<![今明后大])(?:今晚|[今明后大]+天)(?:{_TEMPORAL_RELATION_SUFFIX_PATTERN})?|"
    rf"(?<!\d){_DATE_CANDIDATE_NUMBER_PATTERN}[年\-/]"
    rf"{_DATE_CANDIDATE_NUMBER_PATTERN}[月\-/]"
    rf"{_DATE_CANDIDATE_NUMBER_PATTERN}{_DATE_CANDIDATE_TRAILING_PATTERN}"
    rf"(?!\d)(?:{_TEMPORAL_RELATION_SUFFIX_PATTERN})?|"
    rf"(?<!\d){_DATE_CANDIDATE_NUMBER_PATTERN}月"
    rf"{_DATE_CANDIDATE_NUMBER_PATTERN}[日号]"
    rf"[+.\-/\d]*(?!\d)(?:{_TEMPORAL_RELATION_SUFFIX_PATTERN})?"
)
_UNSUPPORTED_DATE_PREFIX_PATTERN = re.compile(
    rf"(?:{_ALLOWED_APPROXIMATE_TEMPORAL_PREFIX_ALTERNATION}|"
    rf"{_UNSUPPORTED_TEMPORAL_PREFIX_ALTERNATION})"
    rf"{_TEMPORAL_PREFIX_BRIDGE_PATTERN}(?=(?:{_DATE_CANDIDATE_PATTERN.pattern}))"
)
_DATE_MARKER_PATTERN = re.compile(rf"(?<!\d){_DATE_CANDIDATE_NUMBER_PATTERN}[年月日号]")
_NUMERIC_DATE_MARKER_PATTERN = re.compile(
    r"(?<!\d)[+\-]?(?>\d{4,})(?:[.\-/]+(?>\d+)){1,3}[.\-/]*(?!\d)|"
    r"(?<!\d)[+\-]?(?>\d{4,})[.\-/]+(?!\d)"
)
_REPEATED_FULL_DATE_SUFFIX_PATTERN = re.compile(
    r"(?<!\d)(?>\d{4})[年\-/](?>\d+)[月\-/](?>\d+)"
    r"(?:日日(?!程|历|报|记|志|常|语)|号号(?!召|码|线|角)|"
    r"日号(?!召|码|线|角)|号日(?!程|历|报|记|志|常|语))"
)
_ADJACENT_RELATIVE_DATE_PATTERN = re.compile(r"(?:今天|今晚|明天|后天){2,}")


class _DateAnalysis(NamedTuple):
    value: str | None
    fragment_count: int
    invalid: bool


def _date_value(match: re.Match[str], today: date) -> str | None:
    relative = match.group("relative")
    if relative is not None:
        return (today + timedelta(days=_RELATIVE_DATE_OFFSETS[relative])).isoformat()

    year_text = match.group("year")
    if year_text is not None and re.fullmatch(r"20\d{2}", year_text) is None:
        return None
    month_text = match.group("month") or match.group("short_month")
    day_text = match.group("day") or match.group("short_day")
    if month_text is None or day_text is None:
        return None
    if len(month_text) > 2 or len(day_text) > 2:
        return None
    try:
        return date(
            int(year_text) if year_text is not None else today.year,
            int(month_text),
            int(day_text),
        ).isoformat()
    except ValueError:
        return None


def _analyze_date(text: str, today: date) -> _DateAnalysis:
    candidates = list(_DATE_CANDIDATE_PATTERN.finditer(text))
    candidate_spans = tuple(candidate.span() for candidate in candidates)
    marker_spans = (
        *(marker.span() for marker in _DATE_MARKER_PATTERN.finditer(text)),
        *(marker.span() for marker in _NUMERIC_DATE_MARKER_PATTERN.finditer(text)),
        *(marker.span() for marker in _REPEATED_FULL_DATE_SUFFIX_PATTERN.finditer(text)),
    )
    matches = [_DATE_FRAGMENT_PATTERN.fullmatch(candidate.group()) for candidate in candidates]
    values = [_date_value(match, today) if match is not None else None for match in matches]
    marker_index = 0
    markers_are_covered = True
    for marker_start, marker_end in sorted(marker_spans):
        while (
            marker_index < len(candidate_spans) and candidate_spans[marker_index][1] <= marker_start
        ):
            marker_index += 1
        if (
            marker_index == len(candidate_spans)
            or candidate_spans[marker_index][0] > marker_start
            or candidate_spans[marker_index][1] < marker_end
        ):
            markers_are_covered = False
            break
    return _DateAnalysis(
        value=next((value for value in values if value is not None), None),
        fragment_count=len(candidates),
        invalid=any(
            match is None or value is None for match, value in zip(matches, values, strict=True)
        )
        or not markers_are_covered,
    )


def _find_date(text: str, today: date) -> str | None:
    return _analyze_date(text, today).value


def _has_detached_temporal_relation(
    text: str,
    spans: tuple[tuple[int, int], ...],
) -> bool:
    return any(
        _DETACHED_TEMPORAL_RELATION_PATTERN.match(text[end:]) is not None for _, end in spans
    )


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
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
        return int(value) if len(value) <= 9 else None
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
    if value.isdigit() and len(value) > 2:
        return None
    return _chinese_number(value)


_NUMBER_TOKEN_CHARACTERS = r"\d零〇一二两三四五六七八九十百千万"
_REMINDER_AMOUNT_PATTERN = re.compile(
    r"提前\s*(?:"
    r"(?P<half>半)\s*(?:个)?\s*小时|"
    rf"(?P<amount>[{_NUMBER_TOKEN_CHARACTERS}]+)\s*(?:个)?\s*"
    r"(?P<unit>分钟|小时|天)"
    r")"
)
_REMINDER_PATTERN = re.compile(
    _REMINDER_AMOUNT_PATTERN.pattern
    + r"\s*(?:提醒|通知)(?:我)?"
    + r"(?:\s*(?:[，,]\s*)?(?:谢谢|多谢|感谢|谢了))?"
    + r"(?=$|[，,。.!！?？；;:：])"
)
_REMINDER_REQUEST_SIGNAL_PATTERN = re.compile(r"提醒|通知")
_REMINDER_CLAUSE_PATTERN = re.compile(r"[^，,。.!！?？；;:：]+")
_REMINDER_NOMINAL_CLAUSE_PATTERN = re.compile(
    r"(?:(?:研究|学习|讨论|比较|分析|整理|记录|了解|阅读|开发|测试|实现|设计|"
    r"优化|编写|撰写|发送|写|维护).*?(?:提醒|通知)"
    r"(?:公告|功能(?:的实现)?|事项|内容|模板|机制|系统|服务|文本|字样|说法|"
    r"语义|教程|记录|书|栏|函)?|"
    r"(?:提醒|通知)(?:公告|功能|事项|内容|模板|机制|系统|服务|文本|字样|说法|"
    r"语义|教程|记录|书|栏|函)"
    r"(?:整理|研究|开发|维护|设计|实现|优化|分析|说明|模板|记录)?)"
)
_REMINDER_NOMINAL_COMMAND_CONNECTOR_PATTERN = re.compile(
    r"(?:然后|并且|接着|接下来|随后|之后|随即|随之|继而|转而|顺便|最后|同时|"
    r"而后|稍后|过后|末了|待会(?:儿)?|另外|顺手|顺带|顺道|随手|额外|再者|"
    r"此外|顺路|一同|也请|还请|还(?:要|得)|顺势|到时候|届时|外加|另加|再加|"
    r"外带|连带|反而|反倒|加之|回头|转头|等会(?:儿)?|过会(?:儿)?|稍候|稍等|"
    r"片刻后|尔后|其后|待一会儿|晚点|后面|不久后|到时|改天|又|再|且|"
    r"(?<!合)并(?=(?:请|要)?(?:提醒|通知)))"
)
_REMINDER_STRIP_PREFIX_TOKENS = tuple(
    sorted(
        {
            "请帮我",
            "您帮我",
            "你帮我",
            "麻烦你",
            "待一会儿",
            "到时候",
            "接下来",
            "给我",
            "替我",
            "帮我",
            "为我",
            "让我",
            "由我",
            "等会儿",
            "过会儿",
            "片刻后",
            "不久后",
            "劳烦你",
            "劳驾你",
            "烦请你",
            "拜托你",
            "请托你",
            "拜请你",
            "央求你",
            "委派你",
            "嘱咐你",
            "指派你",
            "交给你",
            "待会儿",
            "然后",
            "并且",
            "接着",
            "随后",
            "之后",
            "顺便",
            "随即",
            "随之",
            "继而",
            "转而",
            "最后",
            "同时",
            "而后",
            "过后",
            "请你",
            "届时",
            "稍后",
            "及时",
            "末了",
            "待会",
            "回头",
            "转头",
            "等会",
            "过会",
            "稍候",
            "稍等",
            "尔后",
            "其后",
            "晚点",
            "后面",
            "到时",
            "改天",
            "反倒",
            "加之",
            "求你",
            "托你",
            "劳烦",
            "劳驾",
            "烦请",
            "拜托",
            "麻烦",
            "顺手",
            "千万",
            "请",
            "您",
            "你",
            "又",
            "再",
        },
        key=lambda token: (-len(token), token),
    )
)
_REMINDER_BOUNDARY_STRIP_PREFIX_TOKENS = frozenset(
    {"劳烦", "劳驾", "烦请", "拜托", "麻烦", "顺手", "请"}
)
_REMINDER_PREFIX_BOUNDARY_CHARACTERS = frozenset("，,。.!！?？；;:：")
_REMINDER_MEMORY_CUES = tuple(
    sorted(
        {
            f"{negative}忘{aspect}{tail}"
            for negative in ("不要", "别")
            for aspect in ("", "记", "了")
            for tail in ("", "要", "得", "务必", "一定要")
        }
        | {"记得", "请记得", "记住", "务必", "一定要"},
        key=lambda token: (-len(token), token),
    )
)
_REMINDER_NEGATION_PATTERN = re.compile(
    r"(?:没(?:有)?(?:必要|要求|计划|安排|打算|想过|想着|想要)|"
    r"(?:从来|尚|并)?未(?:曾)?(?:计划|安排|打算|想过|要求)|"
    r"何必|何须|何苦|免得|省得|甭|毋须|毋需|毋用|休要|免予|免于|无(?:需|须)?必要|"
    r"莫(?:要|再)?|避免|回避|防止|省去|省下|略去|"
    r"去掉|去除|取消|关闭|关掉|撤销|撤掉|停止|暂停|拒绝|"
    r"免(?:去|除|掉|了)|省掉|省略|跳过|杜绝|放弃|未要求|并非需要|不需要|不要|"
    r"无需|无须|不用|不必|请勿|勿|禁止|不能|不准|不可|不得|不应|无意|"
    r"不想|不愿|不再|未(?!来)|没|别|不)"
)
_REMINDER_AFFIRMATIVE_MEMORY_PATTERN = re.compile(r"(?:不要|别)忘(?:记|了)?(?:要|得|务必|一定要)?$")

_REMINDER_CLAUSE_BOUNDARY_PATTERN = re.compile(r"[，,。.!！?？；;:：]")
_REMINDER_EXTERNAL_AGENT_PREFIX_PATTERN = re.compile(
    r"(?:(?:劳烦|劳驾|嘱咐|叮嘱|交代|命令|指派|交给|烦请|委派|央求|请托|拜请|"
    r"请|让|由|给|要求|安排|委托|托|麻烦|叫|通知|告诉|拜托|喊|派|责成|责令|督促|催促|指示|授意|授权|指定|转告|"
    r"給|讓|請|委託|麻煩|轉告|吩咐|(?<!寻)找|(?<![需诉追寻要])求)"
    r"(?!你(?:帮我)?$|帮我$|我$)[^，,。.!！?？；;:：]++$|"
    r"^(?:老师|辅导员|同学|朋友|家长|秘书|助理|负责人|管理员|导师|教授|"
    r"班长|室友|同事|舍友|队长|组员|大家|对方|小王|王老师|他|她|它|"
    r"他们|她们)(?:帮我)?$)"
)


def _left_reminder_token_start(
    text: str,
    end: int,
    lower_bound: int,
    tokens: tuple[str, ...],
    *,
    boundary_tokens: frozenset[str] | None = None,
) -> int | None:
    token_end = end
    while token_end > lower_bound and text[token_end - 1].isspace():
        token_end -= 1
    for token in tokens:
        start = token_end - len(token)
        if start < lower_bound or not text.startswith(token, start, token_end):
            continue
        if (
            boundary_tokens is not None
            and token in boundary_tokens
            and start > lower_bound
            and text[start - 1] not in _REMINDER_PREFIX_BOUNDARY_CHARACTERS
        ):
            continue
        return start
    return None


_REMINDER_SELF_REQUEST_TOKENS = tuple(
    sorted(
        set(_REMINDER_STRIP_PREFIX_TOKENS)
        | set(_REMINDER_MEMORY_CUES)
        | {
            "计划",
            "打算",
            "准备",
            "预计",
            "希望",
            "想要",
            "需要",
            "给我",
            "替我",
            "帮我",
            "为我",
            "让我",
            "您帮我",
            "你帮我",
            "您",
            "你",
            "由我",
            "我想",
            "我要",
            "我需要",
            "想",
            "要",
            "我",
        },
        key=lambda token: (-len(token), token),
    )
)


def _reminder_prefix_is_self_request(prefix: str) -> bool:
    cursor = len(prefix)
    while cursor:
        start = _left_reminder_token_start(
            prefix,
            cursor,
            0,
            _REMINDER_SELF_REQUEST_TOKENS,
        )
        if start is None:
            return False
        cursor = start
    return True


def _reminder_previous_clause_is_explicit_command(clause: str) -> bool:
    return (
        _DIRECT_MUTATION_COMMAND_PATTERN.match(clause) is not None
        and _TARGET_SIGNAL_SCAN_PATTERN.search(clause) is not None
    )


_REMINDER_EXTERNAL_AGENT_ROLE_SUFFIX_PATTERN = re.compile(
    r"(?P<actor>.+?)(?:负责|帮忙|代为)(?:着|了|过)?$"
)
_REMINDER_EXTERNAL_AGENT_TENSE_SUFFIX_PATTERN = re.compile(
    r"(?P<actor>.+?)(?:(?:将会|将要|会|将|正|正在)(?:再|也|还)?|"
    r"(?:正|正在)?(?:计划|打算|准备)|已经)(?:再|也|还)?$"
)


def _reminder_prefix_is_external_agent(prefix: str) -> bool:
    for pattern in (
        _REMINDER_EXTERNAL_AGENT_ROLE_SUFFIX_PATTERN,
        _REMINDER_EXTERNAL_AGENT_TENSE_SUFFIX_PATTERN,
    ):
        match = pattern.fullmatch(prefix)
        if match is None:
            continue
        return not _reminder_prefix_is_self_request(match.group("actor"))
    return _REMINDER_EXTERNAL_AGENT_PREFIX_PATTERN.search(prefix) is not None


def _reminder_prefix_is_unsafe(text: str, marker_start: int) -> bool:
    clauses = _REMINDER_CLAUSE_BOUNDARY_PATTERN.split(text[:marker_start])
    current_clause = clauses[-1]
    prior_clauses = [clause for clause in clauses[:-1] if clause]
    previous_clause = prior_clauses[-1] if prior_clauses else ""
    antecedent_clause = prior_clauses[-2] if len(prior_clauses) > 1 else ""
    if _reminder_prefix_is_external_agent(current_clause):
        return True
    if (
        current_clause
        and not _reminder_prefix_is_self_request(current_clause)
        and not _reminder_previous_clause_is_explicit_command(previous_clause)
    ):
        return True
    if (
        _REMINDER_NEGATION_PATTERN.search(current_clause) is not None
        and _REMINDER_AFFIRMATIVE_MEMORY_PATTERN.search(current_clause) is None
    ):
        return True
    if (
        not _reminder_previous_clause_is_explicit_command(previous_clause)
        and not _reminder_previous_clause_is_explicit_command(antecedent_clause)
        and _reminder_prefix_is_external_agent(previous_clause)
    ):
        return True
    previous_negation_scopes = (
        previous_clause,
        previous_clause.rstrip("了过吧呢啊呀哦"),
    )
    return (
        any(
            match.end() == len(scope)
            for scope in previous_negation_scopes
            for match in _REMINDER_NEGATION_PATTERN.finditer(scope)
        )
        and _REMINDER_AFFIRMATIVE_MEMORY_PATTERN.search(previous_clause) is None
    )


def _reminder_minutes(match: re.Match[str]) -> int | None:
    if match.group("half"):
        return 30
    amount = _chinese_number(match.group("amount"))
    if amount is None:
        return None
    multiplier = {"分钟": 1, "小时": 60, "天": 1440}[match.group("unit")]
    minutes = amount * multiplier
    return minutes if 0 <= minutes <= 525_600 else None


def _find_reminder_minutes(text: str) -> int | None:
    match = _REMINDER_PATTERN.search(text)
    return _reminder_minutes(match) if match is not None else None


def _has_unsafe_reminder(text: str) -> bool:
    matches = list(_REMINDER_PATTERN.finditer(text))
    candidates = list(_REMINDER_AMOUNT_PATTERN.finditer(text))
    if len(matches) > 1 or any(_reminder_minutes(candidate) is None for candidate in candidates):
        return True
    match_spans = tuple(match.span() for match in matches)
    for clause_match in _REMINDER_CLAUSE_PATTERN.finditer(text):
        clause = clause_match.group()
        nominal_clause: bool | None = None
        for request in _REMINDER_REQUEST_SIGNAL_PATTERN.finditer(clause):
            request_start = clause_match.start() + request.start()
            request_end = clause_match.start() + request.end()
            if any(start <= request_start and request_end <= end for start, end in match_spans):
                continue
            if nominal_clause is None:
                nominal_clause = (
                    _REMINDER_NOMINAL_CLAUSE_PATTERN.fullmatch(clause) is not None
                    and _REMINDER_NOMINAL_COMMAND_CONNECTOR_PATTERN.search(clause) is None
                )
            if not nominal_clause:
                return True
    for marker in re.finditer(r"提前", text):
        if _reminder_prefix_is_unsafe(text, marker.start()):
            return True
        boundary = _REMINDER_CLAUSE_BOUNDARY_PATTERN.search(text, marker.end())
        clause_end = boundary.start() if boundary is not None else len(text)
        candidate = text[marker.start() : clause_end]
        exact = _REMINDER_PATTERN.fullmatch(candidate)
        if exact is None or _reminder_minutes(exact) is None:
            return True
    return False


def _without_reminder_phrases(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for reminder in _REMINDER_PATTERN.finditer(text):
        start = reminder.start()
        cue_start = _left_reminder_token_start(text, start, cursor, _REMINDER_MEMORY_CUES)
        if cue_start is not None:
            start = cue_start
        while True:
            prefix_start = _left_reminder_token_start(
                text,
                start,
                cursor,
                _REMINDER_STRIP_PREFIX_TOKENS,
                boundary_tokens=_REMINDER_BOUNDARY_STRIP_PREFIX_TOKENS,
            )
            if prefix_start is None:
                break
            start = prefix_start
        parts.append(text[cursor:start])
        cursor = reminder.end()
    if not parts:
        return text
    parts.append(text[cursor:])
    return "".join(parts)


_TIME_PERIOD_ALTERNATION = r"凌晨|早上|上午|中午|下午|晚上|今晚|午夜|半夜"
_TIME_HOUR_ALTERNATION = rf"[{_NUMBER_TOKEN_CHARACTERS}]+"
_TIME_RANGE_CONNECTOR_PATTERN = r"(?:到|至|[-~～—–－])"

_UNSUPPORTED_TIME_PERIOD_PATTERN = re.compile(
    rf"午夜|半夜|夜半|"
    rf"(?:夜晚|今夜|明晚|夜里|夜间|晚间|傍晚|深夜|(?<!今)晚(?!上|间))"
    rf"(?=[{_NUMBER_TOKEN_CHARACTERS}])|"
    rf"(?:(?:{_TIME_PERIOD_ALTERNATION})){{2,}}+"
)
_TIME_FRAGMENT_PATTERN = re.compile(
    rf"(?:(?P<period>{_TIME_PERIOD_ALTERNATION})|(?<![{_NUMBER_TOKEN_CHARACTERS}]))"
    rf"(?P<hour>{_TIME_HOUR_ALTERNATION})"
    rf"(?:(?P<separator>[:点时])(?P<minute>\d*)分?|"
    rf"(?=\s*{_TIME_RANGE_CONNECTOR_PATTERN}\s*"
    rf"(?:(?:{_TIME_PERIOD_ALTERNATION}))?"
    rf"(?:{_TIME_HOUR_ALTERNATION})(?:[:点时])))"
    rf"(?![{_NUMBER_TOKEN_CHARACTERS}])"
)
_BROAD_TIME_NUMBER_CHARACTERS = _NUMBER_TOKEN_CHARACTERS + "廿卅"
_BROAD_TIME_PERIOD_ALTERNATION = _TIME_PERIOD_ALTERNATION + r"|夜里|夜间|晚间|傍晚|深夜"
_BROAD_TIME_PERIOD_CHARACTERS = "凌晨早上中午下午晚夜间里傍深今"
_BROAD_TIME_PERIOD_PATTERN = (
    rf"(?:(?<![{_BROAD_TIME_PERIOD_CHARACTERS}])"
    rf"(?:{_BROAD_TIME_PERIOD_ALTERNATION})"
    rf"[{_BROAD_TIME_PERIOD_CHARACTERS}]*+)?"
)
_BROAD_TIME_HOUR_PATTERN = (
    rf"(?:[+.\-])?[{_BROAD_TIME_NUMBER_CHARACTERS}]+"
    rf"(?:\.[{_BROAD_TIME_NUMBER_CHARACTERS}]+)?"
)
_BROAD_TIME_TAIL_PATTERN = (
    rf"(?:"
    rf":(?:[{_BROAD_TIME_NUMBER_CHARACTERS}]*)(?:\.[{_BROAD_TIME_NUMBER_CHARACTERS}]+)?"
    rf"(?:分)?(?:[{_BROAD_TIME_NUMBER_CHARACTERS}]+(?:秒)?)?"
    rf"|[点时](?:半|[{_BROAD_TIME_NUMBER_CHARACTERS}]+"
    rf"(?:\.[{_BROAD_TIME_NUMBER_CHARACTERS}]+)?(?:分)?"
    rf"(?:[{_BROAD_TIME_NUMBER_CHARACTERS}]+(?:秒)?)?)?"
    rf")"
)
_BROAD_MARKED_TIME_PATTERN = (
    rf"{_BROAD_TIME_PERIOD_PATTERN}"
    rf"{_BROAD_TIME_HOUR_PATTERN}"
    rf"{_BROAD_TIME_TAIL_PATTERN}"
)
_BROAD_TIME_TRAILING_GARBAGE_PATTERN = (
    rf"(?:[+.:][{_BROAD_TIME_NUMBER_CHARACTERS}]+(?:[分秒])?)*"
    rf"(?:秒(?!表|针|杀|懂|回)|钟(?!楼|表|声|摆|点)|"
    rf"整(?!理|合|顿|改|洁|装|体|套|编|齐|形)|"
    rf"多(?!媒|项|人|个|次|种|门|元)|{_TEMPORAL_RELATION_SUFFIX_PATTERN})?"
)
_TIME_CANDIDATE_PATTERN = re.compile(
    rf"(?:(?={_BROAD_TIME_PERIOD_ALTERNATION})|"
    rf"(?<![{_BROAD_TIME_NUMBER_CHARACTERS}]))"
    rf"{_BROAD_MARKED_TIME_PATTERN}"
    rf"{_BROAD_TIME_TRAILING_GARBAGE_PATTERN}"
    rf"(?![{_BROAD_TIME_NUMBER_CHARACTERS}])"
)
_BROAD_TIME_RANGE_CONNECTOR_PATTERN = (
    r"(?:或者|以及|到|至|和|及|与|或|跟|再|、|，|,|/|&|\+|\||[-~～—–－−‑])"
)
_BARE_TO_MARKED_RANGE_CANDIDATE_PATTERN = re.compile(
    rf"(?<![{_BROAD_TIME_NUMBER_CHARACTERS}])"
    rf"(?<![{_BROAD_TIME_NUMBER_CHARACTERS}][:点时])"
    rf"(?P<left>{_BROAD_TIME_PERIOD_PATTERN}{_BROAD_TIME_HOUR_PATTERN})"
    rf"(?P<connector>\s*(?:{_BROAD_TIME_RANGE_CONNECTOR_PATTERN})+\s*)"
    rf"(?P<right>{_BROAD_MARKED_TIME_PATTERN})"
    rf"(?![{_BROAD_TIME_NUMBER_CHARACTERS}])"
)
_CONTEXT_DATE_FRAGMENT_PATTERN = _DATE_FRAGMENT_PATTERN
_DANGLING_TIME_RANGE_PATTERN = re.compile(
    rf"(?<![{_NUMBER_TOKEN_CHARACTERS}])(?:(?:{_TIME_PERIOD_ALTERNATION}))?"
    rf"(?:{_TIME_HOUR_ALTERNATION})(?:[:点时]\d*分?)"
    rf"\s*{_TIME_RANGE_CONNECTOR_PATTERN}\s*"
    rf"(?:(?:{_TIME_PERIOD_ALTERNATION}))?"
    rf"(?:{_TIME_HOUR_ALTERNATION})(?![{_NUMBER_TOKEN_CHARACTERS}])(?!\s*[:点时])"
)
_TIME_LITERAL_PATTERN = (
    rf"(?:(?:{_TIME_PERIOD_ALTERNATION}))?"
    rf"(?:{_TIME_HOUR_ALTERNATION})(?:[:点时]\d*分?)"
)
_ADJACENT_TIME_FRAGMENTS_PATTERN = re.compile(
    rf"(?<![{_NUMBER_TOKEN_CHARACTERS}])"
    rf"{_TIME_LITERAL_PATTERN}{_TIME_LITERAL_PATTERN}"
    rf"(?![{_NUMBER_TOKEN_CHARACTERS}])"
)
_CHAINED_SHORTHAND_TIME_RANGE_PATTERN = re.compile(
    rf"(?<![{_NUMBER_TOKEN_CHARACTERS}])"
    rf"(?:(?:{_TIME_PERIOD_ALTERNATION}))?{_TIME_HOUR_ALTERNATION}"
    rf"(?:\s*{_TIME_RANGE_CONNECTOR_PATTERN}\s*"
    rf"(?:(?:{_TIME_PERIOD_ALTERNATION}))?{_TIME_HOUR_ALTERNATION}){{2,}}"
    rf"(?:[:点时]\d*分?)(?![{_NUMBER_TOKEN_CHARACTERS}])"
)
_LEADING_DANGLING_TIME_CONNECTOR_PATTERN = re.compile(r"(?:(?>再到|到|至|从|[-~～—–－−‑]))++$")
_TRAILING_DANGLING_TIME_CONNECTOR_PATTERN = re.compile(
    r"^(?:(?>然后|并且|接着|随后|之后|顺便|又|再|预计|准时|正好|刚好|"
    r"恰好|正点|按时|最终|终于|最后|马上|立即|立刻|稍后|直接|以及|接下来|才|"
    r"最好|应该|应当|需要|需|务必|请|计划|打算|准备|尽(?:量|快|早|速)|"
    r"赶忙|赶着|火速|快速|快些|早点|赶(?:快|紧)|快点|迅速|立马|必须|要|能|得))*+"
    r"(?:赶)?(?:到|至|[-~～—–－−‑])"
)
_TRAILING_DANGLING_FROM_PATTERN = re.compile(
    r"(?:(?:然后|并且|接着|随后|之后|顺便|又|再))*+从++"
    r"(?!(?:容|事|头|新|严|简|速|中|前|来|而|小|未|此|今|早|"
    r"长|轻|重|宽|属|众|军|政|业|教|医|商|善|实|心|命|优|缓|紧))"
)
_TRAILING_DANGLING_TIME_RANGE_PATTERN = re.compile(
    r"^(?:一?直|持续|延续|开始|起)(?:到|至|[-~～—–－−‑])$"
)
_ALLOWED_TIME_DESTINATION_PREFIX_PATTERN = re.compile(
    r"(?:改到|推迟到|提前到|调整到|安排到|截止到|定到|设置到)$"
)
_BROAD_TIME_PERIOD_SIGNAL_PATTERN = re.compile(_BROAD_TIME_PERIOD_ALTERNATION)
_TEMPORAL_GAP_FILLER_PATTERN = re.compile(
    r"(?:大约|大概|约|差不多|可能|也许|或许|大致|直接|会|在|于|的|左右|[()（）,，。.!！?？；;:：])"
)
_BARE_TIME_CUE_PATTERN = re.compile(r"(?:在|于|从|到|截止|开始|定在|安排在)$")
_UNSUPPORTED_TIME_OFFSET_AMOUNT_PATTERN = rf"(?:半(?:个)?|[{_BROAD_TIME_NUMBER_CHARACTERS}几]+)"
_UNSUPPORTED_TIME_OFFSET_UNIT_PATTERN = r"(?:分(?:钟)?|刻(?:钟)?|小时)"
_UNSUPPORTED_TIME_OFFSET_PATTERN = re.compile(
    rf"(?<![{_BROAD_TIME_NUMBER_CHARACTERS}])"
    rf"(?:(?:{_BROAD_TIME_PERIOD_ALTERNATION}))?(?:"
    rf"{_BROAD_TIME_HOUR_PATTERN}{_BROAD_TIME_TAIL_PATTERN}(?:"
    rf"过(?:{_UNSUPPORTED_TIME_OFFSET_AMOUNT_PATTERN}"
    rf"{_UNSUPPORTED_TIME_OFFSET_UNIT_PATTERN}|一会儿?)|"
    rf"(?:差|又){_UNSUPPORTED_TIME_OFFSET_AMOUNT_PATTERN}"
    rf"{_UNSUPPORTED_TIME_OFFSET_UNIT_PATTERN})|"
    rf"差{_UNSUPPORTED_TIME_OFFSET_AMOUNT_PATTERN}"
    rf"{_UNSUPPORTED_TIME_OFFSET_UNIT_PATTERN}"
    rf"{_BROAD_TIME_HOUR_PATTERN}{_BROAD_TIME_TAIL_PATTERN})"
)
_UNSUPPORTED_SINGLE_END_TIME_PATTERN = re.compile(
    rf"(?:结束(?:时间)?(?:在|于|为)?(?={_BROAD_MARKED_TIME_PATTERN})|"
    rf"(?<![{_NUMBER_TOKEN_CHARACTERS}])"
    rf"{_TIME_LITERAL_PATTERN}(?:时)?(?:结束(?!语|词)|终止|终了|下课|停课|散会|闭会|"
    rf"休会|下班|放学|结课|闭馆|关馆|关门|闭门|闭店|闭展|闭园|闭市|闭业|"
    rf"停业|歇业|停工|打烊|散场|收场|退场|散席|落幕|收官|收尾|谢幕|停演|"
    rf"终场|停赛|完赛|赛毕|完工|竣工|完结|完毕|告终|闭幕(?!式)))"
)
_UNSUPPORTED_EVENT_SINGLE_END_TIME_PATTERN = re.compile(
    rf"(?<![{_NUMBER_TOKEN_CHARACTERS}])"
    rf"{_TIME_LITERAL_PATTERN}(?:时)?(?:截止|截至)"
)
_ALLOWED_APPROXIMATE_TIME_PREFIX_PATTERN = re.compile(
    rf"(?:{_ALLOWED_APPROXIMATE_TEMPORAL_PREFIX_ALTERNATION})"
    rf"{_TEMPORAL_PREFIX_BRIDGE_PATTERN}$"
)
_UNSUPPORTED_TIME_PREFIX_PATTERN = re.compile(
    rf"(?:{_UNSUPPORTED_TEMPORAL_PREFIX_ALTERNATION})"
    rf"{_TEMPORAL_PREFIX_BRIDGE_PATTERN}(?={_BROAD_MARKED_TIME_PATTERN})"
)
_TIME_LEXICAL_CONTINUATION_PATTERN = re.compile(
    r"(?:儿|心意?|子|建议|要求|意见|内容|注意事项|注意|原则|问题|理由|措施|"
    r"方案|说明|想法|结论|重点|要点|观点|经验|看法|认识|心得|启示|主张|"
    r"体会|特征|特点|依据|证据|好处|不足|风险|价值|作用|影响|区别|共识|"
    r"事实|观察|发现|思考|兴起|之间|半会|时代|式|水|透视|定位|估计|共线|"
    r"法|支撑|测量|图(?!书馆)|数据|坐标|曲线)"
)
_HAN_CHARACTER_PATTERN = re.compile(r"[\u3400-\u9fff]")


class _TimeAnalysis(NamedTuple):
    start_time: str | None
    end_time: str | None
    fragment_count: int
    invalid: bool
    range_connector_is_valid: bool


def _is_pure_temporal_clarification(text: str) -> bool:
    remainder = _without_reminder_phrases(text)
    remainder = _REMINDER_AMOUNT_PATTERN.sub("", remainder)
    remainder = _CONTEXT_DATE_FRAGMENT_PATTERN.sub("", remainder)
    remainder = _CHAINED_SHORTHAND_TIME_RANGE_PATTERN.sub("", remainder)
    remainder = _ADJACENT_TIME_FRAGMENTS_PATTERN.sub("", remainder)
    remainder = _TIME_FRAGMENT_PATTERN.sub("", remainder)
    remainder = _UNSUPPORTED_TIME_PERIOD_PATTERN.sub("", remainder)
    return (
        re.fullmatch(r"[从到至和及、，,。.!！?？:：\-~～—–－呢吧啊呀嘛哦吗呐]*", remainder)
        is not None
    )


def _normalize_time_fragment(match: re.Match[str], inherited_period: str) -> str | None:
    hour = _hour_number(match.group("hour"))
    if hour is None:
        return None
    minute_text = match.group("minute")
    if match.group("separator") == ":" and not minute_text:
        return None
    if minute_text is not None and len(minute_text) > 2:
        return None
    minute = int(minute_text or 0)
    period = match.group("period") or inherited_period
    if period:
        if period in {"午夜", "半夜"}:
            return None
        if not (1 <= hour <= 12 or (period == "凌晨" and hour == 0)):
            return None
        if period == "凌晨" and hour == 12:
            hour = 0
        elif period in {"下午", "晚上", "今晚"}:
            if period in {"晚上", "今晚"} and hour == 12:
                return None
            if hour < 12:
                hour += 12
        elif period == "中午" and hour not in {11, 12}:
            return None
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _without_temporal_gap_fillers(text: str) -> str:
    without_dates = _DATE_CANDIDATE_PATTERN.sub("", text)
    return _TEMPORAL_GAP_FILLER_PATTERN.sub("", without_dates)


def _temporal_gap_content_prefix(text: str) -> list[int]:
    coverage_delta = [0] * (len(text) + 1)
    for filler in _TEMPORAL_GAP_FILLER_PATTERN.finditer(text):
        coverage_delta[filler.start()] += 1
        coverage_delta[filler.end()] -= 1
    prefix = [0] * (len(text) + 1)
    active_coverage = 0
    for index, character in enumerate(text):
        active_coverage += coverage_delta[index]
        prefix[index + 1] = prefix[index] + int(active_coverage == 0 and not character.isspace())
    return prefix


def _attached_date_contexts(
    text: str,
    matches: Sequence[re.Match[str]],
    date_spans: tuple[tuple[int, int], ...],
    *,
    allowed_literal_bridges: frozenset[str] | None = None,
) -> dict[int, tuple[int, int]]:
    if not matches or not date_spans:
        return {}
    gap_content = _temporal_gap_content_prefix(text)
    contexts: dict[int, tuple[int, int]] = {}
    date_index = 0
    preceding_date: tuple[int, int] | None = None
    for match in matches:
        while date_index < len(date_spans) and date_spans[date_index][1] <= match.start():
            preceding_date = date_spans[date_index]
            date_index += 1
        if preceding_date is None:
            continue
        gap_is_allowed_literal_bridge = any(
            match.start() - preceding_date[1] == len(bridge)
            and text.startswith(bridge, preceding_date[1], match.start())
            for bridge in allowed_literal_bridges or ()
        )
        if (
            gap_content[match.start()] == gap_content[preceding_date[1]]
            or gap_is_allowed_literal_bridge
        ):
            contexts[match.start()] = preceding_date
    return contexts


def _has_lexical_time_continuation(
    text: str,
    match: re.Match[str],
    *,
    title_prefix_has_content: bool | None = None,
) -> bool:
    lexical_match = _TIME_LEXICAL_CONTINUATION_PATTERN.match(text, match.end())
    bare_one_point_duplicate = (
        match.group("period") is None
        and match.group("hour") in {"一", "1"}
        and match.group("separator") == "点"
        and text.startswith("点", match.end())
    )
    if lexical_match is None and not bare_one_point_duplicate:
        return False
    if match.group("period") is not None or match.group("separator") == ":":
        return False
    if match.group("hour").isdigit() and int(match.group("hour")) > 12:
        return False
    if match.group("hour") in {"一", "1"}:
        return True
    if title_prefix_has_content is None:
        title_prefix = re.split(
            r"[：:，,。.!！?？；;]",
            text[: match.start()],
        )[-1]
        return bool(_without_temporal_gap_fillers(title_prefix))
    return title_prefix_has_content


def _title_prefix_content_flags(text: str) -> list[bool]:
    coverage_delta = [0] * (len(text) + 1)
    for pattern in (_DATE_CANDIDATE_PATTERN, _TEMPORAL_GAP_FILLER_PATTERN):
        for candidate in pattern.finditer(text):
            coverage_delta[candidate.start()] += 1
            coverage_delta[candidate.end()] -= 1
    flags = [False] * (len(text) + 1)
    active_coverage = 0
    has_content = False
    for index, character in enumerate(text):
        active_coverage += coverage_delta[index]
        if character in "：:，,。.!！?？；;":
            has_content = False
        elif active_coverage == 0:
            has_content = True
        flags[index + 1] = has_content
    return flags


def _effective_time_matches(
    text: str,
) -> tuple[
    list[re.Match[str]],
    frozenset[tuple[int, int]],
    frozenset[tuple[int, int]],
]:
    matches = list(_TIME_FRAGMENT_PATTERN.finditer(text))
    title_prefix_content = _title_prefix_content_flags(text)
    lexical_spans = frozenset(
        match.span()
        for match in matches
        if _has_lexical_time_continuation(
            text,
            match,
            title_prefix_has_content=title_prefix_content[match.start()],
        )
    )
    if not lexical_spans or len(lexical_spans) == len(matches):
        return matches, frozenset(), lexical_spans
    return (
        [match for match in matches if match.span() not in lexical_spans],
        lexical_spans,
        lexical_spans,
    )


def _protect_ignored_lexical_times(
    text: str,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    _, ignored_spans, _ = _effective_time_matches(text)
    if not ignored_spans:
        return text, ()
    parts: list[str] = []
    replacements: list[tuple[str, str]] = []
    cursor = 0
    codepoint = 0xF0000
    for start, end in sorted(ignored_spans):
        while chr(codepoint) in text:
            codepoint += 1
        if codepoint > 0xFFFFD:
            raise RuntimeError("lexical time placeholder space exhausted")
        literal = text[start:end]
        placeholder = chr(codepoint) * len(literal)
        parts.extend((text[cursor:start], placeholder))
        replacements.append((placeholder, literal))
        cursor = end
        codepoint += 1
    parts.append(text[cursor:])
    return "".join(parts), tuple(replacements)


_TIME_PERIOD_LEXICAL_CONTINUATIONS = {
    "早上": ("好",),
    "上午": ("场",),
    "中午": ("饭", "餐"),
    "下午": ("茶",),
    "晚上": ("好",),
    "晚间": ("新闻",),
    "凌晨": ("档",),
}


def _is_lexical_time_period_continuation(text: str, period: re.Match[str]) -> bool:
    return any(
        text.startswith(continuation, period.end())
        for continuation in _TIME_PERIOD_LEXICAL_CONTINUATIONS.get(period.group(), ())
    )


def _has_detached_time_period(text: str, matches: list[re.Match[str]]) -> bool:
    if not 1 <= len(matches) <= 2:
        return False
    strict_spans = tuple(match.span() for match in matches)
    periods = [
        period
        for period in _BROAD_TIME_PERIOD_SIGNAL_PATTERN.finditer(text)
        if not any(start <= period.start() and period.end() <= end for start, end in strict_spans)
    ]
    for match in matches:
        if match.group("period") is not None:
            continue
        period = next(
            (candidate for candidate in reversed(periods) if candidate.end() <= match.start()),
            None,
        )
        if period is not None and not _without_temporal_gap_fillers(
            text[period.end() : match.start()]
        ):
            return True
        following_period = next(
            (candidate for candidate in periods if candidate.start() >= match.end()),
            None,
        )
        if (
            following_period is not None
            and not _without_temporal_gap_fillers(text[match.end() : following_period.start()])
            and not _is_lexical_time_period_continuation(text, following_period)
        ):
            return True
    return False


def _analyze_times(text: str) -> _TimeAnalysis:
    text = _without_reminder_phrases(text)
    matches, ignored_lexical_spans, lexical_spans = _effective_time_matches(text)
    candidates = [
        candidate
        for candidate in _TIME_CANDIDATE_PATTERN.finditer(text)
        if candidate.span() not in ignored_lexical_spans
    ]
    first_period = (matches[0].group("period") or "") if matches else ""
    values = [
        _normalize_time_fragment(match, first_period if index > 0 else "")
        for index, match in enumerate(matches)
    ]
    connector_is_valid = True
    if len(matches) == 2:
        connector_is_valid = (
            re.fullmatch(
                rf"\s*{_TIME_RANGE_CONNECTOR_PATTERN}\s*",
                text[matches[0].end() : matches[1].start()],
            )
            is not None
        )
    strict_spans = {match.span() for match in matches}
    candidate_coverage_is_valid = all(candidate.span() in strict_spans for candidate in candidates)
    bare_range_coverage_is_valid = True
    date_spans = tuple(candidate.span() for candidate in _DATE_CANDIDATE_PATTERN.finditer(text))
    attached_date_contexts = _attached_date_contexts(text, matches, date_spans)
    approximate_prefix_is_valid = not any(
        _ALLOWED_APPROXIMATE_TIME_PREFIX_PATTERN.search(
            text,
            max(0, match.start() - 32),
            match.start(),
        )
        is not None
        and match.start() not in attached_date_contexts
        for match in matches
    )
    lexical_continuation_is_valid = not lexical_spans or bool(ignored_lexical_spans)
    date_index = 0
    for candidate in _BARE_TO_MARKED_RANGE_CANDIDATE_PATTERN.finditer(text):
        left_span = candidate.span("left")
        while date_index < len(date_spans) and date_spans[date_index][1] <= left_span[0]:
            date_index += 1
        if date_index < len(date_spans) and date_spans[date_index][0] < left_span[1]:
            continue
        connector_is_exact = (
            re.fullmatch(
                rf"\s*{_TIME_RANGE_CONNECTOR_PATTERN}\s*",
                candidate.group("connector"),
            )
            is not None
        )
        right_span = candidate.span("right")
        if (
            not connector_is_exact
            or left_span not in strict_spans
            or right_span not in strict_spans
        ):
            bare_range_coverage_is_valid = False
            break
    period_order_is_valid = not (
        len(matches) == 2
        and matches[0].group("period") is None
        and matches[1].group("period") is not None
    )
    bare_one_is_range = len(matches) == 2 and connector_is_valid
    ambiguous_bare_one_point_is_valid = not any(
        match.group("period") is None
        and match.group("hour") in {"一", "1"}
        and match.group("separator") in {"点", "时"}
        and not match.group("minute")
        and not bare_one_is_range
        and match.start() not in attached_date_contexts
        and _BARE_TIME_CUE_PATTERN.search(text[: match.start()]) is None
        for match in matches
    )
    detached_period_is_valid = not _has_detached_time_period(text, matches)
    dangling_connector_is_valid = True
    if matches:
        prefix = _without_temporal_gap_fillers(text[: matches[0].start()]).rstrip()
        suffix = _without_temporal_gap_fillers(text[matches[-1].end() :]).lstrip()
        leading_connector = _LEADING_DANGLING_TIME_CONNECTOR_PATTERN.search(prefix)
        if (
            leading_connector is not None
            and _ALLOWED_TIME_DESTINATION_PREFIX_PATTERN.search(prefix) is None
            and not (leading_connector.group() == "从" and len(matches) == 2 and connector_is_valid)
        ):
            dangling_connector_is_valid = False
        if (
            _TRAILING_DANGLING_TIME_CONNECTOR_PATTERN.search(suffix) is not None
            or _TRAILING_DANGLING_TIME_RANGE_PATTERN.search(suffix) is not None
            or _TRAILING_DANGLING_FROM_PATTERN.match(suffix) is not None
        ):
            dangling_connector_is_valid = False
    return _TimeAnalysis(
        start_time=values[0] if values else None,
        end_time=values[1] if len(values) > 1 else None,
        fragment_count=len(matches),
        invalid=(
            any(value is None for value in values)
            or not candidate_coverage_is_valid
            or not bare_range_coverage_is_valid
            or not period_order_is_valid
            or not approximate_prefix_is_valid
            or not lexical_continuation_is_valid
            or not ambiguous_bare_one_point_is_valid
            or not detached_period_is_valid
            or not dangling_connector_is_valid
        ),
        range_connector_is_valid=connector_is_valid,
    )


def _find_times(text: str) -> tuple[str | None, str | None]:
    analysis = _analyze_times(text)
    return analysis.start_time, analysis.end_time


_QUOTE_OPEN_TO_CLOSE = {
    "《": "》",
    "“": "”",
    "「": "」",
    "『": "』",
    "‘": "’",
    "«": "»",
    "‹": "›",
    "〝": "〞",
    "〈": "〉",
    "【": "】",
    "〔": "〕",
    "〖": "〗",
    "〘": "〙",
    "〚": "〛",
    "⟪": "⟫",
    "⦅": "⦆",
    "❝": "❞",
    "„": "“",
    "`": "`",
    "＂": "＂",
    '"': '"',
    "'": "'",
}
_QUOTE_CLOSE_CHARACTERS = frozenset(_QUOTE_OPEN_TO_CLOSE.values())
_QUOTE_CHARACTERS = frozenset(_QUOTE_OPEN_TO_CLOSE) | _QUOTE_CLOSE_CHARACTERS
_RENAME_MODIFIER_PATTERN = re.compile(r"(?:改名为|重命名为|标题改为)[：:，,]*")


class _QuotedSpan(NamedTuple):
    start: int
    end: int


class _RenameClauseSpan(NamedTuple):
    modifier_start: int
    payload_start: int
    payload_end: int
    quoted: bool


def _is_ascii_word_apostrophe(text: str, index: int) -> bool:
    return (
        text[index] == "'"
        and index > 0
        and index + 1 < len(text)
        and text[index - 1].isascii()
        and text[index - 1].isalnum()
        and text[index + 1].isascii()
        and text[index + 1].isalnum()
    )


def _paired_quote_spans(
    text: str,
    *,
    pair_ends: dict[int, int] | None = None,
) -> tuple[_QuotedSpan, ...] | None:
    if _QUOTE_CHARACTERS.isdisjoint(text):
        return ()
    spans: list[_QuotedSpan] = []
    stack: list[tuple[str, int]] = []
    for index, character in enumerate(text):
        if _is_ascii_word_apostrophe(text, index):
            continue
        if stack and character == stack[-1][0]:
            _, start = stack.pop()
            if pair_ends is not None:
                pair_ends[start] = index + 1
            if not stack:
                spans.append(_QuotedSpan(start, index + 1))
            continue
        close_character = _QUOTE_OPEN_TO_CLOSE.get(character)
        if close_character is not None:
            stack.append((close_character, index))
            continue
        if character in _QUOTE_CLOSE_CHARACTERS:
            return None
    return None if stack else tuple(spans)


def _mask_span(text: str, start: int, end: int) -> str:
    return text[:start] + (" " * (end - start)) + text[end:]


def _mask_paired_quotes(text: str) -> str | None:
    spans = _paired_quote_spans(text)
    if spans is None:
        return None
    parts: list[str] = []
    cursor = 0
    for span in spans:
        parts.append(text[cursor : span.start])
        parts.append(" " * (span.end - span.start))
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)


def _quote_span_is_abort_separator(content: str) -> bool:
    return all(
        character.isspace()
        or _is_abort_separator(character)
        or unicodedata.category(character)[0] in {"C", "M"}
        for character in content
    )


def _mask_paired_quotes_for_abort(text: str) -> str | None:
    spans = _paired_quote_spans(text)
    if spans is None:
        return None
    parts: list[str] = []
    cursor = 0
    for span in spans:
        parts.append(text[cursor : span.start])
        content = text[span.start + 1 : span.end - 1]
        replacement = "/" if _quote_span_is_abort_separator(content) else " "
        parts.append(replacement * (span.end - span.start))
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)


def _rename_clause_span(text: str) -> _RenameClauseSpan | None:
    quote_spans = _paired_quote_spans(text)
    if quote_spans is None:
        return None
    masked = _mask_paired_quotes(text)
    if masked is None:
        return None
    modifier = _RENAME_MODIFIER_PATTERN.search(masked)
    if modifier is None:
        return None
    payload_outer_start = modifier.end()
    while payload_outer_start < len(text) and text[payload_outer_start].isspace():
        payload_outer_start += 1
    quoted_span = next(
        (span for span in quote_spans if span.start == payload_outer_start),
        None,
    )
    if quoted_span is not None:
        return _RenameClauseSpan(
            modifier_start=modifier.start(),
            payload_start=quoted_span.start + 1,
            payload_end=quoted_span.end - 1,
            quoted=True,
        )
    boundary = re.search(r"[，,。.!！?？；;]", text[payload_outer_start:])
    payload_end = payload_outer_start + boundary.start() if boundary is not None else len(text)
    return _RenameClauseSpan(
        modifier_start=modifier.start(),
        payload_start=payload_outer_start,
        payload_end=payload_end,
        quoted=False,
    )


def _masked_update_scan_scope(text: str) -> str:
    masked = _mask_paired_quotes(text)
    if masked is None:
        return text
    rename = _rename_clause_span(text)
    if rename is not None and not rename.quoted:
        masked = _mask_span(masked, rename.payload_start, rename.payload_end)
    return masked


def _strip_outer_quoted_literal(text: str) -> str:
    pair_ends: dict[int, int] = {}
    spans = _paired_quote_spans(text, pair_ends=pair_ends)
    if spans is None or len(spans) != 1:
        return text
    span = spans[0]
    if span.start != 0 or span.end != len(text):
        return text

    start = 0
    end = len(text)
    while pair_ends.get(start) == end:
        start += 1
        end -= 1
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
    return text[start:end]


_UPDATE_FIELD_NAME_PATTERN = (
    r"(?:标题|优先级|状态|截止时间|截止日期|开始时间|结束时间|时间|日期|"
    r"地点|位置|课程|描述|备注)"
)
_UPDATE_FIELD_ASSIGNMENT_PATTERN = (
    r"(?:改名为|重命名为|指定为|更改为|赋值为|设置为|设置成|调整为|调整到|"
    r"更新为|变更为|迁移到|转移到|移动到|移动至|推迟到|提前到|设置到|改为|改成|"
    r"标记为|设为|设成|定为|变为|置为|迁到|迁至|搬到|换到|调到|转到|"
    r"移到|挪到|移至|换为|换成|变成|改作|换作|改到|"
    r"(?:改|换|变|转|设|定|置)(?:为|成|作|做|到)|成为|当作|作为)"
)
_UPDATE_FIELD_MODIFIER_PATTERN = re.compile(
    rf"(?:的)?(?:{_UPDATE_FIELD_NAME_PATTERN})?{_UPDATE_FIELD_ASSIGNMENT_PATTERN}"
)
_EXPLICIT_UPDATE_FIELD_MODIFIER_PATTERN = re.compile(
    rf"(?:的)?{_UPDATE_FIELD_NAME_PATTERN}{_UPDATE_FIELD_ASSIGNMENT_PATTERN}"
)
_TEMPORAL_META_TITLE_PATTERN = re.compile(
    r"^(?:研究|学习|讨论|比较|分析|整理|记录|了解|阅读).+"
    r"(?:的)?(?:含义|概念|(?:这个)?表达|语义|字样|说法|用法|词句|词语|短语|"
    r"意思|区别|差异|原因|原理|方法|教程|示例|例子)$"
)
_REMINDER_META_TITLE_PREFIXES = (
    "开发",
    "研究",
    "测试",
    "实现",
    "设计",
    "优化",
    "讨论",
    "整理",
    "记录",
    "学习",
    "编写",
    "撰写",
)
_REMINDER_META_TITLE_SIGNALS = ("提前", "提醒", "通知")
_REMINDER_META_TITLE_SUFFIXES = (
    "功能",
    "实现",
    "逻辑",
    "机制",
    "系统",
    "服务",
    "模块",
    "代码",
    "教程",
    "文档",
    "用法",
    "语义",
    "模板",
)


def _is_reminder_meta_title(title: str) -> bool:
    return (
        title.startswith(_REMINDER_META_TITLE_PREFIXES)
        and title.endswith(_REMINDER_META_TITLE_SUFFIXES)
        and any(signal in title for signal in _REMINDER_META_TITLE_SIGNALS)
        and _REMINDER_NOMINAL_COMMAND_CONNECTOR_PATTERN.search(title) is None
    )


def _time_range_cleanup_start(text: str, time_start: int) -> int:
    prefix = text[:time_start]
    from_index = prefix.rfind("从")
    if from_index >= 0 and not _without_temporal_gap_fillers(prefix[from_index + 1 :]):
        return from_index
    return time_start


def _without_temporal_phrases_outside_quotes(text: str) -> str:
    quote_spans = _paired_quote_spans(text)
    if quote_spans is None:
        return text
    cleaned_parts: list[str] = []
    cursor = 0
    for quote_span in (*quote_spans, _QuotedSpan(len(text), len(text))):
        part = text[cursor : quote_span.start]
        part, protected_lexical_times = _protect_ignored_lexical_times(part)
        time_matches = list(_TIME_FRAGMENT_PATTERN.finditer(part))
        range_spans = [
            (
                _time_range_cleanup_start(part, left.start()),
                right.end(),
            )
            for left, right in zip(time_matches, time_matches[1:], strict=False)
            if re.fullmatch(
                r"(?:到|至|[-~～—–－])",
                part[left.end() : right.start()],
            )
            is not None
        ]
        date_matches = list(_DATE_CANDIDATE_PATTERN.finditer(part))
        attached_date_time_spans: list[tuple[int, int]] = []
        date_index = 0
        preceding_date: re.Match[str] | None = None
        for time_match in time_matches:
            while (
                date_index < len(date_matches)
                and date_matches[date_index].end() <= time_match.start()
            ):
                preceding_date = date_matches[date_index]
                date_index += 1
            if preceding_date is not None and not _without_temporal_gap_fillers(
                part[preceding_date.end() : time_match.start()]
            ):
                attached_date_time_spans.append((preceding_date.start(), time_match.end()))
        merged_spans: list[tuple[int, int]] = []
        for start, end in sorted((*range_spans, *attached_date_time_spans)):
            if merged_spans and start <= merged_spans[-1][1]:
                merged_spans[-1] = (merged_spans[-1][0], max(end, merged_spans[-1][1]))
            else:
                merged_spans.append((start, end))
        cleaned = part
        for start, end in reversed(merged_spans):
            cleaned = cleaned[:start] + cleaned[end:]
        cleaned = _without_reminder_phrases(cleaned)
        cleaned = _CONTEXT_DATE_FRAGMENT_PATTERN.sub("", cleaned)
        cleaned = _TIME_FRAGMENT_PATTERN.sub("", cleaned)
        cleaned = re.sub(
            r"(^|[，,。.!！?？；;])(?:截止|开始)(?=$|[，,。.!！?？；;])",
            r"\1",
            cleaned,
        )
        for placeholder, literal in protected_lexical_times:
            cleaned = cleaned.replace(placeholder, literal)
        cleaned_parts.append(cleaned)
        if quote_span.end > quote_span.start:
            cleaned_parts.append(text[quote_span.start : quote_span.end])
        cursor = quote_span.end
    return "".join(cleaned_parts)


def _split_temporal_meta_title(text: str) -> tuple[str, str] | None:
    match = re.match(r"(?P<title>[^，,。.!！?？；;]+)(?P<trailing>.*)$", text)
    if match is None:
        return None
    title = match.group("title")
    has_temporal_literal = (
        _REMINDER_PATTERN.search(title) is not None
        or _REMINDER_AMOUNT_PATTERN.search(title) is not None
        or _REMINDER_REQUEST_SIGNAL_PATTERN.search(title) is not None
        or _CONTEXT_DATE_FRAGMENT_PATTERN.search(title) is not None
        or _TIME_FRAGMENT_PATTERN.search(title) is not None
        or _UNSUPPORTED_TIME_PERIOD_PATTERN.search(title) is not None
    )
    if not has_temporal_literal or (
        _TEMPORAL_META_TITLE_PATTERN.search(title) is None and not _is_reminder_meta_title(title)
    ):
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
        field_scope = _masked_update_scan_scope(normalized)
        modifier = _UPDATE_FIELD_MODIFIER_PATTERN.search(field_scope)
        return field_scope[modifier.start() :] if modifier is not None else ""
    if intent in {IntentName.CREATE_TASK, IntentName.CREATE_EVENT}:
        delimiter_scope = _mask_paired_quotes(normalized)
        title_delimiter = _title_delimiter_position(
            delimiter_scope if delimiter_scope is not None else normalized
        )
        if title_delimiter is not None:
            title = normalized[title_delimiter + 1 :]
            meta_title = _split_temporal_meta_title(title)
            if meta_title is not None:
                normalized = meta_title[1]
        masked = _mask_paired_quotes(normalized)
        return masked if masked is not None else normalized
    return normalized


def _point_prefix_is_command_only(prefix: str) -> bool:
    prefix = prefix.strip("，,。.!！?？；;:：")
    if not prefix:
        return True
    return (
        re.fullmatch(
            rf"{_COMMAND_PREFIX_PATTERN}(?:安排)?(?:(?:{_MUTATION_SIGNAL_PATTERN})"
            rf"(?:一个|一项|一条|一场)?(?:{_TARGET_SIGNAL_PATTERN}))?"
            rf"(?:从|在|于|到)?",
            prefix,
        )
        is not None
    )


def _point_prefix_supports_scheduled_time(prefix: str) -> bool:
    prefix = prefix.strip("，,。.!！?？；;:：")
    return _point_prefix_is_command_only(prefix) or (
        _TARGET_SIGNAL_SCAN_PATTERN.search(prefix) is not None
    )


def _has_explicit_point_time_cue(prefix: str) -> bool:
    cue = _BARE_TIME_CUE_PATTERN.search(prefix)
    return cue is not None and _point_prefix_supports_scheduled_time(prefix[: cue.start()])


def _has_exact_time_range_connector(text: str, left_end: int, right_start: int) -> bool:
    return (
        re.fullmatch(
            rf"\s*{_TIME_RANGE_CONNECTOR_PATTERN}\s*",
            text[left_end:right_start],
        )
        is not None
    )


def _has_ambiguous_action_point_literal(text: str) -> bool:
    matches = list(_TIME_FRAGMENT_PATTERN.finditer(text))
    date_spans = tuple(candidate.span() for candidate in _DATE_CANDIDATE_PATTERN.finditer(text))
    attached_dates = _attached_date_contexts(
        text,
        matches,
        date_spans,
        allowed_literal_bridges=frozenset({"从"}),
    )
    boundaries = iter(_REMINDER_CLAUSE_BOUNDARY_PATTERN.finditer(text))
    boundary = next(boundaries, None)
    clause_start = 0
    supported_time_seen = False
    for index, match in enumerate(matches):
        while boundary is not None and boundary.end() <= match.start():
            clause_start = boundary.end()
            supported_time_seen = False
            boundary = next(boundaries, None)
        hour = _hour_number(match.group("hour"))
        separator = match.group("separator")
        if hour is None:
            continue
        if separator != "点":
            if separator in {":", "时"}:
                supported_time_seen = True
            continue
        if hour > 12 or _HAN_CHARACTER_PATTERN.match(text, match.end()) is None:
            supported_time_seen = True
            continue
        previous = matches[index - 1] if index else None
        connected_previous = (
            previous is not None
            and previous.start() >= clause_start
            and _has_exact_time_range_connector(text, previous.end(), match.start())
        )
        if connected_previous and supported_time_seen:
            continue
        candidate_start = (
            previous.start() if connected_previous and previous is not None else match.start()
        )
        prefix = text[clause_start:candidate_start]
        if text.startswith(("截止", "开始"), match.end()):
            supported_time_seen = True
            continue
        if supported_time_seen or _has_explicit_point_time_cue(prefix):
            supported_time_seen = True
            continue
        preceding_date = attached_dates.get(candidate_start)
        if preceding_date is not None and preceding_date[0] >= clause_start:
            gap = text[preceding_date[1] : candidate_start]
            if "的" in gap or not _point_prefix_supports_scheduled_time(
                text[clause_start : preceding_date[0]]
            ):
                return True
            supported_time_seen = True
            continue
        if not _point_prefix_supports_scheduled_time(prefix):
            return True
        supported_time_seen = True
    return False


def _has_unsafe_mutation_temporal_scope(text: str, today: date) -> bool:
    if _has_ambiguous_action_point_literal(text):
        return True
    date_analysis = _analyze_date(text, today)
    time_analysis = _analyze_times(text)
    return (
        date_analysis.fragment_count > 1
        or date_analysis.invalid
        or _ADJACENT_RELATIVE_DATE_PATTERN.search(text) is not None
        or _has_unsafe_reminder(text)
        or _UNSUPPORTED_DATE_PREFIX_PATTERN.search(text) is not None
        or _UNSUPPORTED_TIME_PERIOD_PATTERN.search(text) is not None
        or _UNSUPPORTED_TIME_OFFSET_PATTERN.search(text) is not None
        or _UNSUPPORTED_SINGLE_END_TIME_PATTERN.search(text) is not None
        or _UNSUPPORTED_TIME_PREFIX_PATTERN.search(text) is not None
        or _has_detached_temporal_relation(
            text,
            (
                *(candidate.span() for candidate in _DATE_CANDIDATE_PATTERN.finditer(text)),
                *(match.span() for match in _effective_time_matches(text)[0]),
            ),
        )
        or _CHAINED_SHORTHAND_TIME_RANGE_PATTERN.search(text) is not None
        or _ADJACENT_TIME_FRAGMENTS_PATTERN.search(text) is not None
        or time_analysis.invalid
        or time_analysis.fragment_count > 2
        or (time_analysis.fragment_count == 2 and not time_analysis.range_connector_is_valid)
        or _DANGLING_TIME_RANGE_PATTERN.search(text) is not None
        or (
            time_analysis.start_time is not None
            and time_analysis.end_time is not None
            and time_analysis.end_time <= time_analysis.start_time
        )
    )


def _extract_title(text: str, intent: IntentName) -> str | None:
    candidates: list[str] = []
    object_first = re.match(
        r"^(?:(?:请|帮我|麻烦|替我|给我|我想|我要|我需要)\s*)*(?:把|将)\s*",
        text,
    )
    if object_first is not None:
        after = text[object_first.end() :]
        candidates.append(re.split(r"(?:加到|加入|添加到|放到|设为|创建成)", after, maxsplit=1)[0])
    candidates.append(
        re.sub(
            r"^(?:(?:请|帮我|麻烦|替我|给我|我想|我要|我需要|先|首先|马上|"
            r"立刻|立即|稍后|再次|分别|别忘了|不要忘记|记得)\s*)*"
            r"(?:创建|新建|添加|记一个|安排)\s*"
            r"(?:(?:(?:一个)|(?:1|一)(?:个|项|条|门|份|场|次|节))\s*"
            r"(?:待办|任务|作业|日历事件|日历|日程|事件|组会|会议|考试|答辩|讲座|课程)"
            r"|(?:待办|任务|作业|日历事件|日历|日程|事件|组会|会议|考试|答辩|讲座|课程)"
            r"(?=[：:，,\s])|(?:待办|任务|作业|日历事件|日历|日程|事件))?"
            r"[：:，,\s]*",
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
            cleaned = re.sub(r"^(?:待办|任务)[：:，,\s]*", "", cleaned).strip()
        if cleaned and cleaned not in {"待办", "任务", "日程", "事件", "日历"}:
            return cleaned
    return None


_UPDATE_TRAILING_SINGLE_CHARACTERS = frozenset("，,。.!！?？；;：:")
_UPDATE_TRAILING_TOKENS = (
    "然后",
    "并且",
    "接着",
    "随后",
    "之后",
    "顺便",
    "再",
    "把",
    "将",
)


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


_MUTATION_RESULTATIVE_SUFFIX_PATTERN = r"(?:好|完(?:成|毕)?|干净|掉|妥(?:当)?|彻底)"
_MUTATION_TERMINAL_PARTICLE_PATTERN = (
    r"(?:吧|啦|呀|啊|呢|哦|嘛|哈|呗|咯|喽|哎|诶|哟|唷|嘞|咧|哩|欸)"
)
_MUTATION_TERMINAL_PARTICLE_RUN_PATTERN = rf"{_MUTATION_TERMINAL_PARTICLE_PATTERN}*+"
_OBJECT_FIRST_TRAILING_MUTATION_PATTERN = re.compile(
    r"(?:给|也|都)?(?:删除|删掉|移除|取消|撤销|撤掉|修改|更新|调整|完成)"
    rf"(?:一下|一遍)?(?:{_MUTATION_RESULTATIVE_SUFFIX_PATTERN})?(?:了)?"
    rf"{_MUTATION_TERMINAL_PARTICLE_RUN_PATTERN}[，,。.!！?？…~～]*$"
)


def _extract_target_title(text: str, intent: IntentName) -> str | None:
    semantic_text, compact, compact_semantic_offsets = _semantic_compact_text_map(text)
    delimiter_scope = _mask_paired_quotes(compact)
    explicit_title = (
        _title_delimiter_position(delimiter_scope if delimiter_scope is not None else compact)
        is not None
    )
    candidate = semantic_text.strip()
    compact_scope = _command_scope(compact)
    object_first = _SUPPORTED_OBJECT_FIRST_COMMAND_START_PATTERN.match(compact_scope)
    if object_first is not None:
        operation_start = _supported_object_first_mutation_start(compact_scope)
        operation_boundary = operation_start if operation_start is not None else len(compact)
        semantic_start = compact_semantic_offsets[object_first.end()]
        semantic_end = compact_semantic_offsets[operation_boundary]
        candidate = semantic_text[semantic_start:semantic_end]
    if object_first is None:
        candidate = re.sub(
            r"^(?:(?:请|帮我|麻烦|替我|给我|我想|我要|我需要|先|首先|马上|"
            r"立刻|立即|稍后|再次|分别)\s*)*"
            r"(?:删除|删掉|移除|取消|撤销|撤掉|修改|更新|调整|完成)\s*",
            "",
            candidate,
        )
    candidate = re.sub(
        r"^(?:这个|那个|上次的|之前的)?"
        r"(?:待办|任务|作业|日历事件|日历|日程|事件|组会|会议|考试|答辩|讲座|课程)[：:，,\s]*",
        "",
        candidate,
    )
    if object_first is not None:
        trailing_mutation = _OBJECT_FIRST_TRAILING_MUTATION_PATTERN.search(candidate)
        if trailing_mutation is not None:
            candidate = candidate[: trailing_mutation.start()]
    if intent in {IntentName.UPDATE_TASK, IntentName.UPDATE_EVENT}:
        rename = _rename_clause_span(candidate)
        field_scope = _masked_update_scan_scope(candidate)
        modifier = _UPDATE_FIELD_MODIFIER_PATTERN.search(field_scope)
        target_boundaries: list[int] = []
        if rename is not None:
            target_boundaries.append(_update_target_boundary(candidate, rename.modifier_start))
        if modifier is not None:
            target_boundaries.append(_update_target_boundary(field_scope, modifier.start()))
        cut_at = min(target_boundaries, default=len(candidate))
        candidate = candidate[:cut_at]
        candidate = _strip_update_trailing_connectors(candidate)
    if not explicit_title:
        candidate = _without_temporal_phrases_outside_quotes(candidate)
        candidate = re.sub(r"(?:这个)?(?:待办|任务|日历事件|日程|事件)$", "", candidate)
    cleaned = candidate.strip()
    cleaned = cleaned.strip("，,。.!！?？：:").strip()
    cleaned = _strip_outer_quoted_literal(cleaned)
    return cleaned or None


def _new_title(text: str) -> str | None:
    rename = _rename_clause_span(text)
    if rename is None:
        return None
    title = text[rename.payload_start : rename.payload_end].strip()
    return title or None


_EXPLICIT_TARGET_VALUE_HINT_PATTERN = re.compile(
    r"(?:论文|报告|讲稿|文档|合同|发票|项目|计划|方案|申请|总结|复盘|周报|"
    r"月报|日报|工单|需求|草稿|初稿|终稿)"
    r"(?:[A-Za-z0-9甲乙丙丁一二三四五六七八九十]*)$"
)
_HIDDEN_UPDATE_OBJECT_HINT_PATTERN = r"(?:论文|报告|讲稿|文档|合同|项目|方案|任务|待办|日程|事件)"
_HIDDEN_UPDATE_OBJECT_MARKER_PATTERN = re.compile(
    r"(?P<object_first>把|将)|"
    r"(?P<relational>交由|委托|托付|使|叫|请|由|让|令|帮|为)"
)


def _hidden_update_marker_is_lexical_continuation(
    scope: str,
    marker: re.Match[str],
    *,
    rightmost_target_start: int,
) -> bool:
    token = marker.group()
    marker_start = marker.start()
    marker_end = marker.end()
    if rightmost_target_start >= marker_end:
        return False
    if token == "把":
        return scope.endswith(
            ("门", "刀", "车", "话", "火", "拖", "伞"), 0, marker_start
        ) or scope.startswith(("手", "柄", "式"), marker_end)
    if token == "将":
        return scope.startswith(("来", "军", "领", "士", "就", "近", "要", "才", "会"), marker_end)
    if token == "使":
        return scope.startswith(("命", "用", "馆", "者", "劲", "得", "然", "役", "节"), marker_end)
    if token == "叫":
        return scope.startswith(
            ("法", "号", "声", "门", "板", "好", "座", "化", "醒", "停"), marker_end
        )
    if token == "请":
        return scope.endswith(("申", "邀", "聘", "宴"), 0, marker_start) or scope.startswith(
            ("求", "假", "示", "愿", "帖", "柬", "安", "教", "问"), marker_end
        )
    if token == "由":
        return scope.endswith(("理", "自", "缘", "原", "事"), 0, marker_start) or scope.startswith(
            ("于", "来", "衷", "头", "此", "外", "内", "表"), marker_end
        )
    if token in {"委托", "托付"}:
        return scope.startswith(
            (
                "书",
                "协议",
                "合同",
                "事项",
                "关系",
                "业务",
                "手续",
                "证明",
                "费用",
                "记录",
                "安排",
                "代理",
                "服务",
            ),
            marker_end,
        )
    if token == "让":
        return scope.endswith(
            ("转", "礼", "谦", "避", "忍", "退", "出", "割", "承"), 0, marker_start
        ) or scope.startswith(("步", "渡"), marker_end)
    if token == "令":
        return scope.endswith(
            ("命", "口", "指", "法", "司", "时", "节", "县", "政", "军", "号"),
            0,
            marker_start,
        )
    if token == "帮":
        return scope.endswith(("鞋", "船", "马", "丐", "青"), 0, marker_start) or scope.startswith(
            ("扶", "助", "忙", "派", "手", "会"), marker_end
        )
    return token == "为" and (
        scope.endswith(("作", "因", "称", "认", "视", "成", "行"), 0, marker_start)
        or scope.startswith(
            (
                "了",
                "期",
                "题",
                "主",
                "例",
                "目的",
                "核心",
                "基础",
                "依据",
                "标准",
                "准",
                "由",
                "何",
                "人民服务",
            ),
            marker_end,
        )
    )


_NATURAL_SINGLE_TARGET_CONJUNCTION_PATTERN = re.compile(
    r"(?:需求分析与设计|(?:研发|开发)(?:和|与)测试|"
    r"前端(?:和|与)后端(?:联调|开发|协作|评审)?|世界和平|考试及格线|"
    r"理论与实践|设计与实现|输入与输出|线上与线下|工作与生活|教学与科研|"
    r"学习和复习)"
)
_TARGET_DESCRIPTOR_PREFIX_PATTERN = re.compile(r"^(?:另一个|另|名为|叫做|名称为)")


def _looks_like_distinct_target_pair(left: str, right: str) -> bool:
    if (
        _TARGET_SIGNAL_SCAN_PATTERN.search(left) is not None
        or _TARGET_SIGNAL_SCAN_PATTERN.search(right) is not None
    ):
        return True
    if (
        re.fullmatch(r"[A-Za-z0-9_-]{1,12}", left) is not None
        and re.fullmatch(r"[A-Za-z0-9_-]{1,12}", right) is not None
    ):
        return True
    if (
        _EXPLICIT_TARGET_VALUE_HINT_PATTERN.search(left) is not None
        and _EXPLICIT_TARGET_VALUE_HINT_PATTERN.search(right) is not None
    ):
        return True
    common_prefix_length = 0
    for left_character, right_character in zip(left, right, strict=False):
        if left_character != right_character:
            break
        common_prefix_length += 1
    return (
        common_prefix_length > 0
        and max(
            len(left) - common_prefix_length,
            len(right) - common_prefix_length,
        )
        <= 4
    )


def _conditional_target_separator_is_lexical_continuation(
    separator: str,
    right: str,
) -> bool:
    if _TARGET_SIGNAL_SCAN_PATTERN.search(right) is not None:
        return False
    return (
        (separator == "再加" and right.startswith("热"))
        or (separator == "连带" and right.startswith(("责任", "关系", "义务", "后果", "影响")))
        or (separator == "外带" and right.startswith(("练", "训", "餐", "服务")))
    )


def _has_multiple_explicit_target_values(scope: str) -> bool:
    strip_characters = " ，,。.!！?？；;、/|｜&+·•"
    compact_scope = scope.strip(strip_characters)
    if _NATURAL_SINGLE_TARGET_CONJUNCTION_PATTERN.fullmatch(compact_scope) is not None:
        return False
    separators = list(_EXPLICIT_TARGET_LIST_SEPARATOR_PATTERN.finditer(scope))
    if not separators:
        return False
    segments: list[str] = []
    cursor = 0
    for separator in separators:
        segments.append(scope[cursor : separator.start()].strip(strip_characters))
        cursor = separator.end()
    segments.append(scope[cursor:].strip(strip_characters))
    left_values: list[str] = []
    current = ""
    for segment in segments:
        if segment:
            current = segment
        left_values.append(current)
    right_values = [""] * len(segments)
    current = ""
    for index in range(len(segments) - 1, -1, -1):
        if segments[index]:
            current = segments[index]
        right_values[index] = current
    for index, separator in enumerate(separators):
        left = left_values[index]
        right = right_values[index + 1]
        if not left or not right:
            continue
        kind = separator.lastgroup
        if kind == "punctuation":
            if separator.group() in "，," and (
                _REMINDER_PATTERN.fullmatch(right) is not None
                or _UPDATE_TRAILING_CLAUSE_PREFIX_PATTERN.fullmatch(right) is not None
            ):
                continue
            return True
        if kind == "conditional":
            comparison_right = _TARGET_DESCRIPTOR_PREFIX_PATTERN.sub("", right)
            if _conditional_target_separator_is_lexical_continuation(
                separator.group(),
                comparison_right,
            ):
                continue
            return True
        if kind == "word":
            return True
        if (
            separator.group() in {"/", "&"}
            and re.fullmatch(
                r"[A-Za-z][A-Za-z0-9+#.-]{0,7}[/&][A-Za-z][A-Za-z0-9+#.-]{0,7}"
                r"(?:设计|研究|开发|测试|课程|学习|复盘|方案|文档)",
                compact_scope,
            )
            is not None
        ):
            continue
        return True
    return False


def _has_invalid_explicit_target_tail(scope: str) -> bool:
    stripped = scope.rstrip(" ，,。.!！?？；;:：")
    if not stripped:
        return False
    last_clause = re.split(r"[，,。.!！?？；;:：]+", stripped)[-1]
    return (
        _ABORT_PATTERN.fullmatch(last_clause) is not None
        or _UPDATE_TRAILING_CONNECTOR_PATTERN.search(last_clause) is not None
    )


_EXPLICIT_UPDATE_OPERATION_PREFIX_PATTERN = re.compile(r"[，,。.!！?？；;](?:并把|并将|把|将)$")


def _update_target_boundary(scope: str, modifier_start: int) -> int:
    if _EXPLICIT_UPDATE_FIELD_MODIFIER_PATTERN.match(scope, modifier_start) is None:
        return modifier_start
    operation_prefix = _EXPLICIT_UPDATE_OPERATION_PREFIX_PATTERN.search(scope[:modifier_start])
    return operation_prefix.start() if operation_prefix is not None else modifier_start


_IMPLICIT_TARGET_TEMPORAL_SELECTOR_PATTERN = re.compile(
    rf"(?:昨天|昨日|前天|大前天|次日|翌日|当日|当天|今日|"
    rf"明早|明晚|今早|今晨|今夜|明晨|明夜|昨晚|昨夜|"
    rf"(?:上上|上|本|这|下|下下)(?:周|星期|礼拜)(?:[一二三四五六日天末])?|"
    rf"(?:周|星期|礼拜)[一二三四五六日天末]|"
    rf"(?:上上|上|本|这|下|下下)(?:个月|月|季度|季|年)|"
    rf"(?:未来|接下来|往后|过去|近|最近|这|那|前|后|上|下)?"
    rf"[{_NUMBER_TOKEN_CHARACTERS}]+(?:天|日|周|星期|礼拜|个月|月|季度|年)"
    rf"(?:内|以内|之内|以来|前|后)?|(?:上|中|下)旬|"
    rf"去年|今年|明年|后年|前年|(?:月|年)(?:初|中|底|末)|"
    rf"(?:上|本|这|下)?月底|年末|年底|"
    rf"凌晨|清晨|早晨|早上|上午|中午|下午|傍晚|晚上|晚间|深夜|夜里|夜间|"
    rf"近期|最近|近日|日前|前阵子|不久前|稍后|稍早|晚些时候|早些时候)"
)


def _has_implicit_temporal_target_selector(text: str, intent: IntentName) -> bool:
    if intent not in {
        IntentName.UPDATE_TASK,
        IntentName.UPDATE_EVENT,
        IntentName.DELETE_TASK,
        IntentName.DELETE_EVENT,
    }:
        return False
    normalized = _compact_semantic_text(text)
    masked = _mask_paired_quotes(normalized)
    if masked is None:
        return True
    target_end = len(masked)
    title_delimiter = _title_delimiter_position(masked)
    if title_delimiter is not None:
        target_end = title_delimiter
    if intent in {IntentName.UPDATE_TASK, IntentName.UPDATE_EVENT}:
        target_boundaries = [
            _update_target_boundary(masked, match.start())
            for pattern in (_RENAME_MODIFIER_PATTERN, _UPDATE_FIELD_MODIFIER_PATTERN)
            if (match := pattern.search(masked)) is not None
        ]
        if target_boundaries:
            target_end = min(target_end, *target_boundaries)
    target_scope = masked[:target_end]
    return (
        _DATE_CANDIDATE_PATTERN.search(target_scope) is not None
        or _TIME_CANDIDATE_PATTERN.search(target_scope) is not None
        or _UNSUPPORTED_TIME_PERIOD_PATTERN.search(target_scope) is not None
        or _IMPLICIT_TARGET_TEMPORAL_SELECTOR_PATTERN.search(target_scope) is not None
    )


def _has_ambiguous_update_target(text: str, intent: IntentName) -> bool:
    if intent not in {
        IntentName.UPDATE_TASK,
        IntentName.UPDATE_EVENT,
        IntentName.DELETE_TASK,
        IntentName.DELETE_EVENT,
    }:
        return False
    if _has_implicit_temporal_target_selector(text, intent):
        return True
    normalized = _compact_semantic_text(text)
    masked = _mask_paired_quotes(normalized)
    quote_spans = _paired_quote_spans(normalized)
    if masked is None or quote_spans is None:
        return True
    target_end = len(normalized)
    if intent in {IntentName.UPDATE_TASK, IntentName.UPDATE_EVENT}:
        target_boundaries = [
            _update_target_boundary(masked, match.start())
            for pattern in (_RENAME_MODIFIER_PATTERN, _UPDATE_FIELD_MODIFIER_PATTERN)
            if (match := pattern.search(masked)) is not None
        ]
        if target_boundaries:
            target_end = min(target_boundaries)
    if intent in {IntentName.UPDATE_TASK, IntentName.UPDATE_EVENT}:
        normalized_target_scope = normalized[:target_end]
        masked_target_scope = masked[:target_end]
        rightmost_target_start = max(
            (
                target.start()
                for target in _TARGET_SIGNAL_SCAN_PATTERN.finditer(normalized_target_scope)
            ),
            default=-1,
        )
        for hidden_object_marker in _HIDDEN_UPDATE_OBJECT_MARKER_PATTERN.finditer(
            masked_target_scope
        ):
            if (
                hidden_object_marker.lastgroup == "object_first"
                and _SUPPORTED_OBJECT_FIRST_COMMAND_START_PATTERN.fullmatch(
                    normalized[: hidden_object_marker.end()]
                )
                is not None
            ):
                continue
            if _hidden_update_marker_is_lexical_continuation(
                normalized_target_scope,
                hidden_object_marker,
                rightmost_target_start=rightmost_target_start,
            ):
                continue
            preceding_target = _extract_target_title(
                normalized[: hidden_object_marker.start()],
                intent,
            )
            following_target = normalized[hidden_object_marker.end() : target_end].strip(
                " ，,。.!！?？；;：:"
            )
            explicit_prefix_object = (
                hidden_object_marker.lastgroup == "object_first"
                and normalized[: hidden_object_marker.start()].endswith(("：", ":"))
            )
            if following_target and (
                (
                    hidden_object_marker.lastgroup == "object_first"
                    and (preceding_target is not None or explicit_prefix_object)
                )
                or (hidden_object_marker.lastgroup == "relational" and preceding_target is not None)
            ):
                return True
    target_quote_spans = tuple(span for span in quote_spans if span.start < target_end)
    if len(target_quote_spans) > 1:
        return True
    for span in target_quote_spans:
        before = normalized[: span.start]
        before_separator = _MULTIPLE_TARGET_SEPARATOR_PATTERN.search(before)
        if before_separator is not None and before_separator.end() == len(before):
            return True
        if _MULTIPLE_TARGET_SEPARATOR_PATTERN.match(normalized[span.end : target_end]) is not None:
            return True
    if target_quote_spans:
        span = target_quote_spans[0]
        preceding_target = _extract_target_title(normalized[: span.start], intent)
        quote_starts_separate_target = preceding_target is None or (
            span.start > 0 and normalized[span.start - 1] in "，,。.!！?？；;"
        )
        if _MIXED_TARGET_QUOTE_PREFIX_PATTERN.search(normalized[: span.start]) is not None:
            quote_starts_separate_target = True
        if quote_starts_separate_target:
            unquoted_target_scope = normalized[: span.start] + normalized[span.end : target_end]
            if _extract_target_title(unquoted_target_scope, intent) is not None:
                return True
    title_delimiter = _title_delimiter_position(masked)
    if title_delimiter is not None:
        explicit_target_scope = masked[title_delimiter + 1 : target_end]
        if _has_invalid_explicit_target_tail(explicit_target_scope):
            return True
        if not target_quote_spans and _has_multiple_explicit_target_values(explicit_target_scope):
            return True
    elif not target_quote_spans:
        extracted_target = _extract_target_title(normalized[:target_end], intent)
        if extracted_target is not None and _has_multiple_explicit_target_values(extracted_target):
            return True
    if intent not in {IntentName.UPDATE_TASK, IntentName.UPDATE_EVENT}:
        return False
    rename_matches = list(_RENAME_MODIFIER_PATTERN.finditer(masked))
    if len(rename_matches) > 1 or _has_repeated_independent_mutation(masked):
        return True
    rename = _rename_clause_span(normalized)
    if rename is not None and rename.quoted:
        trailing = normalized[rename.payload_end + 1 :].lstrip()
        trailing_without_particle = trailing.rstrip("吧呢啊呀哦")
        if trailing_without_particle and (
            trailing_without_particle[0] not in _UPDATE_TRAILING_SINGLE_CHARACTERS
            and _UPDATE_TRAILING_CLAUSE_PREFIX_PATTERN.match(trailing_without_particle) is None
        ):
            return True
    if title_delimiter is not None:
        explicit_scope = masked[title_delimiter + 1 :]
        boundary = re.search(r"[，,。.!！?？；;]", explicit_scope)
        if boundary is not None:
            first_clause = explicit_scope[: boundary.start()]
            remaining = explicit_scope[boundary.end() :]
            if (
                _UPDATE_FIELD_MODIFIER_PATTERN.search(first_clause) is not None
                and _RENAME_MODIFIER_PATTERN.search(first_clause) is None
                and _UPDATE_FIELD_MODIFIER_PATTERN.search(remaining) is not None
            ):
                return True
    if rename is None:
        return False
    target_start = title_delimiter + 1 if title_delimiter is not None else 0
    target_scope = masked[target_start : rename.modifier_start]
    return _UPDATE_FIELD_MODIFIER_PATTERN.search(target_scope) is not None


def _has_contextual_ambiguous_target(text: str, context: Sequence[str]) -> bool:
    if not context or _classify_intent(context[-1]) not in {
        IntentName.UPDATE_TASK,
        IntentName.UPDATE_EVENT,
        IntentName.DELETE_TASK,
        IntentName.DELETE_EVENT,
    }:
        return False
    normalized = _compact_semantic_text(text)
    masked = _mask_paired_quotes(normalized)
    if masked is None:
        return True
    modifier = _UPDATE_FIELD_MODIFIER_PATTERN.search(masked)
    target_end = modifier.start() if modifier is not None else len(masked)
    target_scope = masked[:target_end]
    normalized_target_scope = normalized[:target_end]
    rightmost_target_start = max(
        (
            target.start()
            for target in _TARGET_SIGNAL_SCAN_PATTERN.finditer(normalized_target_scope)
        ),
        default=-1,
    )
    strip_characters = " ，,。.!！?？；;:：、/|｜&+·•"

    def target_value(start: int, end: int) -> str:
        value = normalized[start:end].strip(strip_characters)
        value = _strip_outer_quoted_literal(value)
        return _TARGET_DESCRIPTOR_PREFIX_PATTERN.sub("", value)

    if _has_multiple_explicit_target_values(target_scope):
        return True
    for marker in _HIDDEN_UPDATE_OBJECT_MARKER_PATTERN.finditer(target_scope):
        if _hidden_update_marker_is_lexical_continuation(
            normalized_target_scope,
            marker,
            rightmost_target_start=rightmost_target_start,
        ):
            continue
        left = target_value(0, marker.start())
        right = target_value(marker.end(), target_end)
        if left and right:
            return True
    if target_scope == normalized[:target_end]:
        return False
    for separator in _EXPLICIT_TARGET_LIST_SEPARATOR_PATTERN.finditer(target_scope):
        left = target_value(0, separator.start())
        right = target_value(separator.end(), target_end)
        if left and right:
            return True
    return False


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
_DELETE_SIGNALS = ("删除", "删掉", "移除", "取消", "撤销", "撤掉")
_SAFETY_ONLY_MUTATION_SIGNAL_PATTERN = (
    r"(?:新增|增加|增添|增设|新设|创立|创设|建立|设立|登记|补充|设个|预订|"
    r"录入|写入|导入|导进|置入|编入|存入|载入|"
    r"插入|记入|列入|排入|纳入|收录|添上|添入|建个|记下|录下|约上|补录|"
    r"增补|预约|预定|变更|变动|更改|改动|编辑|修订|修正|改写|刷新|延期|"
    r"延迟|推延|展期|顺延|延后|推后|调期|换期|移期|重排|重设|迁移|换掉|"
    r"替换|换成|挪动|挪到|前移|后移|重做|校正|校订|清理|放弃|舍弃|遗弃|"
    r"弃置|终止|停止|住手|停手|撤回|撤下|移出|移开|清退|踢出|丢(?:掉|弃|开)|"
    r"扔掉|抛弃|销除|关闭|结束|解散|下架|撤单|砍掉|消掉|摘除|划掉|勾掉|"
    r"清理掉|消除|移走|作废|归零|重置|"
    r"(?:删|移|去|拿|除|关|撤|清|抹|擦|剔)(?:掉|除|去|空|零|走)|"
    r"废(?:除|弃|止|掉)|(?:勾销|注销|销(?:掉|毁)))"
)
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
_MUTATION_SIGNAL_WORD_PATTERN = "|".join(
    re.escape(signal)
    for signal in sorted(
        {*_CREATE_SIGNALS, *_UPDATE_SIGNALS, *_DELETE_SIGNALS},
        key=len,
        reverse=True,
    )
)
_MUTATION_SIGNAL_PATTERN = (
    rf"(?:{_MUTATION_SIGNAL_WORD_PATTERN}|{_SAFETY_ONLY_MUTATION_SIGNAL_PATTERN})"
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
_INDEPENDENT_MUTATION_SIGNAL_WORD_PATTERN = "|".join(
    re.escape(signal)
    for signal in sorted(set(_INDEPENDENT_MUTATION_SIGNALS), key=len, reverse=True)
)
_INDEPENDENT_MUTATION_SIGNAL_PATTERN = (
    rf"(?:{_INDEPENDENT_MUTATION_SIGNAL_WORD_PATTERN}|"
    rf"{_SAFETY_ONLY_MUTATION_SIGNAL_PATTERN})"
)
_RAW_MUTATION_SIGNAL_PATTERN = rf"(?:{_MUTATION_SIGNAL_PATTERN}|删|改|加)"
_RAW_MUTATION_SIGNAL_SCAN_PATTERN = re.compile(_RAW_MUTATION_SIGNAL_PATTERN)

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
    r"(?:待办|任务|作业|日历事件|日历|日程|事件|组会|会议|考试|答辩|"
    r"讲座|课程|它|这个|那个)"
)
_MUTATION_COMMAND_LEAD_PATTERN = re.compile(
    rf"(?:把|将|{_MUTATION_SIGNAL_PATTERN}|(?:删|改|加)(?={_TARGET_SIGNAL_PATTERN}))"
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
_COMMAND_PREFIX_TOKEN_PATTERN = (
    r"(?:请|帮我|麻烦|替我|给我|我想|我要|我需要|先|首先|马上|立刻|立即|"
    r"稍后|再次|分别|别忘了|不要忘记|记得|顺手|顺便|最后|也)"
)
_COMMAND_PREFIX_PATTERN = rf"(?:{_COMMAND_PREFIX_TOKEN_PATTERN})*"
_SEQUENTIAL_COMMAND_SEPARATOR_PATTERN = (
    rf"(?:[，,。.!！?？；;、]+|并且|然后|同时|接着|接下来|随后|之后|随即|随之|外加|"
    rf"另加|再加|外带|连带|继而|转而|反而|顺便|最后|再|又|且|"
    rf"后(?={_COMMAND_PREFIX_PATTERN}(?:(?:把|将)|"
    rf"(?:{_MUTATION_SIGNAL_PATTERN})|{_TARGET_SIGNAL_PATTERN})))"
)
_STRONG_COMMAND_SEPARATOR_PATTERN = re.compile(_SEQUENTIAL_COMMAND_SEPARATOR_PATTERN)
_COMMAND_SEPARATOR_PATTERN = re.compile(
    rf"(?:{_SEQUENTIAL_COMMAND_SEPARATOR_PATTERN}|以及|或者|和|与|并|"
    rf"顺带|顺道|另外|顺手|随手|额外|还(?:要|得)|顺势|再者)"
)
_DIRECT_MUTATION_COMMAND_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}(?:{_MUTATION_SIGNAL_PATTERN})"
)
_OBJECT_FIRST_MUTATION_COMMAND_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}(?:把|将)"
    rf"[^，,。.!！?？；;：:]{{0,128}}?(?:{_MUTATION_SIGNAL_PATTERN})"
)
_CONTEXT_OBJECT_FIRST_MUTATION_COMMAND_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}(?:把|将)"
    rf"{_TARGET_SIGNAL_PATTERN}[A-Za-z0-9_-]{{0,32}}"
    rf"(?:给|也|都)?(?:{_MUTATION_SIGNAL_PATTERN})"
)
_CONTEXT_TITLED_CALENDAR_CREATE_COMMAND_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}(?:把|将)"
    rf"(?:(?!(?:{_MUTATION_SIGNAL_PATTERN}))[^，,。.!！?？；;：:]){{1,64}}?"
    r"(?:日历事件|日程|事件|组会|会议|考试|答辩|讲座|课程)"
    r"[A-Za-z0-9_-]{0,32}(?:加到|加入|添加到|放到)"
    r"(?:我的)?(?:日历|日程)(?:里)?"
)
_CONTEXT_TARGET_FIRST_MUTATION_COMMAND_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}{_TARGET_SIGNAL_PATTERN}[A-Za-z0-9_-]{{0,32}}"
    rf"(?:给|也|都)?(?:{_MUTATION_SIGNAL_PATTERN})(?:掉|了)?"
)
_TARGET_FIRST_NARRATIVE_MARKER_PATTERN = (
    r"(?:由|被|让|今天|昨天|前天|刚才|刚刚?|方才|当时|那时|当初|早前|先前|"
    r"上周|上个月|前阵子|不久前|如今|现今|眼下|当前|已经|已|现已|曾经|"
    r"此前|之前|早就|目前|正在|正|还在|仍在|尚在|不能|不要|无需|不用|"
    r"不必|别|失败)"
)
_TARGET_FIRST_MUTATION_COMMAND_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}{_TARGET_SIGNAL_PATTERN}"
    rf"(?![^，,。.!！?？；;：:]{{0,64}}{_TARGET_FIRST_NARRATIVE_MARKER_PATTERN})"
    rf"[^，,。.!！?？；;：:]{{0,64}}?(?:给|也|都)?"
    rf"(?:{_MUTATION_SIGNAL_PATTERN})(?:掉|了)?"
)
_MUTATION_SIGNAL_SCAN_PATTERN = re.compile(rf"(?:{_MUTATION_SIGNAL_PATTERN})")
_INDEPENDENT_MUTATION_SIGNAL_SCAN_PATTERN = re.compile(
    rf"(?:{_INDEPENDENT_MUTATION_SIGNAL_PATTERN})"
)
_TARGET_SIGNAL_SCAN_PATTERN = re.compile(_TARGET_SIGNAL_PATTERN)
_MULTIPLE_TARGET_SEPARATOR_PATTERN = re.compile(
    r"(?:另一个|连同|并且|以及|针对|和|与|、|或者|或是|或|跟|同|并|及|还有|"
    r"对|向|给|拿|用|替|加上|外加|另加|再加|外带|连带|兼|暨|/|&|\+|\||｜|·|•)"
)
_EXPLICIT_TARGET_LIST_SEPARATOR_PATTERN = re.compile(
    r"(?P<conditional>另加|再加|外带|连带)|"
    r"(?P<word>连同|并且|以及|针对|加上|外加|和|与|或者|或是|或|跟|同|并|及|还有|对|向|给|拿|用|替|兼|暨)|"
    r"(?P<symbol>、|/|&|(?<!\+)\+(?!\+)|\||｜|·|•)|"
    r"(?P<punctuation>[，,。.!！?？；;])"
)
_MIXED_TARGET_QUOTE_PREFIX_PATTERN = re.compile(
    r"(?:连同|并且|以及|针对|加上|外加|另加|再加|外带|连带|和|与|、|或者|或是|"
    r"或|跟|同|并|及|还有|对|向|给|拿|用|替|兼|暨|"
    r"[，,。.!！?？；;])"
    r"(?:另一个|另|名为|叫做|名称为)?$"
)
_BULK_TARGET_QUANTIFIER_PATTERN = (
    r"(?:多个|所有|全部|这些|若干|前几个|一批|每个|任意|剩余|上述|以下|一切|"
    r"大部分|部分|俩|两|双|(?:\d+|[零〇一二两三四五六七八九十百千万几]+)"
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
    rf"(?:、|，|,|以及|针对|加上|外加|另加|再加|外带|连带|和|与|或者|或是|或|"
    rf"跟|及|还有|对|向|给|拿|用|替|兼|暨|"
    rf"/|&|\+|\||｜|·|•)"
    rf"[^：:]+"
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
_MUTATION_NARRATIVE_CONTINUATION_PATTERN = (
    r"(?:之后|以后|然后|并且|随后又?|随即|接下来|紧接着|接着|而后|继而|同时|"
    r"转而|后来|再后来|之后才|最终|所以|因此|因而|故而|于是|结果又?|但是|不过|可是|"
    r"后|但|却|又|再|而|并|且)"
)
_MUTATION_RESULT_STATE_VALUE_PATTERN = (
    r"(?:已(?:经)?(?:失败|成功)|"
    r"(?:尚未|还没|仍未|未能|没(?:有)?)成功|"
    r"失败|成功|(?:(?:正|还|仍|尚)?在)?(?:进行)?中|未果|不了)"
)
_UNQUOTED_RENAME_NARRATIVE_SUFFIX_PATTERN = re.compile(
    rf"(?:{_MUTATION_RESULT_STATE_VALUE_PATTERN}(?:了)?|了|"
    rf"{_MUTATION_TERMINAL_PARTICLE_PATTERN})"
    rf"{_MUTATION_TERMINAL_PARTICLE_RUN_PATTERN}$"
)
_MUTATION_RESULT_STATE_PATTERN = re.compile(
    rf"(?:{_RAW_MUTATION_SIGNAL_PATTERN})(?:[：:,，；;]|[-—–－−‑]+)?"
    rf"{_MUTATION_RESULT_STATE_VALUE_PATTERN}(?:了)?"
    rf"{_MUTATION_TERMINAL_PARTICLE_RUN_PATTERN}"
    rf"(?:(?:[。.!！?？…~～]+)?$|(?:[，,；;]?){_MUTATION_NARRATIVE_CONTINUATION_PATTERN}"
    r"[^，,。.!！?？；;：:]{1,9999}(?:[。.!！?？…~～]+)?$)"
)
_MUTATION_TARGET_RESULT_STATE_PATTERN = re.compile(
    rf"(?P<mutation>{_RAW_MUTATION_SIGNAL_PATTERN})"
    rf"(?P<target>[^。.!！?？；;（）()\r\n]{{0,64}}?"
    rf"(?:{_TARGET_SIGNAL_PATTERN})[^。.!！?？；;（）()\r\n]{{0,64}}?)"
    r"(?:[-—–－−‑]+|[（(])?"
    rf"(?P<state>{_MUTATION_RESULT_STATE_VALUE_PATTERN})(?:了)?"
    rf"{_MUTATION_TERMINAL_PARTICLE_RUN_PATTERN}(?:[）)])?"
    rf"(?:(?:[。.!！?？…~～]+)?$|(?:[，,；;]?){_MUTATION_NARRATIVE_CONTINUATION_PATTERN}"
    r"[^，,。.!！?？；;：:]{1,9999}(?:[。.!！?？…~～]+)?$)"
)
_MUTATION_NARRATIVE_MARKER_SCAN_PATTERN = re.compile(_TARGET_FIRST_NARRATIVE_MARKER_PATTERN)
_MUTATION_NARRATIVE_CONTINUATION_SCAN_PATTERN = re.compile(_MUTATION_NARRATIVE_CONTINUATION_PATTERN)
_MUTATION_NARRATIVE_COMPLETION_HEAD_PATTERN = re.compile(
    rf"(?:(?:{_MUTATION_RESULTATIVE_SUFFIX_PATTERN})"
    rf"(?:了|{_MUTATION_TERMINAL_PARTICLE_PATTERN}|"
    rf"(?={_MUTATION_NARRATIVE_CONTINUATION_PATTERN}))|了|"
    r"过(?:了)?|结束|完事(?:了)?|着(?:呢)?|(?:到)?一半(?:了)?)"
    rf"{_MUTATION_TERMINAL_PARTICLE_RUN_PATTERN}"
)
_MUTATION_NARRATIVE_TRAILING_ASPECT_END_PATTERN = re.compile(
    rf"(?:了|过){_MUTATION_TERMINAL_PARTICLE_RUN_PATTERN}$"
)


def _has_target_mutation_result_report(masked: str) -> bool:
    match = _MUTATION_TARGET_RESULT_STATE_PATTERN.search(masked)
    if match is None:
        return False
    title_delimiter = _title_delimiter_position(masked)
    return title_delimiter is None or not (
        match.start("mutation") < title_delimiter < match.start("state")
    )


def _has_mutation_narrative_completion(text: str, start: int) -> bool:
    if _MUTATION_NARRATIVE_CONTINUATION_SCAN_PATTERN.match(text, start) is not None:
        return True
    completion = _MUTATION_NARRATIVE_COMPLETION_HEAD_PATTERN.match(text, start)
    if completion is None:
        return False
    return completion.end() == len(text) or (
        _MUTATION_NARRATIVE_CONTINUATION_SCAN_PATTERN.match(text, completion.end()) is not None
    )


_NON_IMPERATIVE_MUTATION_PATTERN = re.compile(
    r"(?:只是|仅仅是|不过是|属于|系|由[^：:]{0,32}(?:负责|操作|完成)|"
    r"为[^：:]{0,32}(?:例子|示例)|是[^：:]{0,32}(?:的|记录|例子|示例)|"
    r"为什么|为何|何时|是否|能否|可否|是什么意思|什么(?:意思|含义)|的原因|"
    r"(?:要|该)?(?:怎么|如何)(?:操作|做|弄|恢复|处理)|的方法|的步骤|会(?:怎样|如何)|"
    r"怎么回事|(?:了|过)?(?:吗|么|呢)[。.!！?？]?$)"
)


def _has_unquoted_mutation_narrative(text: str) -> bool:
    masked = _mask_paired_quotes(_compact_semantic_text(text))
    if masked is None:
        return True
    if _MUTATION_RESULT_STATE_PATTERN.search(
        masked
    ) is not None or _has_target_mutation_result_report(masked):
        return True
    for clause in _REMINDER_CLAUSE_BOUNDARY_PATTERN.split(masked):
        signal_clause = _mutation_signal_scope(clause)
        if (
            _TARGET_SIGNAL_SCAN_PATTERN.search(signal_clause) is None
            and re.match(rf"^{_COMMAND_PREFIX_PATTERN}(?:把|将)", signal_clause) is None
        ):
            continue
        mutations = list(_RAW_MUTATION_SIGNAL_SCAN_PATTERN.finditer(signal_clause))
        if not mutations:
            continue
        trailing_aspect = _MUTATION_NARRATIVE_TRAILING_ASPECT_END_PATTERN.search(signal_clause)
        mutation_index = 0
        for marker in _MUTATION_NARRATIVE_MARKER_SCAN_PATTERN.finditer(signal_clause):
            following_mutation = _RAW_MUTATION_SIGNAL_SCAN_PATTERN.match(
                signal_clause,
                marker.end(),
            )
            if following_mutation is None:
                while (
                    mutation_index < len(mutations)
                    and mutations[mutation_index].start() < marker.end()
                ):
                    mutation_index += 1
                if mutation_index >= len(mutations):
                    continue
                following_mutation = mutations[mutation_index]
            mutation_end = following_mutation.end()
            if (
                mutation_end == len(signal_clause)
                or _has_mutation_narrative_completion(signal_clause, mutation_end)
                or (trailing_aspect is not None and mutation_end < trailing_aspect.start())
            ):
                return True
        if any(
            _has_mutation_narrative_completion(signal_clause, mutation.end())
            for mutation in mutations
        ):
            return True
    return False


_TITLE_META_PREFIX_PATTERN = re.compile(r"^(?:研究|学习|讨论|比较|分析|整理|记录|了解|阅读)")
_TITLE_META_SUFFIX_PATTERN = re.compile(
    r"(?:的(?:区别|差异|含义|概念|方法|教程|示例|例子|原因|原理)|"
    r"是什么意思|什么(?:意思|含义))$"
)
_META_TITLE_COORDINATION_BRIDGE_PATTERN = re.compile(rf"{_TARGET_SIGNAL_PATTERN}(?:和|与|及|、)")
_ABORT_POLITE_LEAD_PATTERN = r"(?:(?:麻烦|劳烦|劳驾|烦请|拜托|请)(?:你|您)?|帮(?:我|忙))"
_ABORT_DISCOURSE_LEAD_PATTERN = (
    r"(?:[嗯呃额唔哦啊唉哎诶欸呀]+|(?:好|行|对|是)(?:的|吧|啦|了)?|那个|这个)"
)
_ABORT_LEAD_PATTERN = (
    rf"(?:要不然|那就|还是|干脆|{_ABORT_POLITE_LEAD_PATTERN}|"
    rf"{_ABORT_DISCOURSE_LEAD_PATTERN}|要不|就此|那|就|先)"
)
_ABORT_SCOPE_PATTERN = r"(?:(?:这|本|此)(?:次|回))"
_MUTATION_LEADING_FILLER_PATTERN = re.compile(
    rf"(?:{_ABORT_LEAD_PATTERN}|{_ABORT_SCOPE_PATTERN}|{_COMMAND_PREFIX_TOKEN_PATTERN})"
)
_ABORT_LEAD_RUN_PATTERN = rf"(?:{_ABORT_LEAD_PATTERN})*+"
_ABORT_PREFIX_PATTERN = (
    rf"(?:{_ABORT_LEAD_RUN_PATTERN}(?:{_ABORT_SCOPE_PATTERN}"
    rf"{_ABORT_LEAD_RUN_PATTERN})?)"
)
_ABORT_WITHDRAW_VERB_PATTERN = r"(?:撤回|撤销|取消|停止|放弃|收回|撤)"
_ABORT_ACTION_ASPECT_PATTERN = r"(?:了(?:一下)?|一下)?"
_ABORT_WITHDRAW_PATTERN = (
    rf"{_ABORT_WITHDRAW_VERB_PATTERN}(?:一下)?"
    r"(?:(?:(?:这|本|此)(?:次|回)|这个|当前|刚才|刚刚)(?:的)?)?"
    rf"(?:修改|操作|变更){_ABORT_ACTION_ASPECT_PATTERN}"
)
_ABORT_OBJECT_FIRST_WITHDRAW_PATTERN = (
    r"(?:把|将)(?:(?:(?:这|本|此)(?:次|回)|这个|当前|刚才|刚刚)(?:的)?)?"
    rf"(?:修改|操作|变更){_ABORT_WITHDRAW_VERB_PATTERN}{_ABORT_ACTION_ASPECT_PATTERN}"
)
_ABORT_SCOPE_FIRST_WITHDRAW_PATTERN = (
    r"(?:(?:(?:这|本|此)(?:次|回)|这个|当前|刚才|刚刚)(?:的)?)"
    rf"(?:修改|操作|变更){_ABORT_WITHDRAW_VERB_PATTERN}{_ABORT_ACTION_ASPECT_PATTERN}"
)
_ABORT_NEGATOR_PATTERN = r"(?:(?>不要|不用|无需|无须|不必|请勿|甭|不|别|勿))(?:再)?"
_ABORT_STOP_ACTION_PATTERN = (
    rf"(?:{_MUTATION_SIGNAL_PATTERN}|弄|搞|做|干|办|删|改|加|管|动|处理|折腾|"
    r"继续|操作|执行|推进)"
)
_ABORT_PROGRESS_PREFIX_PATTERN = r"(?:(?:继续(?:往下|向下)?|(?:往下|向下)(?:继续)?))?"
_ABORT_ACTION_TARGET_PATTERN = (
    rf"(?:(?:这个|那个|上次的|之前的)?(?:{_TARGET_SIGNAL_PATTERN})"
    r"[A-Za-z0-9_-]{0,32})?"
)
_ABORT_NEGATED_ACTION_PATTERN = (
    rf"{_ABORT_NEGATOR_PATTERN}{_ABORT_PROGRESS_PREFIX_PATTERN}"
    rf"{_ABORT_STOP_ACTION_PATTERN}{_ABORT_ACTION_TARGET_PATTERN}"
    r"(?:一下(?:了)?|一遍(?:了)?|下去(?:了)?|起来(?:了)?|了(?:一下|一遍)?|"
    r"过(?:了)?|完(?:了)?|好(?:了)?|着(?:呢)?|到一半(?:了)?|结束|完事(?:了)?|妥了)?"
)
_ABORT_CORE_PATTERN = (
    rf"(?:(?:算(?:了|啦|咯|吧)){{1,2}}|当我没说(?:过)?(?:了)?|不作数(?:了)?|"
    rf"作罢(?:了)?|罢了|拉倒|不要了|不用了|打住(?:了)?|"
    r"(?:停(?!止)(?:下来|一下|一停)?|暂停(?:一下)?|收手)(?:了)?|"
    rf"{_ABORT_WITHDRAW_VERB_PATTERN}{_ABORT_ACTION_ASPECT_PATTERN}|"
    rf"{_ABORT_NEGATED_ACTION_PATTERN}|"
    r"别动(?:它|这个|任务|待办|日程|事件)(?:了)?|"
    r"(?:先)?到(?:此|这(?:里|儿)?)(?:为止)?(?:了)?|这样)"
)
_ABORT_PHRASE_PATTERN = (
    rf"{_ABORT_PREFIX_PATTERN}(?:{_ABORT_CORE_PATTERN}|{_ABORT_WITHDRAW_PATTERN}|"
    rf"{_ABORT_OBJECT_FIRST_WITHDRAW_PATTERN}|{_ABORT_SCOPE_FIRST_WITHDRAW_PATTERN})"
)
_ABORT_SEPARATOR_CHARACTER_STRING = "，,；;。.!！?？…~～、：:/\\|·•—–－−‑‐‒―-﹘﹣"
_ABORT_SEPARATOR_CHARACTERS = frozenset(_ABORT_SEPARATOR_CHARACTER_STRING)
_ABORT_TERMINAL_PARTICLE_CHARACTERS = frozenset("吧啦呀啊呢哦嘛哈呗咯喽哎诶哟唷嘞咧哩欸")
_ABORT_TERMINAL_PATTERN = (
    rf"{_MUTATION_TERMINAL_PARTICLE_RUN_PATTERN}"
    rf"(?:[{re.escape(_ABORT_SEPARATOR_CHARACTER_STRING)}]+)?"
)
_ABORT_PATTERN = re.compile(rf"^(?:{_ABORT_PHRASE_PATTERN}){_ABORT_TERMINAL_PATTERN}$")
_ABORT_SUFFIX_PATTERN = re.compile(rf"(?:{_ABORT_PHRASE_PATTERN}){_ABORT_TERMINAL_PATTERN}$")
_ABORT_TERMINAL_STRIP_PATTERN = re.compile(rf"{_ABORT_TERMINAL_PATTERN}$")
_ABORT_PHRASE_SUFFIX_PATTERN = re.compile(rf"(?:{_ABORT_PHRASE_PATTERN})$")
_CREATE_ABORT_OPERATION_LITERAL_PATTERN = re.compile(
    rf"^(?:{_ABORT_WITHDRAW_PATTERN}|{_ABORT_OBJECT_FIRST_WITHDRAW_PATTERN}|"
    rf"{_ABORT_SCOPE_FIRST_WITHDRAW_PATTERN}){_ABORT_TERMINAL_PATTERN}$"
)
_MAX_ABORT_PHRASE_CHARS = 512
_REPEATED_ABORT_PHRASE_PATTERN = re.compile(
    rf"(?>{_ABORT_PREFIX_PATTERN}(?:"
    rf"{_ABORT_WITHDRAW_PATTERN}|"
    rf"{_ABORT_OBJECT_FIRST_WITHDRAW_PATTERN}|"
    rf"{_ABORT_SCOPE_FIRST_WITHDRAW_PATTERN}|"
    rf"{_ABORT_CORE_PATTERN}"
    rf"))"
)


def _is_abort_separator(character: str) -> bool:
    return character in _ABORT_SEPARATOR_CHARACTERS or unicodedata.category(character)[0] in {
        "P",
        "S",
    }


def _abort_text_without_separators(value: str) -> tuple[str, tuple[int, ...]]:
    characters: list[str] = []
    source_positions: list[int] = []
    for index, character in enumerate(value):
        if _is_abort_separator(character):
            continue
        characters.append(character)
        source_positions.append(index)
    return "".join(characters), tuple(source_positions)


def _is_repeated_abort(normalized: str) -> bool:
    if not normalized or len(normalized) > _MAX_PARSE_TEXT_CHARACTERS:
        return False
    masked = _mask_paired_quotes_for_abort(normalized)
    if masked is None:
        return True
    collapsed, _ = _abort_text_without_separators(masked)
    cursor = 0
    count = 0
    while (phrase := _REPEATED_ABORT_PHRASE_PATTERN.match(collapsed, cursor)) is not None:
        if phrase.end() == cursor:
            return False
        cursor = phrase.end()
        count += 1
        while cursor < len(collapsed) and collapsed[cursor] in _ABORT_TERMINAL_PARTICLE_CHARACTERS:
            cursor += 1
    return count >= 1 and cursor == len(collapsed)


def _has_abort_prefix_before_mutation(normalized: str) -> bool:
    masked = _mask_paired_quotes_for_abort(normalized)
    if masked is None:
        return True
    last_mutation_start = max(
        (mutation.start() for mutation in _RAW_MUTATION_SIGNAL_SCAN_PATTERN.finditer(masked)),
        default=-1,
    )
    if last_mutation_start < 0:
        return False
    collapsed, source_positions = _abort_text_without_separators(masked)
    cursor = 0
    while (phrase := _REPEATED_ABORT_PHRASE_PATTERN.match(collapsed, cursor)) is not None:
        if phrase.end() == cursor:
            return False
        cursor = phrase.end()
        while cursor < len(collapsed) and collapsed[cursor] in _ABORT_TERMINAL_PARTICLE_CHARACTERS:
            cursor += 1
        if cursor < len(collapsed):
            next_source_start = source_positions[cursor]
            if last_mutation_start >= next_source_start:
                return True
    return False


def _find_abort_suffix_start_in_masked(masked: str) -> int | None:
    collapsed, source_positions = _abort_text_without_separators(masked)
    if not collapsed:
        return None
    scan_start = max(0, len(collapsed) - _MAX_ABORT_PHRASE_CHARS)
    matched = _ABORT_SUFFIX_PATTERN.search(collapsed, scan_start)
    if matched is None:
        terminal = _ABORT_TERMINAL_STRIP_PATTERN.search(collapsed)
        if terminal is None or terminal.start() == len(collapsed):
            return None
        semantic_end = terminal.start()
        scan_start = max(0, semantic_end - _MAX_ABORT_PHRASE_CHARS)
        matched = _ABORT_PHRASE_SUFFIX_PATTERN.search(collapsed, scan_start, semantic_end)
        if matched is None:
            restored_end = semantic_end + 1
            scan_start = max(0, restored_end - _MAX_ABORT_PHRASE_CHARS)
            matched = _ABORT_PHRASE_SUFFIX_PATTERN.search(collapsed, scan_start, restored_end)
    return None if matched is None else source_positions[matched.start()]


def _has_unquoted_abort_suffix(text: str) -> bool:
    masked = _mask_paired_quotes_for_abort(text)
    return masked is None or _find_abort_suffix_start_in_masked(masked) is not None


def _has_mutation_abort_tail(text: str, intent: IntentName) -> bool:
    normalized = _normalized_intent_text(text)
    if intent in {
        IntentName.UPDATE_TASK,
        IntentName.UPDATE_EVENT,
        IntentName.DELETE_TASK,
        IntentName.DELETE_EVENT,
    }:
        return _has_unquoted_abort_suffix(normalized)
    if intent not in {IntentName.CREATE_TASK, IntentName.CREATE_EVENT}:
        return False
    masked = _mask_paired_quotes_for_abort(normalized)
    if masked is None:
        return True
    suffix_start = _find_abort_suffix_start_in_masked(masked)
    if suffix_start is None:
        return False
    if _extract_title(normalized[:suffix_start], intent):
        return True
    literal = normalized[suffix_start:]
    return _CREATE_ABORT_OPERATION_LITERAL_PATTERN.fullmatch(literal) is None


_UPDATE_TASK_PRIORITY_PATTERN = re.compile(
    r"(?:优先级(?:改为|改成|调整为|设为|设置为|设成|设置成|更新为)?(?P<after>[高低])|"
    r"(?:改为|改成|调整为|设为|设置为|设成|设置成|更新为)(?P<before>[高低])优先级)"
    r"(?=$|[，,。.!！?？；;：:吧呢啊呀哦]|然后|并且|接着|随后|之后|顺便|同时|以及|并把|并将|又|再|且|并)"
)
_UPDATE_TASK_STATUS_PATTERN = re.compile(
    r"(?:(?:状态)?(?:改为|改成|调整为|设为|设置为|设成|设置成|更新为|标记为)"
    r"(?P<value>未完成|待办|已完成|完成)|恢复为(?P<restore>待办))"
    r"(?=$|[，,。.!！?？；;：:吧呢啊呀哦]|然后|并且|接着|随后|之后|顺便|同时|以及|并把|并将|又|再|且|并)"
)
_UPDATE_TASK_PRIORITY_CUE_PATTERN = re.compile(
    r"(?:优先级(?:改为|改成|调整为|设为|设置为|设成|设置成|更新为)?[高低]|"
    r"(?:改为|改成|调整为|设为|设置为|设成|设置成|更新为)[高低]优先级)"
)
_UPDATE_TASK_STATUS_CUE_PATTERN = re.compile(
    r"(?:(?:状态)?(?:改为|改成|调整为|设为|设置为|设成|设置成|更新为|标记为)"
    r"(?:未完成|待办|已完成|完成)|恢复为待办)"
)
_DIRECT_COMPLETE_TASK_PATTERN = re.compile(
    rf"^{_COMMAND_PREFIX_PATTERN}完成(?:这个|那个|上次的|之前的)?(?:待办|任务|作业)"
)
_DIRECT_COMPLETE_DANGLING_TAIL_PATTERN = re.compile(
    r"(?:[，,。.!！?？；;：:、]+"
    r"(?:然后|并且|接着|随后|之后|顺便|同时|以及|或者|或是|并把|并将|又|再|"
    r"且|并|把|将|连同|加上|外加|另加|再加|外带|连带|和|与|及|跟|同|还有|"
    r"对|向|给|拿|用|替|兼|暨|或)"
    r"[，,。.!！?？；;：:、]*|、)$"
)


def _find_task_update_fields(normalized: str) -> tuple[str | None, str | None]:
    field_scope = _masked_update_scan_scope(normalized)
    priority_match = _UPDATE_TASK_PRIORITY_PATTERN.search(field_scope)
    priority_value = None
    if priority_match is not None:
        level = priority_match.group("after") or priority_match.group("before")
        priority_value = {"高": "high", "低": "low"}[level]

    status_match = _UPDATE_TASK_STATUS_PATTERN.search(field_scope)
    status_value = None
    if status_match is not None:
        value = status_match.group("value") or status_match.group("restore")
        status_value = "pending" if value in {"未完成", "待办"} else "completed"
    elif _DIRECT_COMPLETE_TASK_PATTERN.search(_command_scope(normalized)) is not None:
        status_value = "completed"
    return priority_value, status_value


def _has_ambiguous_task_update_fields(text: str, intent: IntentName) -> bool:
    if intent != IntentName.UPDATE_TASK:
        return False
    normalized = _compact_semantic_text(text)
    field_scope = _masked_update_scan_scope(normalized)
    priority_count = sum(1 for _ in _UPDATE_TASK_PRIORITY_CUE_PATTERN.finditer(field_scope))
    status_count = sum(1 for _ in _UPDATE_TASK_STATUS_CUE_PATTERN.finditer(field_scope))
    direct_complete = _DIRECT_COMPLETE_TASK_PATTERN.search(_command_scope(normalized)) is not None
    return priority_count > 1 or status_count + int(direct_complete) > 1


_UPDATE_TEMPORAL_TRAILING_FIELD_PATTERN = re.compile(
    r"(?P<field>(?:(?:截止)?(?:日期|开始时间|时间)|截止))"
    r"(?:改为|改成|调整为|设为|设置为|设成|设置成|更新为|改到|推迟到|提前到|调整到|设置到)"
    r"(?P<value>.+)"
)
_UPDATE_IMPLICIT_TEMPORAL_TRAILING_FIELD_PATTERN = re.compile(
    r"(?:改到|推迟到|提前到|调整到|设置到)(?P<value>.+)"
)
_UPDATE_TRAILING_CLAUSE_PREFIX_PATTERN = re.compile(
    r"^(?:然后|并且|接着|随后|之后|顺便|同时|以及|并把|并将|又|再|且|并|把|将)+"
)
_UPDATE_TRAILING_CLAUSE_SEPARATOR_PATTERN = re.compile(
    r"(?:[，,。.!！?？；;]+|(?<!\d)[：:]|[：:](?!\d)|"
    r"然后|并且|接着|随后|之后|顺便|同时|以及|且|并|又|再|并把|并将|把|将)+"
)
_UPDATE_TRAILING_CONNECTOR_PATTERN = re.compile(
    r"(?:然后|并且|接着|随后|之后|顺便|同时|以及|且|并|又|再|并把|并将|把|将)+$"
)
_UPDATE_TEXT_TRAILING_FIELD_PATTERN = re.compile(
    r"(?P<field>地点|位置|课程|描述|备注)"
    r"(?:改为|改成|调整为|设为|设置为|设成|设置成|更新为)(?P<value>.+)"
)
_UPDATE_END_TIME_FIELD_PATTERN = re.compile(
    r"(?:结束|终止)时间(?:改为|改成|调整为|设为|设置为|设成|设置成|更新为|改到|推迟到|"
    r"提前到|调整到|设置到)"
)


def _is_supported_update_trailing_clause(clause: str, intent: IntentName) -> bool:
    clause = _UPDATE_TRAILING_CLAUSE_PREFIX_PATTERN.sub("", clause).rstrip("吧呢啊呀哦")
    if not clause or _ABORT_PATTERN.fullmatch(clause) is not None:
        return False
    if intent == IntentName.UPDATE_TASK and (
        _UPDATE_TASK_PRIORITY_PATTERN.fullmatch(clause) is not None
        or _UPDATE_TASK_STATUS_PATTERN.fullmatch(clause) is not None
    ):
        return True
    if _REMINDER_PATTERN.fullmatch(clause) is not None:
        return True
    text_field = _UPDATE_TEXT_TRAILING_FIELD_PATTERN.fullmatch(clause)
    if text_field is not None:
        if intent == IntentName.UPDATE_TASK and text_field.group("field") in {
            "地点",
            "位置",
        }:
            return False
        value = text_field.group("value")
        return (
            not _has_unquoted_abort_suffix(value)
            and _UPDATE_FIELD_MODIFIER_PATTERN.search(_masked_update_scan_scope(value)) is None
        )
    temporal = _UPDATE_TEMPORAL_TRAILING_FIELD_PATTERN.fullmatch(clause)
    explicit_field = temporal.group("field") if temporal is not None else None
    if temporal is None:
        temporal = _UPDATE_IMPLICIT_TEMPORAL_TRAILING_FIELD_PATTERN.fullmatch(clause)
    if temporal is None:
        return False
    value = temporal.group("value")
    has_date = _DATE_CANDIDATE_PATTERN.search(value) is not None
    has_time = _TIME_FRAGMENT_PATTERN.search(value) is not None
    has_reminder = _REMINDER_AMOUNT_PATTERN.search(value) is not None
    if explicit_field is not None:
        if explicit_field.endswith("日期") and (not has_date or has_time or has_reminder):
            return False
        if explicit_field == "开始时间" and (not has_time or has_date or has_reminder):
            return False
        if explicit_field in {"时间", "截止时间", "截止"} and (
            not (has_date or has_time) or has_reminder
        ):
            return False
    remainder = _without_temporal_phrases_outside_quotes(value)
    return not remainder.strip("吧呢啊呀哦")


def _split_update_trailing_clauses(scope: str) -> list[str]:
    masked = _mask_paired_quotes(scope)
    if masked is None:
        return []
    clauses: list[str] = []
    cursor = 0
    for separator in _UPDATE_TRAILING_CLAUSE_SEPARATOR_PATTERN.finditer(masked):
        clause = scope[cursor : separator.start()]
        if clause:
            clauses.append(clause)
        cursor = separator.end()
    trailing = scope[cursor:]
    if trailing:
        clauses.append(trailing)
    return clauses


def _has_invalid_update_clauses(text: str, intent: IntentName) -> bool:
    if intent not in {IntentName.UPDATE_TASK, IntentName.UPDATE_EVENT}:
        return False
    normalized = _compact_semantic_text(text)
    if (
        intent == IntentName.UPDATE_TASK
        and _DIRECT_COMPLETE_TASK_PATTERN.search(_command_scope(normalized)) is not None
        and (
            _has_invalid_explicit_target_tail(normalized)
            or _DIRECT_COMPLETE_DANGLING_TAIL_PATTERN.search(normalized) is not None
        )
    ):
        return True
    if _UPDATE_END_TIME_FIELD_PATTERN.search(_masked_update_scan_scope(normalized)) is not None:
        return True
    rename = _rename_clause_span(normalized)
    if rename is not None:
        if not rename.quoted:
            payload = normalized[rename.payload_start : rename.payload_end]
            if _has_unquoted_abort_suffix(payload):
                return True
            if _UNQUOTED_RENAME_NARRATIVE_SUFFIX_PATTERN.search(payload) is not None:
                return True
            if _UPDATE_TRAILING_CONNECTOR_PATTERN.search(payload) is not None:
                return True
            for separator in _UPDATE_TRAILING_CLAUSE_SEPARATOR_PATTERN.finditer(payload):
                if _is_supported_update_trailing_clause(payload[separator.end() :], intent):
                    return True
        trailing_start = rename.payload_end + int(rename.quoted)
        operation_scope = normalized[trailing_start:]
        if not operation_scope:
            return False
        if not operation_scope.strip("，,。.!！?？；;：:吧呢啊呀哦"):
            return False
        if (
            operation_scope[0] not in _UPDATE_TRAILING_SINGLE_CHARACTERS
            and _UPDATE_TRAILING_CLAUSE_PREFIX_PATTERN.match(operation_scope) is None
        ):
            return True
    else:
        field_scope = _masked_update_scan_scope(normalized)
        modifier = _UPDATE_FIELD_MODIFIER_PATTERN.search(field_scope)
        if modifier is None:
            return False
        command_scope = _command_scope(normalized)
        object_first_operation = _supported_object_first_mutation_start(command_scope)
        if object_first_operation is not None and object_first_operation > modifier.start():
            operation_scope = normalized[object_first_operation:]
        else:
            operation_scope = normalized[modifier.start() :]

    scope_without_terminal_punctuation = operation_scope.rstrip("，,。.!！?？；;：:")
    if not scope_without_terminal_punctuation:
        return False
    if _UPDATE_TRAILING_CONNECTOR_PATTERN.search(scope_without_terminal_punctuation) is not None:
        return True
    clauses = _split_update_trailing_clauses(operation_scope)
    return not clauses or any(
        not _is_supported_update_trailing_clause(clause, intent) for clause in clauses
    )


def _semantic_intent_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
        and not unicodedata.category(character).startswith("M")
    )


def _semantic_character_expansion(character: str) -> tuple[str, ...]:
    return tuple(
        expanded
        for expanded in unicodedata.normalize("NFKC", character)
        if unicodedata.category(expanded) != "Cf"
        and not unicodedata.category(expanded).startswith("M")
    )


def _is_ascii_word_character(character: str) -> bool:
    return character.isascii() and character.isalnum()


def _ascii_word_apostrophe_source_indexes(
    semantic_expansions: tuple[tuple[str, ...], ...],
) -> frozenset[int]:
    semantic_characters = [
        (source_index, expanded)
        for source_index, expansion in enumerate(semantic_expansions)
        for expanded in expansion
    ]
    apostrophe_indexes: set[int] = set()
    for position in range(1, len(semantic_characters) - 1):
        apostrophe_index, character = semantic_characters[position]
        if character != "'":
            continue
        _, preceding_character = semantic_characters[position - 1]
        _, following_character = semantic_characters[position + 1]
        if _is_ascii_word_character(preceding_character) and _is_ascii_word_character(
            following_character
        ):
            apostrophe_indexes.add(apostrophe_index)
    return frozenset(apostrophe_indexes)


def _source_symbol_needs_safety_separator(
    character: str,
    semantic_expansion: tuple[str, ...],
) -> bool:
    if (
        character in _QUOTE_OPEN_TO_CLOSE
        or character in _QUOTE_CLOSE_CHARACTERS
        or unicodedata.category(character)[0] not in {"P", "S"}
    ):
        return False
    return not semantic_expansion or any(
        unicodedata.category(expanded)[0] not in {"P", "S"} for expanded in semantic_expansion
    )


def _safety_source_text(text: str) -> str:
    semantic_expansions = tuple(_semantic_character_expansion(character) for character in text)
    word_apostrophe_indexes = _ascii_word_apostrophe_source_indexes(semantic_expansions)
    return "".join(
        "/"
        if (
            index in word_apostrophe_indexes
            or unicodedata.category(character)[0] == "M"
            or (unicodedata.category(character)[0] == "C" and not character.isspace())
            or _source_symbol_needs_safety_separator(
                character,
                semantic_expansions[index],
            )
        )
        else character
        for index, character in enumerate(text)
    )


def _safety_semantic_text(text: str) -> str:
    return _semantic_intent_text(_safety_source_text(text))


_SEMANTIC_FORMAT_CHARACTERS = frozenset(("\u200b", "\u2060", "\ufeff"))


def _is_unsafe_nonsemantic_character(character: str) -> bool:
    category = unicodedata.category(character)
    return category.startswith("M") or (category.startswith("C") and not character.isspace())


def _is_disruptive_nonsemantic_character(character: str) -> bool:
    return (
        _is_unsafe_nonsemantic_character(character) and character not in _SEMANTIC_FORMAT_CHARACTERS
    )


def _nonsemantic_projection(
    text: str,
) -> tuple[str, tuple[int, ...], frozenset[int]]:
    stripped_characters: list[str] = []
    source_indexes: list[int] = []
    disruptive_indexes: set[int] = set()
    for index, character in enumerate(unicodedata.normalize("NFKC", text)):
        if _is_unsafe_nonsemantic_character(character):
            if _is_disruptive_nonsemantic_character(character):
                disruptive_indexes.add(index)
            continue
        stripped_characters.append(character)
        source_indexes.append(index)
    return (
        "".join(stripped_characters),
        tuple(source_indexes),
        frozenset(disruptive_indexes),
    )


def _source_span_contains_disruptive_nonsemantic_character(
    match: re.Match[str],
    source_indexes: tuple[int, ...],
    disruptive_indexes: frozenset[int],
) -> bool:
    if match.start() == match.end():
        return False
    start = source_indexes[match.start()]
    end = source_indexes[match.end() - 1] + 1
    return any(start <= index <= end for index in disruptive_indexes)


def _has_unsafe_nonsemantic_mutation_injection(text: str) -> bool:
    stripped_text, source_indexes, disruptive_indexes = _nonsemantic_projection(text)
    if not disruptive_indexes:
        return False
    stripped_intent = _classify_intent(stripped_text)
    if (
        stripped_intent not in _MUTATING_INTENTS
        or _classify_intent(_safety_semantic_text(text)) == stripped_intent
    ):
        return False
    title_delimiter = _title_delimiter_position(stripped_text)
    command_scope = stripped_text if title_delimiter is None else stripped_text[:title_delimiter]
    for pattern in (_MUTATION_SIGNAL_SCAN_PATTERN, _TARGET_SIGNAL_SCAN_PATTERN):
        for match in pattern.finditer(command_scope):
            if _source_span_contains_disruptive_nonsemantic_character(
                match,
                source_indexes,
                disruptive_indexes,
            ):
                return True
    return False


def _has_unsafe_contextual_temporal_mutation_injection(
    text: str,
    context: Sequence[str],
) -> bool:
    stripped_text, _, disruptive_indexes = _nonsemantic_projection(text)
    if not context or not disruptive_indexes:
        return False
    stripped_normalized = _normalized_intent_text(stripped_text)
    return (
        _is_pure_temporal_clarification(stripped_normalized)
        and not _is_pure_temporal_clarification(_safety_normalized_intent_text(text))
        and any(_classify_intent(previous_text) in _MUTATING_INTENTS for previous_text in context)
    )


def _semantic_compact_text_map(text: str) -> tuple[str, str, tuple[int, ...]]:
    semantic_text = _semantic_intent_text(text)
    compact_characters: list[str] = []
    compact_semantic_offsets: list[int] = []
    semantic_index = 0
    while semantic_index < len(semantic_text):
        character = semantic_text[semantic_index]
        if character in "\r\n":
            compact_characters.append(";")
            compact_semantic_offsets.append(semantic_index)
            semantic_index += 1
            while semantic_index < len(semantic_text) and semantic_text[semantic_index] in "\r\n":
                semantic_index += 1
            continue
        if not character.isspace():
            compact_characters.append(character)
            compact_semantic_offsets.append(semantic_index)
        semantic_index += 1
    compact_semantic_offsets.append(len(semantic_text))
    return semantic_text, "".join(compact_characters), tuple(compact_semantic_offsets)


def _compact_semantic_text(text: str) -> str:
    normalized = _semantic_intent_text(text)
    normalized = re.sub(r"[\r\n]+", ";", normalized)
    return "".join(character for character in normalized if not character.isspace())


def _normalized_intent_text(text: str) -> str:
    return _without_reminder_phrases(_compact_semantic_text(text))


def _safety_normalized_intent_text(text: str) -> str:
    return _normalized_intent_text(_safety_source_text(text))


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
    prefix = _TITLE_META_PREFIX_PATTERN.match(title)
    if _TITLE_META_SUFFIX_PATTERN.search(title) is not None and (
        prefix is not None or len(re.findall(_MUTATION_SIGNAL_PATTERN, title)) > 1
    ):
        return True
    if prefix is None:
        return False
    first_mutation = _RAW_MUTATION_SIGNAL_SCAN_PATTERN.search(title, prefix.end())
    if first_mutation is None or first_mutation.start() != prefix.end():
        return False
    previous_mutation = first_mutation
    for mutation in _RAW_MUTATION_SIGNAL_SCAN_PATTERN.finditer(title, first_mutation.end()):
        bridge = title[previous_mutation.end() : mutation.start()]
        if _META_TITLE_COORDINATION_BRIDGE_PATTERN.fullmatch(bridge) is None:
            return False
        previous_mutation = mutation
    return True


def _command_scope(normalized: str) -> str:
    scan_scope = _masked_update_scan_scope(normalized)
    title_delimiter = _title_delimiter_position(scan_scope)
    if title_delimiter is None:
        return scan_scope
    prefix = scan_scope[:title_delimiter]
    title = scan_scope[title_delimiter + 1 :]
    title_is_meta = _is_meta_title_description(title)
    separator = _STRONG_COMMAND_SEPARATOR_PATTERN if title_is_meta else _COMMAND_SEPARATOR_PATTERN
    title_clauses = separator.split(title)
    trailing_commands = [
        clause
        for clause in title_clauses[1:]
        if _is_explicit_mutation_clause(clause)
        and _UPDATE_TEXT_TRAILING_FIELD_PATTERN.fullmatch(clause.rstrip("吧呢啊呀哦")) is None
    ]
    if not title_is_meta:
        prefix_is_update = any(
            signal in _mutation_signal_scope(prefix) for signal in _UPDATE_SIGNALS
        )
        field_spans = iter(
            match.span() for match in _EXPLICIT_UPDATE_FIELD_MODIFIER_PATTERN.finditer(title)
        )
        field_span = next(field_spans, None) if prefix_is_update else None
        has_title_content = False
        checked_title_content = False
        for mutation in _RAW_MUTATION_SIGNAL_SCAN_PATTERN.finditer(title):
            if mutation.start() == 0:
                has_title_content = True
                checked_title_content = True
                continue
            if not checked_title_content:
                preceding_title = _without_temporal_phrases_outside_quotes(
                    title[: mutation.start()]
                ).strip("，,。.!！?？；;:：")
                has_title_content = bool(preceding_title)
                checked_title_content = True
            if not has_title_content:
                has_title_content = True
                continue
            preceding_title = title[: mutation.start()]
            if mutation.group() == "完成" and preceding_title.endswith(
                ("已", "未", "已经", "尚未", "已被")
            ):
                continue
            while field_span is not None and field_span[1] <= mutation.start():
                field_span = next(field_spans, None)
            if (
                field_span is not None
                and field_span[0] <= mutation.start()
                and mutation.end() <= field_span[1]
            ):
                continue
            clause = title[mutation.start() :]
            if (
                clause not in trailing_commands
                and _is_explicit_mutation_clause(clause)
                and _UPDATE_TEXT_TRAILING_FIELD_PATTERN.fullmatch(clause.rstrip("吧呢啊呀哦"))
                is None
            ):
                trailing_commands.append(clause)
                break
    return ";".join((prefix, *trailing_commands))


def _explicit_target_scope(normalized: str) -> str:
    return re.split(r"[，,。.!！?？；;、]", _command_scope(normalized), maxsplit=1)[0]


def _has_multiple_target_mutation(command_scope: str) -> bool:
    if _DIRECT_MUTATION_COMMAND_PATTERN.search(command_scope) is None:
        return False
    first_target = _TARGET_SIGNAL_SCAN_PATTERN.search(command_scope)
    if first_target is None:
        return False
    separator = _MULTIPLE_TARGET_SEPARATOR_PATTERN.search(command_scope, first_target.end())
    if separator is None:
        return False
    return _TARGET_SIGNAL_SCAN_PATTERN.search(command_scope, separator.end()) is not None


def _has_repeated_independent_mutation(command_scope: str) -> bool:
    first_mutation = _MUTATION_SIGNAL_SCAN_PATTERN.search(command_scope)
    if first_mutation is None:
        return False
    second_mutation = _INDEPENDENT_MUTATION_SIGNAL_SCAN_PATTERN.search(
        command_scope, first_mutation.end()
    )
    if second_mutation is None:
        return False
    return _TARGET_SIGNAL_SCAN_PATTERN.search(command_scope, second_mutation.end()) is not None


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
    first_clause_is_counted_mutation = bool(clauses) and (
        _DIRECT_MUTATION_COMMAND_PATTERN.search(clauses[0]) is not None
        or (
            _OBJECT_FIRST_MUTATION_COMMAND_PATTERN.search(clauses[0]) is not None
            and re.search(_TARGET_SIGNAL_PATTERN, clauses[0]) is not None
        )
    )
    leading_colloquial = (
        bool(clauses)
        and not first_clause_is_counted_mutation
        and _COLLOQUIAL_MUTATION_PATTERN.match(clauses[0]) is not None
    )
    if (
        _has_multiple_target_mutation(command_scope)
        or _BULK_TARGET_MUTATION_PATTERN.search(command_scope) is not None
        or _CREATE_BULK_TARGET_MUTATION_PATTERN.search(command_scope) is not None
        or _IMPLICIT_MULTIPLE_TARGET_PATTERN.search(command_scope) is not None
        or _has_repeated_independent_mutation(command_scope)
        or _REPEATED_MUTATION_PREDICATE_PATTERN.search(command_scope) is not None
        or (leading_colloquial and count >= 1)
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


_OBJECT_FIRST_COMMAND_START_PATTERN = re.compile(r"^[^，,。.!！?？；;：:把将]*(?:把|将)")
_SUPPORTED_OBJECT_FIRST_COMMAND_START_PATTERN = re.compile(
    r"^(?:(?:请|帮我|麻烦|替我|给我|我想|我要|我需要))?(?:把|将)"
)
_OBJECT_FIRST_PREDICATE_LEAD_PATTERN = re.compile(r"(?:给|也|都)$")
_OBJECT_FIRST_TARGET_SUFFIX_PATTERN = re.compile(r"[A-Za-z0-9_ -]{1,64}")
_OBJECT_FIRST_TARGET_PUNCTUATION_PATTERN = re.compile(r"[，,。.!！?？；;：:、]")
_OBJECT_FIRST_ASCII_TITLE_PATTERN = re.compile(r"[A-Za-z0-9_+\-]{1,32}")
_OBJECT_FIRST_LEXICAL_TITLES = frozenset(
    {
        "机器学习",
        "项目",
        "软件工程",
        "高等数学",
        "安全培训",
        "毕业",
        "人工智能导论",
        "化学实验",
        "课题",
        "论文",
    }
)
_OBJECT_FIRST_NON_LEXICAL_TITLE_PATTERN = re.compile(
    r"(?:现在|随后|之后|后来|最终|故而|所以|因此|因而|于是|然后|接着|"
    r"准备|打算|计划|预计|决定|考虑|想|试图|企图|尝试|"
    r"(?:说|做|弄|处理)完(?:了)?(?:再|后)?|待会|等会|马上|立即|"
    r"将要|即将|可能|应该|需要|负责|通知|告知|表示|提到|声称|称|先|再|才|就)"
)
_OBJECT_FIRST_INTERROGATIVE_TITLE_PATTERN = re.compile(
    r"(?:哪个|哪一个|哪项|哪条|哪份|什么|何种|谁(?:的)?)"
)


def _is_supported_object_first_lexical_title(value: str) -> bool:
    has_anchored_lexical_title = (
        _OBJECT_FIRST_ASCII_TITLE_PATTERN.fullmatch(value) is not None
        or value in _OBJECT_FIRST_LEXICAL_TITLES
    )
    return (
        bool(value)
        and len(value) <= 64
        and has_anchored_lexical_title
        and _DATE_CANDIDATE_PATTERN.search(value) is None
        and _TIME_CANDIDATE_PATTERN.search(value) is None
        and _UNSUPPORTED_TIME_PERIOD_PATTERN.search(value) is None
        and _IMPLICIT_TARGET_TEMPORAL_SELECTOR_PATTERN.search(value) is None
        and _MUTATION_NARRATIVE_MARKER_SCAN_PATTERN.search(value) is None
        and _OBJECT_FIRST_NON_LEXICAL_TITLE_PATTERN.search(value) is None
        and re.search(_BULK_TARGET_QUANTIFIER_PATTERN, value) is None
        and re.search(_MUTATION_RESULT_STATE_VALUE_PATTERN, value) is None
        and _OBJECT_FIRST_INTERROGATIVE_TITLE_PATTERN.search(value) is None
    )


def _has_explicit_object_first_predicate_target(scope: str) -> bool:
    target = _TARGET_SIGNAL_SCAN_PATTERN.match(scope)
    return target is not None and target.end() < len(scope)


def _is_supported_object_first_target_scope(scope: str) -> bool:
    if (
        not scope
        or len(scope) > 128
        or _OBJECT_FIRST_TARGET_PUNCTUATION_PATTERN.search(scope) is not None
        or _RAW_MUTATION_SIGNAL_SCAN_PATTERN.search(scope) is not None
    ):
        return False
    rightmost_target: re.Match[str] | None = None
    for target in _TARGET_SIGNAL_SCAN_PATTERN.finditer(scope):
        rightmost_target = target
    if rightmost_target is None:
        return False
    if rightmost_target.start() == 0:
        suffix = scope[rightmost_target.end() :]
        return (
            not suffix
            or _OBJECT_FIRST_TARGET_SUFFIX_PATTERN.fullmatch(suffix) is not None
            or _EXPLICIT_TARGET_VALUE_HINT_PATTERN.fullmatch(suffix) is not None
        )
    if rightmost_target.end() != len(scope):
        return False
    return _is_supported_object_first_lexical_title(scope[: rightmost_target.start()])


def _supported_object_first_mutation_start(scope: str) -> int | None:
    object_first = _SUPPORTED_OBJECT_FIRST_COMMAND_START_PATTERN.match(scope)
    if object_first is None:
        return None
    first_mutation = _RAW_MUTATION_SIGNAL_SCAN_PATTERN.search(scope, object_first.end())
    if first_mutation is None:
        return None
    field_modifier = _UPDATE_FIELD_MODIFIER_PATTERN.search(scope, object_first.end())
    raw_direct_scope = scope[object_first.end() : first_mutation.start()]
    direct_scope = _OBJECT_FIRST_PREDICATE_LEAD_PATTERN.sub("", raw_direct_scope)
    predicate_lead_length = len(raw_direct_scope) - len(direct_scope)
    if predicate_lead_length and not _has_explicit_object_first_predicate_target(direct_scope):
        return None
    field_follows_mutation = (
        field_modifier is not None and first_mutation.end() <= field_modifier.start()
    )
    if not field_follows_mutation and _is_supported_object_first_target_scope(direct_scope):
        return first_mutation.start() - predicate_lead_length
    if field_modifier is None or not (
        field_modifier.start() <= first_mutation.start()
        and first_mutation.end() <= field_modifier.end()
    ):
        return None
    if _is_supported_object_first_target_scope(scope[object_first.end() : field_modifier.start()]):
        return field_modifier.start()
    return None


def _is_supported_object_first_mutation_command(scope: str) -> bool:
    return _supported_object_first_mutation_start(scope) is not None


def _starts_with_supported_mutation_command(scope: str) -> bool:
    return (
        _DIRECT_MUTATION_COMMAND_PATTERN.match(scope) is not None
        or _CONTEXT_TARGET_FIRST_MUTATION_COMMAND_PATTERN.match(scope) is not None
        or _is_supported_object_first_mutation_command(scope)
    )


def _has_unsafe_object_first_mutation_order(normalized: str) -> bool:
    scope = _command_scope(normalized)
    if _OBJECT_FIRST_COMMAND_START_PATTERN.match(scope) is None:
        return False
    if (
        _RAW_MUTATION_SIGNAL_SCAN_PATTERN.search(scope) is None
        or _TARGET_SIGNAL_SCAN_PATTERN.search(scope) is None
    ):
        return False
    return not _is_supported_object_first_mutation_command(scope)


def _is_leading_mutation_noise(character: str) -> bool:
    return unicodedata.category(character)[0] in {"P", "S"}


def _leading_mutation_noise_end(scope: str) -> int:
    cursor = 0
    while cursor < len(scope):
        previous_cursor = cursor
        while cursor < len(scope) and _is_leading_mutation_noise(scope[cursor]):
            cursor += 1
        filler = _MUTATION_LEADING_FILLER_PATTERN.match(scope, cursor)
        if filler is not None:
            separator_end = filler.end()
            while separator_end < len(scope) and _is_leading_mutation_noise(scope[separator_end]):
                separator_end += 1
            if separator_end > filler.end():
                cursor = separator_end
        if cursor == previous_cursor:
            break
    return cursor


def _first_mutation_command_start(scope: str) -> int | None:
    if (
        _DIRECT_MUTATION_COMMAND_PATTERN.match(scope) is not None
        or _CONTEXT_TARGET_FIRST_MUTATION_COMMAND_PATTERN.match(scope) is not None
        or _TARGET_FIRST_MUTATION_COMMAND_PATTERN.match(scope) is not None
        or _is_supported_object_first_mutation_command(scope)
    ):
        return 0
    command = _MUTATION_COMMAND_LEAD_PATTERN.search(scope)
    return None if command is None else command.start()


def _has_leading_mutation_noise(normalized: str) -> bool:
    scope = _command_scope(normalized)
    noise_end = _leading_mutation_noise_end(scope)
    stripped_scope = scope[noise_end:]
    has_mutation_and_target = (
        _RAW_MUTATION_SIGNAL_SCAN_PATTERN.search(stripped_scope) is not None
        and _TARGET_SIGNAL_SCAN_PATTERN.search(stripped_scope) is not None
    )
    if not has_mutation_and_target:
        return False
    if noise_end > 0:
        return True
    command_start = _first_mutation_command_start(scope)
    return (
        command_start is not None
        and command_start > 0
        and any(_is_leading_mutation_noise(character) for character in scope[:command_start])
    )


def _has_contextual_orphan_leading_mutation(
    normalized: str,
    context: Sequence[str],
) -> bool:
    if not context:
        return False
    scope = _command_scope(normalized)
    mutation = _RAW_MUTATION_SIGNAL_SCAN_PATTERN.search(scope)
    if mutation is None or mutation.start() == 0:
        return False
    if _starts_with_supported_mutation_command(scope):
        return False
    leading = scope[: mutation.start()].strip("，,。.!！?？；;:：、")
    return bool(leading)


def _explicitly_negates_mutation(normalized: str) -> bool:
    command_scope = _command_scope(normalized)
    return (
        _NEGATED_IMPERATIVE_MUTATION_PATTERN.search(command_scope) is not None
        or _NEGATED_STATEMENT_MUTATION_PATTERN.search(command_scope) is not None
    )


def _explicitly_aborts(normalized: str) -> bool:
    return _ABORT_PATTERN.fullmatch(normalized) is not None or _is_repeated_abort(normalized)


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
            and (
                _NON_IMPERATIVE_MUTATION_PATTERN.search(command_scope) is not None
                or _MUTATION_RESULT_STATE_PATTERN.search(command_scope) is not None
                or _has_unquoted_mutation_narrative(command_scope)
            )
        ),
    )


def _classify_intent(text: str) -> IntentName:
    normalized = _normalized_intent_text(text)
    signals = _intent_signals(normalized)
    event_signal = any(
        word in normalized
        for word in (
            "日历",
            "日程",
            "事件",
            "会议",
            "组会",
            "考试",
            "答辩",
            "讲座",
            "课程",
        )
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


def _fallback_parse_single(
    text: str,
    now: datetime,
    *,
    safety_source_text: str | None = None,
    precomputed_semantic_text: str | None = None,
    precomputed_safety_semantic_text: str | None = None,
) -> IntentResult:
    semantic_text = (
        precomputed_semantic_text
        if precomputed_semantic_text is not None
        else _semantic_intent_text(text)
    )
    normalized = re.sub(r"\s+", "", semantic_text)
    safety_semantic_text = (
        precomputed_safety_semantic_text
        if precomputed_safety_semantic_text is not None
        else _semantic_intent_text(
            safety_source_text if safety_source_text is not None else _safety_source_text(text)
        )
    )
    safety_normalized = re.sub(r"\s+", "", safety_semantic_text)
    intent = _classify_intent(semantic_text)
    if intent == IntentName.UNKNOWN:
        return _unknown_result(text)
    if (
        _has_unquoted_mutation_narrative(semantic_text)
        or _has_ambiguous_update_target(semantic_text, intent)
        or _has_mutation_abort_tail(safety_semantic_text, intent)
        or _has_abort_prefix_before_mutation(safety_normalized)
        or _has_leading_mutation_noise(safety_normalized)
        or _has_unsafe_object_first_mutation_order(normalized)
    ):
        return _unknown_result(text)

    temporal_scope = _temporal_slot_scope(semantic_text, intent)
    if intent in _MUTATING_INTENTS and _has_unsafe_mutation_temporal_scope(
        temporal_scope, now.date()
    ):
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
        title = _extract_target_title(text, intent)
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
        if intent == IntentName.CREATE_TASK:
            slot_signal_scope = _command_scope(_normalized_intent_text(semantic_text))
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
        else:
            slots.priority, slots.status = _find_task_update_fields(normalized)
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
    if _explicitly_aborts(_safety_normalized_intent_text(text)):
        return None
    if not _is_pure_temporal_clarification(normalized):
        return None
    if _has_unsafe_mutation_temporal_scope(normalized, now.date()):
        return None
    parsed_date = _find_date(normalized, now.date())
    start_time, end_time = _find_times(normalized)
    reminder_minutes = _find_reminder_minutes(normalized)
    for previous_text in reversed(context):
        if _explicitly_aborts(_safety_normalized_intent_text(previous_text)):
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


def _fallback_parse(
    text: str,
    now: datetime,
    context: Sequence[str] = (),
    *,
    safety_source_text: str | None = None,
    precomputed_semantic_text: str | None = None,
    precomputed_safety_semantic_text: str | None = None,
    precomputed_normalized: str | None = None,
    precomputed_safety_normalized: str | None = None,
) -> IntentResult:
    current = _fallback_parse_single(
        text,
        now,
        safety_source_text=safety_source_text,
        precomputed_semantic_text=precomputed_semantic_text,
        precomputed_safety_semantic_text=precomputed_safety_semantic_text,
    )
    if current.intent != IntentName.UNKNOWN:
        return current
    normalized = (
        precomputed_normalized
        if precomputed_normalized is not None
        else _normalized_intent_text(text)
    )
    safety_normalized = (
        precomputed_safety_normalized
        if precomputed_safety_normalized is not None
        else (
            _normalized_intent_text(safety_source_text)
            if safety_source_text is not None
            else _safety_normalized_intent_text(text)
        )
    )
    signals = _intent_signals(normalized)
    if (
        _explicitly_aborts(safety_normalized)
        or _has_abort_prefix_before_mutation(safety_normalized)
        or _explicitly_negates_mutation(safety_normalized)
        or _has_leading_mutation_noise(safety_normalized)
        or signals.conflicting
        or signals.query_mutation_conflict
    ):
        return current
    return _continue_from_context(text, context, now) or current


def _enforce_policy(result: IntentResult, asr_confidence: float | None) -> IntentResult:
    slots = result.slots
    ambiguities = list(dict.fromkeys(result.ambiguities))
    if re.search(r"(?:那个|这个|上次|之前的|这个考试)", result.source_text) and (
        result.intent == IntentName.UNKNOWN or not (slots.task_id or slots.event_id)
    ):
        ambiguities.append("指代不明确，需要确认具体对象")
    if result.intent == IntentName.UNKNOWN:
        return result.model_copy(
            update={
                "slots": IntentSlots(),
                "missing_fields": [],
                "ambiguities": list(dict.fromkeys(ambiguities)),
                "requires_confirmation": False,
            }
        )
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
    if result.intent == IntentName.UNKNOWN:
        return result.model_copy(update={"slots": IntentSlots()})
    slots = result.slots.model_copy(deep=True)
    temporal_scope = _temporal_slot_scope(text, result.intent)
    if _has_unsafe_mutation_temporal_scope(temporal_scope, now.date()):
        return result
    parsed_date = _find_date(temporal_scope, now.date())
    start_time, end_time = _find_times(temporal_scope)
    reminder_minutes = _find_reminder_minutes(temporal_scope)

    if reminder_minutes is not None and result.intent in _MUTATING_INTENTS:
        slots.reminder_minutes = reminder_minutes
    if result.intent in {IntentName.CREATE_EVENT, IntentName.UPDATE_EVENT}:
        slots.date = parsed_date or slots.date
        slots.start_time = start_time or slots.start_time
        slots.end_time = end_time or slots.end_time
    elif result.intent in {IntentName.CREATE_TASK, IntentName.UPDATE_TASK}:
        slots.due_date = parsed_date or slots.due_date
        slots.due_time = start_time or slots.due_time
    return result.model_copy(update={"slots": slots})


_METADATA_ASSIGNMENT_OPERATOR_PATTERN = r"(?:改为|改成|调整为|设为|设置为|设成|设置成|更新为)"
_METADATA_VALUE_BOUNDARY_CHARACTERS = frozenset("，,。.!！?？；;:")
_METADATA_NEXT_UPDATE_FIELD_PATTERN = re.compile(
    rf"^{_UPDATE_FIELD_NAME_PATTERN}{_UPDATE_FIELD_ASSIGNMENT_PATTERN}"
)
_METADATA_EMBEDDED_UPDATE_FIELD_PATTERN = re.compile(
    rf"{_UPDATE_FIELD_NAME_PATTERN}{_UPDATE_FIELD_ASSIGNMENT_PATTERN}"
)


def _metadata_candidate_bounds(
    source_text: str,
    start: int,
    end: int,
) -> tuple[int, int]:
    while start > 0 and end < len(source_text):
        opener = source_text[start - 1]
        if _QUOTE_OPEN_TO_CLOSE.get(opener) != source_text[end]:
            break
        start -= 1
        end += 1
    return start, end


def _metadata_value_has_right_boundary(
    source_text: str,
    end: int,
    *,
    allow_terminal_particles: bool = False,
) -> bool:
    suffix = source_text[end:]
    if not suffix or suffix[0] in _METADATA_VALUE_BOUNDARY_CHARACTERS:
        return True
    particle = re.match(r"[吧呢啊呀哦]+", suffix)
    if allow_terminal_particles and particle is not None:
        remainder = suffix[particle.end() :]
        return not remainder or remainder[0] in _METADATA_VALUE_BOUNDARY_CHARACTERS
    connector = _UPDATE_TRAILING_CLAUSE_PREFIX_PATTERN.match(suffix)
    return (
        connector is not None
        and _METADATA_NEXT_UPDATE_FIELD_PATTERN.match(suffix[connector.end() :]) is not None
    )


def _metadata_value_has_explicit_cue(
    field: str,
    candidate: str,
    source_text: str,
) -> bool:
    embedded_update_field = _METADATA_EMBEDDED_UPDATE_FIELD_PATTERN.search(candidate) is not None
    escaped = re.escape(candidate)
    negated_pattern = (
        rf"(?:不要|不用|无需|不必|请勿|勿|禁止|不能|不准|不可|不得|不应|"
        rf"不想|不愿|不再|别|不是|并非|非|不在|没有|尚未|未(?!来)|没|不曾)"
        rf"[^，,。.!！?？；;:]{{0,16}}{escaped}"
    )
    if re.search(negated_pattern, source_text) is not None:
        return False
    if field == "description":
        label_pattern = r"(?:描述|备注|说明|内容)"
        reverse_pattern = re.compile(r"^(?:作为)?(?:描述|备注|说明)")
    elif field == "course":
        label_pattern = r"(?:课程|科目)"
        reverse_pattern = re.compile(r"^(?:课程|课)")
    elif field == "location":
        label_pattern = r"(?:地点|位置)"
        reverse_pattern = None
    else:
        return False
    prefix_cue = re.compile(
        rf"{label_pattern}(?:(?:为|是|:)|{_METADATA_ASSIGNMENT_OPERATOR_PATTERN})?$"
    )
    position = source_text.find(candidate)
    while position >= 0:
        candidate_end = position + len(candidate)
        value_start, value_end = _metadata_candidate_bounds(source_text, position, candidate_end)
        quoted_value = value_start != position
        if embedded_update_field and not quoted_value:
            position = source_text.find(candidate, position + 1)
            continue
        if prefix_cue.search(
            source_text[:value_start]
        ) is not None and _metadata_value_has_right_boundary(
            source_text,
            value_end,
            allow_terminal_particles=quoted_value,
        ):
            return True
        if reverse_pattern is not None:
            reverse = reverse_pattern.match(source_text[value_end:])
            if reverse is not None and _metadata_value_has_right_boundary(
                source_text,
                value_end + reverse.end(),
                allow_terminal_particles=quoted_value,
            ):
                return True
        if (
            field == "location"
            and re.search(
                r"(?:在|位于)[^，,。.!！?？；;:]{0,8}$",
                source_text[:value_start],
            )
            is not None
            and _metadata_value_has_right_boundary(
                source_text,
                value_end,
                allow_terminal_particles=quoted_value,
            )
        ):
            return True
        position = source_text.find(candidate, position + 1)
    return False


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
    semantic_candidate = _semantic_intent_text(candidate)
    if candidate != semantic_candidate or candidate not in source_text:
        return None
    candidate_quote_spans = _paired_quote_spans(semantic_candidate)
    if candidate_quote_spans is None or candidate_quote_spans:
        return None
    if any(unicodedata.category(character).startswith("C") for character in candidate):
        return None
    if not _metadata_value_has_explicit_cue(field, candidate, source_text):
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
        if len(cleaned) > _MAX_PARSE_TEXT_CHARACTERS:
            return _enforce_policy(
                _unknown_result(cleaned[:_MAX_PARSE_TEXT_CHARACTERS]), asr_confidence
            )
        fallback_semantic_text = _semantic_intent_text(cleaned)
        semantic_text = fallback_semantic_text.strip()
        safety_source_text = _safety_source_text(cleaned)
        fallback_safety_semantic_text = _semantic_intent_text(safety_source_text)
        safety_semantic_text = fallback_safety_semantic_text.strip()
        if not semantic_text:
            raise IntentParseError("empty_text", "请输入或转写一段文本后再解析。")
        if len(semantic_text) > _MAX_PARSE_TEXT_CHARACTERS or _context_exceeds_limits(context):
            return _enforce_policy(_unknown_result(cleaned), asr_confidence)
        timezone = ZoneInfo(timezone_name) if timezone_name is not None else self._timezone
        if now is None:
            current = datetime.now(timezone)
        elif now.tzinfo is None:
            current = now.replace(tzinfo=timezone)
        else:
            current = now.astimezone(timezone)
        if _paired_quote_spans(semantic_text) is None:
            return _enforce_policy(_unknown_result(cleaned), asr_confidence)
        normalized = _normalized_intent_text(semantic_text)
        safety_normalized = _normalized_intent_text(safety_source_text)
        signals = _intent_signals(normalized)
        classified_intent = _classify_intent(semantic_text)
        contextual_update_intent = next(
            (
                previous_intent
                for previous_text in reversed(context)
                if (previous_intent := _classify_intent(previous_text))
                in {IntentName.UPDATE_TASK, IntentName.UPDATE_EVENT}
            ),
            None,
        )
        unsafe_nonsemantic_mutation_injection = _has_unsafe_nonsemantic_mutation_injection(
            cleaned
        ) or any(
            _has_unsafe_nonsemantic_mutation_injection(previous_text) for previous_text in context
        )
        unsafe_contextual_temporal_mutation_injection = (
            _has_unsafe_contextual_temporal_mutation_injection(cleaned, context)
        )
        if (
            _has_ambiguous_update_target(semantic_text, classified_intent)
            or _has_ambiguous_task_update_fields(semantic_text, classified_intent)
            or _has_invalid_update_clauses(semantic_text, classified_intent)
            or (
                classified_intent == IntentName.UNKNOWN
                and contextual_update_intent is not None
                and _has_invalid_update_clauses(semantic_text, contextual_update_intent)
            )
            or (
                classified_intent == IntentName.UNKNOWN
                and not signals.query
                and _has_contextual_ambiguous_target(semantic_text, context)
            )
        ):
            return _enforce_policy(_unknown_result(cleaned), asr_confidence)
        temporal_safety_scope = _compact_semantic_text(semantic_text)
        classified_temporal_scope = _temporal_slot_scope(semantic_text, classified_intent)
        unsafe_mutation_temporal_scope = (
            classified_intent in _MUTATING_INTENTS
            and _has_unsafe_mutation_temporal_scope(classified_temporal_scope, current.date())
        )
        event_single_end_time_scope = (
            classified_intent in {IntentName.CREATE_EVENT, IntentName.UPDATE_EVENT}
            and _UNSUPPORTED_EVENT_SINGLE_END_TIME_PATTERN.search(classified_temporal_scope)
            is not None
        )
        contextual_unsafe_temporal_scope = (
            classified_intent == IntentName.UNKNOWN or (bool(context) and not signals.query)
        ) and _has_unsafe_mutation_temporal_scope(temporal_safety_scope, current.date())
        contextual_event_single_end_time_scope = (
            bool(context)
            and _UNSUPPORTED_EVENT_SINGLE_END_TIME_PATTERN.search(temporal_safety_scope) is not None
            and any(
                _classify_intent(previous_text)
                in {IntentName.CREATE_EVENT, IntentName.UPDATE_EVENT}
                for previous_text in reversed(context)
            )
        )
        deterministic_safety_conflict = (
            unsafe_nonsemantic_mutation_injection
            or unsafe_contextual_temporal_mutation_injection
            or _explicitly_aborts(safety_normalized)
            or _has_abort_prefix_before_mutation(safety_normalized)
            or _explicitly_negates_mutation(safety_normalized)
            or _has_leading_mutation_noise(safety_normalized)
            or signals.conflicting
            or signals.query_mutation_conflict
            or (
                classified_intent == IntentName.UNKNOWN
                and signals.raw_mutation
                and not signals.query
                and not context
            )
            or _has_mutation_abort_tail(safety_semantic_text, classified_intent)
            or (bool(context) and _has_unquoted_abort_suffix(safety_normalized))
            or _has_unquoted_mutation_narrative(semantic_text)
            or _has_unsafe_object_first_mutation_order(normalized)
            or _has_contextual_orphan_leading_mutation(normalized, context)
            or unsafe_mutation_temporal_scope
            or event_single_end_time_scope
            or contextual_unsafe_temporal_scope
            or contextual_event_single_end_time_scope
        )
        if deterministic_safety_conflict:
            return _enforce_policy(_unknown_result(cleaned), asr_confidence)
        deterministic_fallback = _enrich_deterministically(
            _fallback_parse(
                cleaned,
                current,
                context,
                safety_source_text=safety_source_text,
                precomputed_semantic_text=fallback_semantic_text,
                precomputed_safety_semantic_text=fallback_safety_semantic_text,
                precomputed_normalized=normalized,
                precomputed_safety_normalized=safety_normalized,
            ),
            semantic_text,
            current,
        )
        if self._llm is None:
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
