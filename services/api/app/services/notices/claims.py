import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

EXTRACTOR_VERSION = "deterministic-claims-v2"
CHANGE_ALGORITHM_VERSION = "normalized-diff-v2"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DATE = r"(?P<date>20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)"
_TIME = r"(?P<start>\d{1,2}:\d{2})\s*[–—~～至-]\s*(?P<end>\d{1,2}:\d{2})"


@dataclass(frozen=True, slots=True)
class ExtractedClaim:
    key: str
    claim_type: str
    value: dict[str, Any]
    normalized: dict[str, Any]
    audience: dict[str, Any]
    confidence: float
    start: int
    end: int
    review_state: str


def extract_claims(content: str) -> list[ExtractedClaim]:
    scan_content, source_offsets = _nfkc_with_source_offsets(content)
    claims: list[ExtractedClaim] = []
    audience_rule: dict[str, Any] = {}
    audience_match = re.search(r"(?:适用于|面向)\s*([^;。\n]+)", scan_content)
    if audience_match:
        raw = _source_group(content, audience_match, source_offsets, 1).strip()
        normalized_raw = audience_match.group(1).strip()
        grade = re.search(r"(20\d{2})\s*级", normalized_raw)
        major = re.search(r"(?:20\d{2}\s*级)?\s*([^,、;。]+?)\s*专业", normalized_raw)
        course = re.search(r"(?:课程|科目)\s*[:：]?\s*([^,、;。]+)", normalized_raw)
        audience_rule = {
            key: value
            for key, value in {
                "grade": normalize_grade(grade.group(1)) if grade else None,
                "major": normalize_major(major.group(1)) if major else None,
                "course": normalize_course(course.group(1)) if course else None,
                "raw": raw,
            }.items()
            if value is not None
        }
        normalized_rule = {key: value for key, value in audience_rule.items() if key != "raw"}
        claims.append(
            _claim(
                "audience",
                "audience",
                {"text": raw},
                normalized_rule,
                audience_rule,
                audience_match,
                content=content,
                source_offsets=source_offsets,
            )
        )

    range_match = re.search(
        rf"(?:考试时间|活动时间|时间)\s*[：:]\s*(?:暂定|预计|约)?\s*{_DATE}\s*{_TIME}",
        scan_content,
    )
    if range_match:
        start_at = _parse_local(range_match.group("date"), range_match.group("start"))
        end_at = _parse_local(range_match.group("date"), range_match.group("end"))
        if end_at <= start_at:
            end_at += timedelta(days=1)
        raw_value = {
            "date": range_match.group("date"),
            "start": range_match.group("start"),
            "end": range_match.group("end"),
        }
        claims.extend(
            [
                _claim(
                    "event.start_at",
                    "datetime",
                    raw_value,
                    {"iso": start_at.isoformat()},
                    audience_rule,
                    range_match,
                    content=content,
                    source_offsets=source_offsets,
                ),
                _claim(
                    "event.end_at",
                    "datetime",
                    raw_value,
                    {"iso": end_at.isoformat()},
                    audience_rule,
                    range_match,
                    content=content,
                    source_offsets=source_offsets,
                ),
            ]
        )

    deadline_match = re.search(
        rf"(?:截止时间|提交截止|截止)\s*[：:]\s*{_DATE}(?:\s*(?P<deadline_time>\d{{1,2}}:\d{{2}}))?",
        scan_content,
    )
    if deadline_match:
        deadline = _parse_local(
            deadline_match.group("date"), deadline_match.group("deadline_time") or "23:59"
        )
        claims.append(
            _claim(
                "task.due_at",
                "datetime",
                {"text": deadline_match.group(0)},
                {"iso": deadline.isoformat()},
                audience_rule,
                deadline_match,
                content=content,
                source_offsets=source_offsets,
            )
        )

    for key, claim_type, pattern in (
        (
            "event.location",
            "location",
            r"(?:考试地点|活动地点|地点)(?:改为)?\s*[:]?\s*([^;。\n]+)",
        ),
        ("required_materials", "materials", r"(?:要求携带|所需材料)\s*[:]?\s*([^;。\n]+)"),
        ("action_requirement", "requirement", r"(?:操作要求|要求)\s*[:]\s*([^;。\n]+)"),
    ):
        match = re.search(pattern, scan_content)
        if match:
            value = re.sub(r"\s+", " ", _source_group(content, match, source_offsets, 1)).strip()
            claims.append(
                _claim(
                    key,
                    claim_type,
                    {"text": value},
                    {"text": normalize_semantic_text(value)},
                    audience_rule,
                    match,
                    content=content,
                    source_offsets=source_offsets,
                )
            )

    reminder_match = re.search(r"提前\s*(\d+)\s*(分钟|小时|天)提醒", scan_content)
    if reminder_match:
        amount = int(reminder_match.group(1))
        multiplier = {"分钟": 1, "小时": 60, "天": 1440}[reminder_match.group(2)]
        claims.append(
            _claim(
                "reminder.minutes",
                "reminder",
                {"amount": amount, "unit": reminder_match.group(2)},
                {"minutes": amount * multiplier},
                audience_rule,
                reminder_match,
                content=content,
                source_offsets=source_offsets,
            )
        )
    return sorted(claims, key=lambda item: item.key)


def _claim(
    key: str,
    claim_type: str,
    value: dict[str, Any],
    normalized: dict[str, Any],
    audience: dict[str, Any],
    match: re.Match[str],
    *,
    content: str,
    source_offsets: list[int],
) -> ExtractedClaim:
    start, end = _source_span(match, source_offsets)
    evidence = content[start:end]
    uncertain = bool(re.search(r"可能|暂定|待定|预计|约", evidence))
    return ExtractedClaim(
        key=key,
        claim_type=claim_type,
        value=value,
        normalized=normalized,
        audience=audience,
        confidence=0.55 if uncertain else 0.98,
        start=start,
        end=end,
        review_state="pending" if uncertain else "approved",
    )


def _nfkc_with_source_offsets(value: str) -> tuple[str, list[int]]:
    """Normalize compatibility characters while retaining Python code-point offsets."""
    normalized: list[str] = []
    source_offsets: list[int] = []
    for index, character in enumerate(value):
        replacement = unicodedata.normalize("NFKC", character)
        normalized.append(replacement)
        source_offsets.extend([index] * len(replacement))
    return "".join(normalized), source_offsets


def _source_span(
    match: re.Match[str], source_offsets: list[int], group: int = 0
) -> tuple[int, int]:
    start, end = match.span(group)
    if start == end:
        original = source_offsets[start] if start < len(source_offsets) else len(source_offsets)
        return original, original
    return source_offsets[start], source_offsets[end - 1] + 1


def _source_group(
    content: str,
    match: re.Match[str],
    source_offsets: list[int],
    group: int,
) -> str:
    start, end = _source_span(match, source_offsets, group)
    return content[start:end]


def _parse_local(raw_date: str, raw_time: str) -> datetime:
    normalized = unicodedata.normalize("NFKC", raw_date)
    normalized = normalized.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-").replace(".", "-")
    year, month, day = (int(part) for part in normalized.split("-"))
    hour, minute = (int(part) for part in raw_time.split(":"))
    return datetime(year, month, day, hour, minute, tzinfo=_SHANGHAI)


def normalize_semantic_text(value: str) -> str:
    """Canonicalize cosmetic Unicode, whitespace, and punctuation differences."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if not unicodedata.category(character).startswith(("P", "Z")) and not character.isspace()
    )


def normalize_grade(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    match = re.search(r"20\d{2}", normalized)
    return match.group(0) if match else normalize_semantic_text(normalized.removesuffix("级"))


def normalize_major(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"^20\d{2}\s*级\s*", "", normalized)
    return normalize_semantic_text(normalized.removesuffix("专业"))


def normalize_course(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"^(?:课程|科目)\s*[:：]?\s*", "", normalized)
    return normalize_semantic_text(normalized)
