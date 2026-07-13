from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from secrets import token_bytes, token_urlsafe

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.router import api_router
from app.api.routes import asr, health
from app.core.config import Settings, get_settings
from app.core.metrics import InMemoryMetrics
from app.core.observability import RequestObservabilityMiddleware, request_id_from
from app.core.startup import validate_runtime_capabilities
from app.db.base import Base
from app.db.session import create_database_engine, create_session_factory
from app.models.entities import User, UserSettings
from app.security.authentication import build_authenticator
from app.security.oidc import OidcClient
from app.services.asr.connections import build_asr_quota_registry
from app.services.errors import DomainError


def create_app(
    settings: Settings | None = None,
    *,
    engine: AsyncEngine | None = None,
    initialize_schema: bool | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    validate_runtime_capabilities(settings)
    if settings.confirmation_secret is None or not settings.confirmation_secret.get_secret_value():
        settings = settings.model_copy(update={"confirmation_secret": SecretStr(token_urlsafe(48))})
    owns_engine = engine is None
    database_engine = engine or create_database_engine(settings.database_url)
    session_factory = create_session_factory(database_engine)
    should_initialize = (
        settings.database_auto_create if initialize_schema is None else initialize_schema
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await application.state.asr_connections.start()
        if should_initialize:
            async with database_engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        if settings.auth_mode == "demo":
            async with session_factory() as session, session.begin():
                user = await session.get(User, settings.demo_user_id)
                if user is None:
                    session.add(
                        User(
                            id=settings.demo_user_id,
                            display_name="CampusVoice Demo User",
                        )
                    )
                    session.add(
                        UserSettings(
                            user_id=settings.demo_user_id,
                            timezone=settings.timezone,
                            asr_model_config={
                                "provider": settings.asr_provider,
                                "model": settings.asr_model,
                                "device": settings.asr_device,
                            },
                        )
                    )
        try:
            yield
        finally:
            await application.state.asr_connections.close()
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
    app.state.authenticator = build_authenticator(settings)
    app.state.oidc_client = OidcClient(settings) if settings.auth_mode == "oidc" else None
    app.state.asr_connections = build_asr_quota_registry(settings)
    metrics = InMemoryMetrics()
    app.state.metrics = metrics
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # The web client intentionally sends credentials in every auth mode.
        # Exact configured origins above keep this safe for local JWT/demo and OIDC alike.
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(
        RequestObservabilityMiddleware,
        metrics=metrics,
        pseudonym_salt=token_bytes(32),
        log_level=settings.log_level,
    )
    app.include_router(api_router, prefix=settings.api_prefix)
    app.include_router(asr.router)
    app.include_router(health.root_router)

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        request_id = request_id_from(request)
        headers = {"X-Request-ID": request_id}
        if exc.status_code == 401:
            headers["WWW-Authenticate"] = "Bearer" if settings.auth_mode == "jwt" else "Session"
        return JSONResponse(
            status_code=exc.status_code,
            headers=headers,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
                "request_id": request_id,
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, _exc: Exception) -> JSONResponse:
        request_id = request_id_from(request)
        return JSONResponse(
            status_code=500,
            headers={"X-Request-ID": request_id},
            content={
                "error": {
                    "code": "internal_error",
                    "message": "The service could not complete the request",
                    "details": {},
                },
                "request_id": request_id,
            },
        )

    return app


app = create_app()
