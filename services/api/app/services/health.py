from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.schemas.health import HealthCheck, ReadinessResponse

_API_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def expected_alembic_heads() -> tuple[str, ...]:
    config = Config(str(_API_ROOT / "alembic.ini"))
    return tuple(sorted(ScriptDirectory.from_config(config).get_heads()))


def _current_alembic_heads(connection: Connection) -> tuple[str, ...]:
    return tuple(sorted(MigrationContext.configure(connection).get_current_heads()))


def _module_available(module_name: str) -> bool:
    try:
        return find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def _configured_component_checks(settings: Settings) -> dict[str, HealthCheck]:
    if settings.asr_provider == "disabled":
        asr = HealthCheck(status="disabled", message="ASR is intentionally disabled")
    else:
        module_name = "funasr" if settings.asr_provider == "funasr" else "whisper"
        available = _module_available(module_name)
        asr = HealthCheck(
            status="ok" if available else "error",
            message="ASR dependency is available" if available else "ASR dependency is unavailable",
        )

    if settings.knowledge_retriever == "lexical":
        retriever = HealthCheck(status="ok", message="Lexical retrieval is configured")
    else:
        available = _module_available("sentence_transformers")
        retriever = HealthCheck(
            status="ok" if available else "error",
            message=(
                "Embedding dependency is available"
                if available
                else "Embedding dependency is unavailable"
            ),
        )

    llm_configured = bool(settings.llm_base_url and settings.llm_model)
    llm = HealthCheck(
        status="ok" if llm_configured else "disabled",
        message=(
            "LLM endpoint is configured; network is not probed"
            if llm_configured
            else "LLM integration is intentionally disabled"
        ),
    )
    return {"asr": asr, "retriever": retriever, "llm": llm}


async def readiness_report(engine: AsyncEngine, settings: Settings) -> ReadinessResponse:
    checks = _configured_component_checks(settings)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            current_heads = await connection.run_sync(_current_alembic_heads)
        checks["database"] = HealthCheck(status="ok", message="Database connection succeeded")
    except Exception:
        checks["database"] = HealthCheck(status="error", message="Database connection failed")
        checks["migrations"] = HealthCheck(
            status="error",
            message="Database migration revision could not be checked",
        )
    else:
        try:
            expected_heads = expected_alembic_heads()
        except Exception:
            checks["migrations"] = HealthCheck(
                status="error",
                message="Application migration head could not be read",
            )
        else:
            migrations_current = current_heads == expected_heads and bool(expected_heads)
            checks["migrations"] = HealthCheck(
                status="ok" if migrations_current else "error",
                message=(
                    "Database migration revision matches the application head"
                    if migrations_current
                    else "Database migration revision does not match the application head"
                ),
            )

    status = "error" if any(check.status == "error" for check in checks.values()) else "ok"
    return ReadinessResponse(
        status=status,
        service=settings.app_name,
        version=settings.app_version,
        checks=checks,
    )
