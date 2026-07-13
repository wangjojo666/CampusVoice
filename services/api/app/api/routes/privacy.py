from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import UserIdDependency
from app.schemas.privacy import (
    PrivacyDeletionChallengeResponse,
    PrivacyDeletionConfirmRequest,
    PrivacyDeletionResult,
    PrivacyExportResponse,
    RetentionRunResponse,
)
from app.services.privacy import PrivacyService

router = APIRouter(prefix="/privacy", tags=["privacy"])


def get_privacy_service(request: Request) -> PrivacyService:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return PrivacyService(factory, request.app.state.settings)


PrivacyServiceDependency = Annotated[PrivacyService, Depends(get_privacy_service)]


def _prevent_sensitive_response_caching(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"


@router.get("/export", response_model=PrivacyExportResponse)
async def export_user_data(
    response: Response,
    user_id: UserIdDependency,
    service: PrivacyServiceDependency,
) -> PrivacyExportResponse:
    _prevent_sensitive_response_caching(response)
    response.headers["Content-Disposition"] = 'attachment; filename="campusvoice-data-export.json"'
    return await service.export_user_data(user_id)


@router.post("/retention/run", response_model=RetentionRunResponse)
async def run_retention(
    response: Response,
    user_id: UserIdDependency,
    service: PrivacyServiceDependency,
) -> RetentionRunResponse:
    _prevent_sensitive_response_caching(response)
    return await service.run_retention(user_id)


@router.post(
    "/deletion-challenges",
    response_model=PrivacyDeletionChallengeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_deletion_challenge(
    response: Response,
    user_id: UserIdDependency,
    service: PrivacyServiceDependency,
) -> PrivacyDeletionChallengeResponse:
    _prevent_sensitive_response_caching(response)
    return await service.issue_deletion_challenge(user_id)


@router.post(
    "/deletion-challenges/{challenge_id}/confirm",
    response_model=PrivacyDeletionResult,
)
async def confirm_deletion(
    challenge_id: str,
    body: PrivacyDeletionConfirmRequest,
    response: Response,
    user_id: UserIdDependency,
    service: PrivacyServiceDependency,
) -> PrivacyDeletionResult:
    _prevent_sensitive_response_caching(response)
    return await service.clear_user_data(
        user_id,
        challenge_id,
        body.challenge,
        body.scope,
    )
