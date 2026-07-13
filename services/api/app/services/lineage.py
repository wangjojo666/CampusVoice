from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Document, DocumentChunk, NoticeClaim
from app.services.errors import DomainError


async def validate_notice_lineage(
    session: AsyncSession,
    user_id: str,
    *,
    document_id: str | None,
    chunk_id: str | None,
    claim_id: str | None,
) -> None:
    if chunk_id is None and claim_id is None and document_id is None:
        return
    if document_id is None:
        raise DomainError(
            "invalid_source_lineage",
            "source_document_id is required when a source chunk or claim is supplied",
            status_code=422,
        )
    document = await session.scalar(
        select(Document.id).where(Document.id == document_id, Document.user_id == user_id)
    )
    if document is None:
        raise DomainError(
            "invalid_source_lineage", "The source document is not available", status_code=422
        )
    if chunk_id is not None:
        chunk = await session.scalar(
            select(DocumentChunk.id).where(
                DocumentChunk.id == chunk_id,
                DocumentChunk.document_id == document_id,
            )
        )
        if chunk is None:
            raise DomainError(
                "invalid_source_lineage",
                "The source chunk does not belong to the selected document",
                status_code=422,
            )
    if claim_id is not None:
        claim = await session.scalar(
            select(NoticeClaim.id).where(
                NoticeClaim.id == claim_id,
                NoticeClaim.user_id == user_id,
                NoticeClaim.document_id == document_id,
                *([NoticeClaim.chunk_id == chunk_id] if chunk_id is not None else []),
            )
        )
        if claim is None:
            raise DomainError(
                "invalid_source_lineage",
                "The source claim does not belong to the selected document and chunk",
                status_code=422,
            )
