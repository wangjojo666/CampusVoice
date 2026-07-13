from importlib.util import find_spec

from app.core.config import Settings


def _missing_modules(names: tuple[str, ...]) -> list[str]:
    return [name for name in names if find_spec(name) is None]


def validate_runtime_capabilities(settings: Settings) -> None:
    """Fail before serving requests when an enabled optional provider cannot run."""
    if settings.asr_provider == "funasr":
        missing = _missing_modules(("funasr", "torch"))
        if missing:
            raise ValueError(
                "FunASR is enabled but optional dependencies are missing: " + ", ".join(missing)
            )
    elif settings.asr_provider == "whisper":
        missing = _missing_modules(("whisper", "torch"))
        if missing:
            raise ValueError(
                "Whisper is enabled but optional dependencies are missing: " + ", ".join(missing)
            )
        if "paraformer" in settings.asr_model.lower():
            raise ValueError("Whisper requires a Whisper model name instead of a Paraformer model")
    if settings.knowledge_retriever == "embedding":
        missing = _missing_modules(("sentence_transformers",))
        if missing:
            raise ValueError(
                "Embedding retrieval is enabled but sentence-transformers is not installed"
            )
