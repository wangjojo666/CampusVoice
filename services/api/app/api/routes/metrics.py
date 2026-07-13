from fastapi import APIRouter, Request

from app.api.dependencies import UserIdDependency
from app.core.metrics import InMemoryMetrics
from app.schemas.metrics import MetricsResponse

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_model=MetricsResponse)
async def metrics(request: Request, _user_id: UserIdDependency) -> MetricsResponse:
    registry: InMemoryMetrics = request.app.state.metrics
    return MetricsResponse.model_validate(registry.snapshot())
