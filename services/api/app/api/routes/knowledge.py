from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.routes.documents import get_knowledge_service
from app.schemas.knowledge import (
    KnowledgeAskRequest,
    KnowledgeAskResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.services.knowledge import KnowledgeService
from app.services.knowledge.retrieval import RetrievalModelError

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    request: KnowledgeSearchRequest,
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeSearchResponse:
    try:
        return await service.search(
            request.query,
            top_k=request.top_k,
            min_similarity=request.min_similarity,
            version=request.version,
            applicable_group=request.applicable_group,
        )
    except RetrievalModelError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "embedding_model_unavailable", "message": str(exc)},
        ) from exc


@router.post("/ask", response_model=KnowledgeAskResponse)
async def ask_knowledge(
    request: KnowledgeAskRequest,
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> KnowledgeAskResponse:
    try:
        return await service.ask(
            request.question,
            top_k=request.top_k,
            min_similarity=request.min_similarity,
            version=request.version,
            applicable_group=request.applicable_group,
        )
    except RetrievalModelError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "embedding_model_unavailable", "message": str(exc)},
        ) from exc
