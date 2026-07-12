from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import UserIdDependency
from app.schemas.correction import (
    CorrectionDecisionRequest,
    CorrectionDecisionResponse,
    CorrectionRequest,
    CorrectionResponse,
)
from app.services.correction import CorrectionEngine
from app.services.correction.persistence import CorrectionService

router = APIRouter(prefix="/correction", tags=["correction"])


@lru_cache
def get_correction_engine() -> CorrectionEngine:
    return CorrectionEngine()


@router.post("/preview", response_model=CorrectionResponse)
async def preview_correction(
    request: CorrectionRequest,
    http_request: Request,
    user_id: UserIdDependency,
    engine: Annotated[CorrectionEngine, Depends(get_correction_engine)],
) -> CorrectionResponse:
    factory: async_sessionmaker[AsyncSession] = http_request.app.state.session_factory
    return await CorrectionService(factory, engine).preview(user_id, request)


@router.post("/{record_id}/decision", response_model=CorrectionDecisionResponse)
async def decide_correction(
    record_id: str,
    body: CorrectionDecisionRequest,
    request: Request,
    user_id: UserIdDependency,
    engine: Annotated[CorrectionEngine, Depends(get_correction_engine)],
) -> CorrectionDecisionResponse:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return await CorrectionService(factory, engine).decide(user_id, record_id, body)
