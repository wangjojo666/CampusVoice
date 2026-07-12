import re
from difflib import SequenceMatcher

from app.schemas.correction import (
    CandidateScore,
    CorrectionCandidate,
    CorrectionModification,
    CorrectionPolicy,
    CorrectionRecord,
    CorrectionRequest,
    CorrectionResponse,
    CorrectionTerm,
    CriticalSpan,
    HotwordSource,
)


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, left.lower(), right.lower()).ratio()


def _overlap(start: int, end: int, span: CriticalSpan) -> bool:
    return start < span.end and end > span.start


def _automatic_critical_spans(text: str) -> list[CriticalSpan]:
    patterns = {
        "date": r"(?:20\d{2}[年\-/]\d{1,2}[月\-/]\d{1,2}日?|\d{1,2}月\d{1,2}[日号]|今天|明天|后天)",
        "time": (
            r"(?:凌晨|早上|上午|中午|下午|晚上)?"
            r"(?:\d{1,2}|[一二两三四五六七八九十]{1,3})"
            r"(?:[:点时]\d{0,2}分?)"
        ),
    }
    spans: list[CriticalSpan] = []
    for kind, pattern in patterns.items():
        spans.extend(
            CriticalSpan(start=match.start(), end=match.end(), kind=kind)
            for match in re.finditer(pattern, text)
        )
    delete_match = re.search(r"(?:删除|删掉|移除|取消)(?P<target>.+)$", text)
    if delete_match:
        spans.append(
            CriticalSpan(
                start=delete_match.start("target"),
                end=delete_match.end("target"),
                kind="delete_target",
            )
        )
    return spans


def _candidate_windows(text: str, term: CorrectionTerm) -> list[tuple[int, int, str, float]]:
    aliases = [alias for alias in term.aliases if alias and alias != term.term]
    windows: dict[tuple[int, int], tuple[str, float]] = {}
    for alias in aliases:
        for match in re.finditer(re.escape(alias), text, flags=re.IGNORECASE):
            windows[(match.start(), match.end())] = (match.group(), 1.0)

    target_length = len(term.term)
    min_length = max(1, target_length - 2)
    max_length = min(len(text), target_length + 2)
    for length in range(min_length, max_length + 1):
        for start in range(0, len(text) - length + 1):
            end = start + length
            original = text[start:end]
            if original == term.term or original.isspace():
                continue
            score = _similarity(original, term.term)
            if score >= 0.5:
                previous = windows.get((start, end))
                if previous is None or score > previous[1]:
                    windows[(start, end)] = (original, score)
    ranked = [(start, end, value[0], value[1]) for (start, end), value in windows.items()]
    ranked.sort(key=lambda item: (-item[3], item[0], item[1]))
    return ranked[:5]


class CorrectionEngine:
    high_threshold = 0.80
    medium_threshold = 0.60

    def correct(self, request: CorrectionRequest) -> CorrectionResponse:
        critical_spans = [*request.critical_spans, *_automatic_critical_spans(request.text)]
        candidates: list[CorrectionCandidate] = []
        context_text = " ".join(request.recent_context).lower()
        course_set = {item.lower() for item in request.current_courses}
        document_set = {item.lower() for item in request.document_terms}

        for term in request.terms:
            for start, end, original, edit_similarity in _candidate_windows(request.text, term):
                alias_similarity = max(
                    (_similarity(original, alias) for alias in term.aliases),
                    default=edit_similarity,
                )
                course_relevance = float(
                    term.term.lower() in course_set
                    or term.source in {HotwordSource.COURSE, HotwordSource.COURSE_CODE}
                )
                document_relevance = float(
                    term.term.lower() in document_set or term.source == HotwordSource.DOCUMENT
                )
                recent_relevance = float(
                    term.term.lower() in context_text
                    or any(keyword.lower() in context_text for keyword in term.context_keywords)
                )
                local_context = request.text[max(0, start - 12) : min(len(request.text), end + 12)]
                semantic_relevance = float(
                    any(keyword in local_context for keyword in term.context_keywords)
                )
                asr_uncertainty = 1.0 - request.asr_confidence
                total = (
                    0.45 * edit_similarity
                    + 0.20 * alias_similarity
                    + 0.08 * asr_uncertainty
                    + 0.08 * course_relevance
                    + 0.06 * document_relevance
                    + 0.07 * recent_relevance
                    + 0.06 * semantic_relevance
                )
                total = round(min(1.0, max(0.0, total)), 6)

                matched_critical = next(
                    (span for span in critical_spans if _overlap(start, end, span)),
                    None,
                )
                if course_relevance:
                    matched_critical = matched_critical or CriticalSpan(
                        start=start,
                        end=end,
                        kind="course",
                    )
                if total >= self.high_threshold and matched_critical is None:
                    policy = CorrectionPolicy.AUTO_APPLY
                elif total >= self.medium_threshold:
                    policy = CorrectionPolicy.SUGGEST
                else:
                    policy = CorrectionPolicy.CLARIFY
                reason = self._reason(term, policy, matched_critical)
                candidates.append(
                    CorrectionCandidate(
                        start=start,
                        end=end,
                        original=original,
                        replacement=term.term,
                        source=term.source,
                        score=CandidateScore(
                            edit_similarity=edit_similarity,
                            pronunciation_similarity=alias_similarity,
                            asr_uncertainty=asr_uncertainty,
                            course_relevance=course_relevance,
                            document_relevance=document_relevance,
                            recent_context_relevance=recent_relevance,
                            semantic_relevance=semantic_relevance,
                            total=total,
                        ),
                        critical_field=matched_critical is not None,
                        critical_kind=matched_critical.kind if matched_critical else None,
                        policy=policy,
                        reason=reason,
                    )
                )

        candidates.sort(
            key=lambda item: (-item.score.total, item.start, item.end, item.replacement)
        )
        selected: list[CorrectionCandidate] = []
        for candidate in candidates:
            if any(
                candidate.start < existing.end and candidate.end > existing.start
                for existing in selected
            ):
                continue
            selected.append(candidate)
        selected.sort(key=lambda item: item.start)

        corrected = request.text
        auto_candidates = [
            candidate for candidate in selected if candidate.policy == CorrectionPolicy.AUTO_APPLY
        ]
        for candidate in sorted(auto_candidates, key=lambda item: item.start, reverse=True):
            corrected = (
                corrected[: candidate.start] + candidate.replacement + corrected[candidate.end :]
            )
        modifications = [
            CorrectionModification(
                start=candidate.start,
                end=candidate.end,
                original=candidate.original,
                replacement=candidate.replacement,
                policy=candidate.policy,
                confidence=candidate.score.total,
                reason=candidate.reason,
                critical_field=candidate.critical_field,
            )
            for candidate in selected
        ]
        requires_input = any(
            candidate.policy in {CorrectionPolicy.SUGGEST, CorrectionPolicy.CLARIFY}
            for candidate in selected
        )
        record = CorrectionRecord(
            original_text=request.text,
            corrected_text=corrected,
            modifications=modifications,
            candidates=candidates,
            reason=self._summary(selected),
            confidence=max((item.score.total for item in selected), default=0.0),
            user_confirmed=False,
        )
        return CorrectionResponse(record=record, requires_user_input=requires_input)

    @staticmethod
    def _reason(
        term: CorrectionTerm,
        policy: CorrectionPolicy,
        critical_span: CriticalSpan | None,
    ) -> str:
        if critical_span:
            return (
                f"候选词来自{term.source.value}，但涉及关键字段 "
                f"{critical_span.kind}，禁止静默修改。"
            )
        if policy == CorrectionPolicy.AUTO_APPLY:
            return f"候选词来自{term.source.value}，综合相似度和上下文置信度较高。"
        if policy == CorrectionPolicy.SUGGEST:
            return f"候选词来自{term.source.value}，需要用户从候选中确认。"
        return f"候选词来自{term.source.value}，证据不足，需要保留原文并追问。"

    @staticmethod
    def _summary(selected: list[CorrectionCandidate]) -> str:
        if not selected:
            return "未发现达到候选阈值的校园术语修改。"
        auto_count = sum(item.policy == CorrectionPolicy.AUTO_APPLY for item in selected)
        review_count = len(selected) - auto_count
        return (
            f"发现 {len(selected)} 处候选：自动标记 {auto_count} 处，待用户确认 {review_count} 处。"
        )
