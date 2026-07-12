import json
import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from app.core.config import Settings
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
        api_key: str,
        model: str,
        timeout_seconds: float = 20,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds

    async def _complete(self, messages: list[dict[str, str]]) -> str:
        headers = {"Authorization": f"Bearer {self._api_key}"}
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
    relative = {"今天": 0, "明天": 1, "后天": 2}
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


def _hour_number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if value.startswith("十") and value[1:] in _CHINESE_DIGITS:
        return 10 + _CHINESE_DIGITS[value[1:]]
    if value.endswith("十") and value[:-1] in _CHINESE_DIGITS:
        return _CHINESE_DIGITS[value[:-1]] * 10
    return _CHINESE_DIGITS.get(value)


def _find_times(text: str) -> tuple[str | None, str | None]:
    pattern = re.compile(
        r"(?:(?P<period>凌晨|早上|上午|中午|下午|晚上))?"
        r"(?P<hour>\d{1,2}|[零一二两三四五六七八九十]{1,3})"
        r"(?:[:点时](?P<minute>\d{1,2})?分?)"
    )
    matches = list(pattern.finditer(text))
    values: list[str] = []
    for match in matches[:2]:
        hour = _hour_number(match.group("hour"))
        if hour is None:
            continue
        minute = int(match.group("minute") or 0)
        period = match.group("period") or ""
        if period in {"下午", "晚上"} and hour < 12:
            hour += 12
        if period == "中午" and hour < 11:
            hour += 12
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            continue
        values.append(f"{hour:02d}:{minute:02d}")
    return (values[0] if values else None, values[1] if len(values) > 1 else None)


def _extract_title(text: str, intent: IntentName) -> str | None:
    candidates: list[str] = []
    if "把" in text:
        after = text.split("把", 1)[1]
        candidates.append(re.split(r"(?:加到|加入|添加到|放到|设为|创建成)", after, maxsplit=1)[0])
    candidates.append(
        re.sub(
            r"^(?:请|帮我|麻烦)?(?:创建|新建|添加|记一个|安排)(?:一个)?"
            r"(?:待办|任务|日程|日历事件|事件)?[：:，, ]*",
            "",
            text,
        )
    )
    for candidate in candidates:
        cleaned = candidate
        cleaned = re.sub(r"20\d{2}[年\-/]\d{1,2}[月\-/]\d{1,2}日?", "", cleaned)
        cleaned = re.sub(r"\d{1,2}月\d{1,2}[日号]", "", cleaned)
        cleaned = re.sub(r"(?:今天|明天|后天)", "", cleaned)
        cleaned = re.sub(
            r"(?:凌晨|早上|上午|中午|下午|晚上)?(?:\d{1,2}|[零一二两三四五六七八九十]{1,3})(?:[:点时]\d{0,2}分?)",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"(?:加到|加入|添加到|放到)(?:我的)?(?:日历|日程|待办)(?:里)?",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"(?:创建|新建|添加)(?:一个)?(?:待办|任务|日程|日历事件|事件)",
            "",
            cleaned,
        )
        cleaned = cleaned.strip("，,。.!！?？ ：:")
        if intent == IntentName.CREATE_TASK:
            cleaned = re.sub(r"^(?:待办|任务)[：:，, ]*", "", cleaned).strip()
        if cleaned and cleaned not in {"待办", "任务", "日程", "事件", "日历"}:
            return cleaned
    return None


def _extract_target_title(text: str, intent: IntentName) -> str | None:
    candidate = text.strip()
    if "把" in candidate:
        candidate = candidate.split("把", 1)[1]
    candidate = re.sub(
        r"^(?:请|帮我|麻烦)?(?:删除|删掉|移除|取消|修改|更新|调整|完成)", "", candidate
    )
    candidate = re.sub(
        r"^(?:这个|那个|上次的|之前的)?(?:待办|任务|日历事件|日程|事件)[：:，, ]*", "", candidate
    )
    if intent in {IntentName.UPDATE_TASK, IntentName.UPDATE_EVENT}:
        candidate = re.split(
            r"(?:的)?(?:标题|优先级|状态|截止时间|截止日期|时间|日期|地点)?"
            r"(?:改名为|重命名为|改为|改成|更新为|调整为|设为|标记为|改到|推迟到|提前到)",
            candidate,
            maxsplit=1,
        )[0]
        candidate = re.split(
            r"到(?:今天|明天|后天|20\d{2}[年\-/]|\d{1,2}月)", candidate, maxsplit=1
        )[0]
    candidate = re.sub(r"20\d{2}[年\-/]\d{1,2}[月\-/]\d{1,2}日?", "", candidate)
    candidate = re.sub(r"\d{1,2}月\d{1,2}[日号]", "", candidate)
    candidate = re.sub(r"(?:今天|明天|后天)", "", candidate)
    candidate = re.sub(
        r"(?:凌晨|早上|上午|中午|下午|晚上)?"
        r"(?:\d{1,2}|[零一二两三四五六七八九十]{1,3})(?:[:点时]\d{0,2}分?)",
        "",
        candidate,
    )
    candidate = re.sub(r"(?:这个)?(?:待办|任务|日历事件|日程|事件)$", "", candidate)
    cleaned = candidate.strip("，,。.!！?？ ：:")
    return cleaned or None


def _new_title(text: str) -> str | None:
    match = re.search(r"(?:改名为|重命名为|标题改为)[：:，, ]*(?P<title>[^，,。.!！?？]+)", text)
    return match.group("title").strip() if match else None


def _classify_intent(text: str) -> IntentName:
    normalized = re.sub(r"\s+", "", text)
    create_signal = any(
        word in normalized for word in ("创建", "新建", "添加", "加到", "加入", "安排", "记一个")
    )
    delete_signal = any(word in normalized for word in ("删除", "删掉", "移除", "取消"))
    update_signal = any(
        word in normalized
        for word in (
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
    )
    event_signal = any(
        word in normalized
        for word in ("日历", "日程", "事件", "会议", "组会", "考试", "答辩", "讲座")
    )
    task_signal = any(word in normalized for word in ("待办", "任务", "作业", "复习"))
    notice_signal = any(word in normalized for word in ("通知", "公告", "报名", "奖学金", "教务"))
    query_signal = any(
        word in normalized
        for word in (
            "查询",
            "查看",
            "搜索",
            "查找",
            "查一下",
            "找一下",
            "什么时候",
            "有哪些",
            "有什么",
        )
    )

    if delete_signal and event_signal:
        return IntentName.DELETE_EVENT
    if delete_signal and task_signal:
        return IntentName.DELETE_TASK
    if update_signal and event_signal:
        return IntentName.UPDATE_EVENT
    if update_signal and task_signal:
        return IntentName.UPDATE_TASK
    if create_signal and event_signal:
        return IntentName.CREATE_EVENT
    if create_signal and task_signal:
        return IntentName.CREATE_TASK
    if notice_signal and (query_signal or not (create_signal or update_signal or delete_signal)):
        return IntentName.SEARCH_NOTICE
    if event_signal and query_signal:
        return IntentName.QUERY_SCHEDULE
    return IntentName.UNKNOWN


def _fallback_parse_single(text: str, now: datetime) -> IntentResult:
    normalized = re.sub(r"\s+", "", text)
    intent = _classify_intent(text)
    if intent == IntentName.UNKNOWN:
        return IntentResult(
            intent=intent,
            confidence=0.25,
            slots=IntentSlots(),
            missing_fields=[],
            ambiguities=[],
            source_text=text,
            requires_confirmation=False,
        )

    parsed_date = _find_date(normalized, now.date())
    start_time, end_time = _find_times(normalized)
    if intent in {IntentName.CREATE_TASK, IntentName.CREATE_EVENT}:
        title = _extract_title(text, intent)
    elif intent in {
        IntentName.UPDATE_TASK,
        IntentName.DELETE_TASK,
        IntentName.UPDATE_EVENT,
        IntentName.DELETE_EVENT,
    }:
        title = _extract_target_title(text, intent)
    else:
        title = None
    slots = IntentSlots(title=title, new_title=_new_title(text))
    if intent in {IntentName.CREATE_EVENT, IntentName.UPDATE_EVENT}:
        slots.date = parsed_date
        slots.start_time = start_time
        slots.end_time = end_time
    elif intent in {IntentName.CREATE_TASK, IntentName.UPDATE_TASK}:
        slots.due_date = parsed_date
        slots.due_time = start_time
        if "未完成" in normalized or "恢复为待办" in normalized:
            slots.status = "pending"
        elif "完成" in normalized:
            slots.status = "completed"
        if "高优先级" in normalized:
            slots.priority = "high"
        elif "低优先级" in normalized:
            slots.priority = "low"
    elif intent == IntentName.SEARCH_NOTICE:
        slots.query = text.strip()
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
    normalized = re.sub(r"\s+", "", text)
    parsed_date = _find_date(normalized, now.date())
    start_time, end_time = _find_times(normalized)
    for previous_text in reversed(context):
        previous = _fallback_parse_single(previous_text, now)
        if previous.intent not in _MUTATING_INTENTS:
            continue
        slots = previous.slots.model_copy(deep=True)
        if previous.intent in {IntentName.CREATE_EVENT, IntentName.UPDATE_EVENT}:
            slots.date = parsed_date or slots.date
            slots.start_time = start_time or slots.start_time
            slots.end_time = end_time or slots.end_time
        elif previous.intent in {IntentName.CREATE_TASK, IntentName.UPDATE_TASK}:
            slots.due_date = parsed_date or slots.due_date
            slots.due_time = start_time or slots.due_time
            if "未完成" in normalized:
                slots.status = "pending"
            elif "完成" in normalized:
                slots.status = "completed"
        if previous.intent in {
            IntentName.UPDATE_TASK,
            IntentName.DELETE_TASK,
            IntentName.UPDATE_EVENT,
            IntentName.DELETE_EVENT,
        } and not (parsed_date or start_time or end_time):
            slots.title = _extract_target_title(text, previous.intent) or slots.title
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
    parsed_date = _find_date(text, now.date())
    start_time, end_time = _find_times(text)
    if result.intent in {IntentName.CREATE_EVENT, IntentName.UPDATE_EVENT}:
        slots.date = slots.date or parsed_date
        slots.start_time = slots.start_time or start_time
        slots.end_time = slots.end_time or end_time
    elif result.intent in {IntentName.CREATE_TASK, IntentName.UPDATE_TASK}:
        slots.due_date = slots.due_date or parsed_date
        slots.due_time = slots.due_time or start_time
    return result.model_copy(update={"slots": slots})


class IntentParser:
    def __init__(
        self,
        llm_client: IntentLlmClient | None = None,
        *,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self._llm = llm_client
        self._timezone = ZoneInfo(timezone_name)

    async def parse(
        self,
        text: str,
        *,
        context: Sequence[str] = (),
        asr_confidence: float | None = None,
        now: datetime | None = None,
    ) -> IntentResult:
        cleaned = text.strip()
        if not cleaned:
            raise IntentParseError("empty_text", "请输入或转写一段文本后再解析。")
        current = now or datetime.now(self._timezone)
        if self._llm is None:
            fallback = _enrich_deterministically(
                _fallback_parse(cleaned, current, context), cleaned, current
            )
            return _enforce_policy(fallback, asr_confidence)

        raw = await self._llm.extract(cleaned, context)
        try:
            parsed = IntentResult.model_validate(_json_object(raw))
        except (json.JSONDecodeError, ValueError, ValidationError) as first_error:
            repaired = await self._llm.repair(cleaned, raw, str(first_error))
            try:
                parsed = IntentResult.model_validate(_json_object(repaired))
            except (json.JSONDecodeError, ValueError, ValidationError) as second_error:
                raise IntentParseError(
                    "invalid_model_output",
                    "意图理解结果格式无效，未执行任何操作。请修改文本后重试。",
                ) from second_error
        if parsed.source_text != cleaned:
            parsed = parsed.model_copy(update={"source_text": cleaned})
        parsed = _enrich_deterministically(parsed, cleaned, current)
        return _enforce_policy(parsed, asr_confidence)


def build_intent_parser(settings: Settings) -> IntentParser:
    if settings.llm_base_url and settings.llm_api_key and settings.llm_model:
        client: IntentLlmClient | None = OpenAICompatibleIntentClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        )
    else:
        client = None
    return IntentParser(client, timezone_name=settings.timezone)
