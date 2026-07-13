from fastapi import APIRouter, Request, Response, status

from app.api.dependencies import SettingsDependency
from app.schemas.health import LivenessResponse, ReadinessResponse
from app.services.health import readiness_report

router = APIRouter(tags=["health"])
root_router = APIRouter(prefix="/health", tags=["health"])


@router.get("/health/live", response_model=LivenessResponse)
@root_router.get("/live", response_model=LivenessResponse)
@root_router.get("", response_model=LivenessResponse, include_in_schema=False)
async def live(settings: SettingsDependency) -> LivenessResponse:
    return LivenessResponse(
        service=settings.app_name,
        version=settings.app_version,
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
@router.get(
    "/health",
    response_model=ReadinessResponse,
    include_in_schema=False,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
@root_router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def ready(
    request: Request,
    response: Response,
    settings: SettingsDependency,
) -> ReadinessResponse:
    report = await readiness_report(
        request.app.state.database_engine,
        settings,
        request.app.state.asr_connections,
    )
    if report.status == "error":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report
