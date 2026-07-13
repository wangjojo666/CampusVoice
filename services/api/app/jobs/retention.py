import asyncio
import json

from app.core.config import get_settings
from app.db.session import create_database_engine, create_session_factory
from app.services.privacy.scheduler import RetentionExecutor, retention_summary


async def _run() -> None:
    settings = get_settings()
    engine = create_database_engine(settings.database_url)
    try:
        executor = RetentionExecutor(create_session_factory(engine), settings)
        results = await executor.run_with_retries()
        print(json.dumps(retention_summary(results), sort_keys=True))
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
