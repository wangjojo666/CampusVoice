from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import HttpUrl, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.dependencies import UserIdDependency
from app.schemas.knowledge import DocumentFileType, DocumentMetadata, DocumentRecord
from app.services.knowledge import (
    DocumentParseError,
    DuplicateDocumentError,
    KnowledgePersistenceError,
    KnowledgeService,
    OpenAICompatibleKnowledgeAnswerer,
    SqlAlchemyKnowledgeRepository,
)
from app.services.knowledge.retrieval import (
    LexicalRetriever,
    RetrievalModelError,
    SentenceTransformerRetriever,
)

router = APIRouter(prefix="/documents", tags=["documents"])
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def get_knowledge_service(request: Request, user_id: UserIdDependency) -> KnowledgeService:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    settings = request.app.state.settings
    retriever = (
        LexicalRetriever()
        if settings.env == "test" or settings.knowledge_retriever == "lexical"
        else SentenceTransformerRetriever(
            settings.embedding_model,
            device=settings.embedding_device,
        )
    )
    answerer = (
        OpenAICompatibleKnowledgeAnswerer(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        )
        if settings.llm_base_url and settings.llm_model
        else None
    )
    return KnowledgeService(
        SqlAlchemyKnowledgeRepository(factory, user_id),
        retriever=retriever,
        answerer=answerer,
    )


@router.post("", response_model=DocumentRecord, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
    user_id: UserIdDependency,
    department: Annotated[str | None, Form()] = None,
    publish_date: Annotated[date | None, Form()] = None,
    applicable_group: Annotated[str | None, Form()] = None,
    source_url: Annotated[HttpUrl | None, Form()] = None,
    version: Annotated[str | None, Form()] = None,
) -> DocumentRecord:
    suffix = (file.filename or "").rsplit(".", maxsplit=1)[-1].lower()
    suffix = "md" if suffix == "markdown" else suffix
    try:
        file_type = DocumentFileType(suffix)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "unsupported_file_type",
                "message": "仅支持 PDF、DOCX、TXT 和 Markdown。",
            },
        ) from exc
    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "document_too_large", "message": "文档不能超过 20 MB。"},
        )
    try:
        metadata = DocumentMetadata(
            title=title,
            department=department,
            publish_date=publish_date,
            applicable_group=applicable_group,
            source_url=source_url,
            version=version,
            file_type=file_type,
        )
        return await service.ingest(
            user_id=user_id,
            metadata=metadata,
            content=content,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_document_metadata", "message": "文档元数据格式无效。"},
        ) from exc
    except DocumentParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except DuplicateDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "duplicate_document",
                "message": "相同内容的文档已经存在，未重复导入。",
            },
        ) from exc
    except KnowledgePersistenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "document_persistence_failed",
                "message": "文档保存后的数据库验证失败，请稍后重试。",
            },
        ) from exc
    except RetrievalModelError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "embedding_model_unavailable", "message": str(exc)},
        ) from exc


@router.get("", response_model=list[DocumentRecord])
async def list_documents(
    service: Annotated[KnowledgeService, Depends(get_knowledge_service)],
) -> list[DocumentRecord]:
    return await service.list_documents()
