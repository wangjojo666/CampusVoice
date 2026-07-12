from app.models.entities import UserSettings
from app.schemas.correction import (
    CorrectionPolicy,
    CorrectionRequest,
    CorrectionTerm,
    HotwordSource,
)
from app.services.correction import CorrectionEngine
from app.services.correction.persistence import _merge_settings_context


def test_high_confidence_noncritical_term_is_auto_applied_and_recorded() -> None:
    request = CorrectionRequest(
        text="复习机气学习重点",
        asr_confidence=0.1,
        terms=[
            CorrectionTerm(
                term="机器学习",
                aliases=["机气学习"],
                source=HotwordSource.AI_TERM,
                context_keywords=["复习"],
            )
        ],
        document_terms=["机器学习"],
        recent_context=["机器学习复习资料"],
    )

    response = CorrectionEngine().correct(request)

    assert response.record.corrected_text == "复习机器学习重点"
    assert response.record.modifications[0].policy == CorrectionPolicy.AUTO_APPLY
    assert response.record.user_confirmed is False
    assert response.requires_user_input is False


def test_time_field_is_never_silently_modified() -> None:
    request = CorrectionRequest(
        text="明天九点考试",
        asr_confidence=0.1,
        terms=[
            CorrectionTerm(
                term="十点",
                aliases=["九点"],
                source=HotwordSource.USER,
                context_keywords=["考试"],
            )
        ],
        document_terms=["十点"],
        recent_context=["考试十点开始"],
    )

    response = CorrectionEngine().correct(request)

    modification = next(item for item in response.record.modifications if item.original == "九点")
    assert modification.critical_field is True
    assert modification.policy != CorrectionPolicy.AUTO_APPLY
    assert response.record.corrected_text == request.text
    assert response.requires_user_input is True


def test_course_term_requires_confirmation_even_with_high_score() -> None:
    request = CorrectionRequest(
        text="添加机气学习作业",
        asr_confidence=0.1,
        terms=[
            CorrectionTerm(
                term="机器学习",
                aliases=["机气学习"],
                source=HotwordSource.COURSE,
                context_keywords=["作业"],
            )
        ],
        current_courses=["机器学习"],
        document_terms=["机器学习"],
        recent_context=["机器学习作业"],
    )

    response = CorrectionEngine().correct(request)

    modification = next(
        item for item in response.record.modifications if item.original == "机气学习"
    )
    assert modification.critical_field is True
    assert modification.policy == CorrectionPolicy.SUGGEST
    assert response.record.corrected_text == request.text


def test_settings_courses_and_teachers_are_merged_into_correction_context() -> None:
    settings = UserSettings(
        user_id="user_demo",
        current_courses=[
            {"code": "AI301", "name": "机器学习", "teacher": "张老师"},
        ],
        teacher_names=["张老师", "李老师"],
        asr_model_config={},
    )

    merged = _merge_settings_context(
        CorrectionRequest(text="复习课程", asr_confidence=0.8),
        settings,
    )

    assert merged.current_courses == ["机器学习", "AI301"]
    assert {(term.term, term.source) for term in merged.terms} == {
        ("机器学习", HotwordSource.COURSE),
        ("AI301", HotwordSource.COURSE_CODE),
        ("张老师", HotwordSource.TEACHER),
        ("李老师", HotwordSource.TEACHER),
    }
