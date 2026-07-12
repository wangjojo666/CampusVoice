from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.router import api_router
from app.api.routes import asr
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import create_database_engine, create_session_factory
from app.models.entities import User, UserSettings
from app.services.errors import DomainError


def create_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
    initialize_schema: bool | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    owns_engine = engine is None
    database_engine = engine or create_database_engine(settings.database_url)
    session_factory = create_session_factory(database_engine)
    should_initialize = (
        settings.database_auto_create if initialize_schema is None else initialize_schema
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if should_initialize:
            async with database_engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session, session.begin():
            user = await session.get(User, settings.single_user_id)
            if user is None:
                session.add(
                    User(
                        id=settings.single_user_id,
                        display_name="CampusVoice Demo User",
                    )
                )
                session.add(
                    UserSettings(
                        user_id=settings.single_user_id,
                        timezone=settings.timezone,
                        asr_model_config={
                            "provider": settings.asr_provider,
                            "model": settings.asr_model,
                            "device": settings.asr_device,
                        },
                    )
                )
        yield
        if owns_engine:
            await database_engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    app.state.session_factory = session_factory
    app.state.database_engine = database_engine
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix=settings.api_prefix)
    app.include_router(asr.router)

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.get("/health", include_in_schema=False)
    async def root_health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
