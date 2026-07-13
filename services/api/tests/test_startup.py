import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.startup import validate_runtime_capabilities


def test_core_defaults_are_dependency_safe_and_partial_llm_config_is_rejected() -> None:
    assert Settings(env="test").knowledge_retriever == "lexical"
    with pytest.raises(ValidationError, match="LLM base URL and model"):
        Settings(env="test", llm_base_url="https://llm.example/v1")


def test_disabled_ai_components_need_no_optional_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.startup.find_spec", lambda _name: None)

    validate_runtime_capabilities(
        Settings(env="test", asr_provider="disabled", knowledge_retriever="lexical")
    )


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("funasr", "FunASR"), ("whisper", "Whisper")],
)
def test_enabled_asr_fails_fast_when_optional_modules_are_missing(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    expected: str,
) -> None:
    monkeypatch.setattr("app.core.startup.find_spec", lambda _name: None)

    with pytest.raises(ValueError, match=expected):
        validate_runtime_capabilities(
            Settings(env="test", asr_provider=provider, asr_model="small")  # type: ignore[arg-type]
        )


def test_whisper_rejects_a_paraformer_model_before_serving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.startup.find_spec", lambda _name: object())

    with pytest.raises(ValueError, match="Whisper model name"):
        validate_runtime_capabilities(
            Settings(env="test", asr_provider="whisper", asr_model="paraformer-zh-streaming")
        )


def test_embedding_retrieval_fails_fast_without_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.core.startup.find_spec", lambda _name: None)

    with pytest.raises(ValueError, match="sentence-transformers"):
        validate_runtime_capabilities(
            Settings(env="test", asr_provider="disabled", knowledge_retriever="embedding")
        )
