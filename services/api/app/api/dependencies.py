import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.metrics import InMemoryMetrics
from app.models.entities import User, UserSettings
from app.security.authentication import Authenticator, AuthPrincipal
from app.security.write_challenges import consume_write_challenge
from app.services.errors import DomainError


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


SessionDependency = Annotated[AsyncSession, Depends(get_session)]


def get_runtime_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]


def get_metrics(request: Request) -> InMemoryMetrics:
    metrics: InMemoryMetrics = request.app.state.metrics
    return metrics


MetricsDependency = Annotated[InMemoryMetrics, Depends(get_metrics)]


_BEARER = HTTPBearer(auto_error=False)


def get_authenticator(request: Request) -> Authenticator:
    authenticator: Authenticator = request.app.state.authenticator
    return authenticator


async def current_principal(
    request: Request,
    authenticator: Annotated[Authenticator, Depends(get_authenticator)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_BEARER)],
) -> AuthPrincipal:
    token = credentials.credentials if credentials is not None else None
    principal = await authenticator.authenticate(token)
    request.state.principal = principal
    return principal


AuthPrincipalDependency = Annotated[AuthPrincipal, Depends(current_principal)]


async def current_user_id(
    principal: AuthPrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
) -> str:
    """Provision and return the authenticated, server-derived user identity.

    External OIDC subjects are never accepted as request parameters and are mapped
    to bounded internal identifiers by the authenticator.
    """
    await provision_principal(session, principal, settings)
    return principal.user_id


async def provision_principal(
    session: AsyncSession,
    principal: AuthPrincipal,
    settings: Settings,
) -> None:
    for attempt in range(2):
        user = await session.get(User, principal.user_id)
        if user is not None and not user.is_active:
            await session.rollback()
            raise DomainError(
                "user_inactive", "The authenticated user is inactive", status_code=403
            )
        user_settings = await session.get(UserSettings, principal.user_id)
        if user is not None and user_settings is not None:
            # Close the dependency's read-only autobegin transaction so route
            # services can open their explicit transaction scopes.
            await session.rollback()
            return
        if user is None:
            session.add(
                User(
                    id=principal.user_id,
                    display_name=principal.display_name,
                )
            )
        if user_settings is None:
            session.add(_default_user_settings(principal.user_id, settings))
        try:
            await session.commit()
            return
        except IntegrityError:
            await session.rollback()
            # A concurrent first request may have provisioned the same OIDC
            # identity. Re-read both rows and retry any missing settings once.
            if attempt == 1:
                raise


def _default_user_settings(user_id: str, settings: Settings) -> UserSettings:
    return UserSettings(
        user_id=user_id,
        timezone=settings.timezone,
        asr_model_config={
            "provider": settings.asr_provider,
            "model": settings.asr_model,
            "device": settings.asr_device,
        },
    )


UserIdDependency = Annotated[str, Depends(current_user_id)]


async def require_write_challenge(
    request: Request,
    session: SessionDependency,
    user_id: UserIdDependency,
    settings: SettingsDependency,
    challenge: Annotated[str | None, Header(alias="X-Write-Challenge")] = None,
) -> None:
    if not challenge:
        raise DomainError(
            "write_challenge_required",
            "This write requires a server-issued one-time challenge",
            status_code=428,
        )
    raw_body = await request.body()
    try:
        body = json.loads(raw_body) if raw_body else None
    except json.JSONDecodeError as exc:
        raise DomainError(
            "invalid_write_challenge_body",
            "The request body must be valid JSON",
            status_code=422,
        ) from exc
    await consume_write_challenge(
        session,
        user_id=user_id,
        challenge=challenge,
        method=request.method,
        path=request.url.path,
        body=body,
        api_prefix=settings.api_prefix,
    )


WriteChallengeDependency = Annotated[None, Depends(require_write_challenge)]
