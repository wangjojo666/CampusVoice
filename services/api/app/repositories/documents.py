from __future__ import annotations

from builtins import list as builtin_list

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Document, DocumentChunk


class DocumentRepository:
    """Persistence hooks used by the knowledge module.

    Text extraction and embedding live outside this repository; this class only
    owns durable document/chunk rows.
    """

    async def get(self, session: AsyncSession, user_id: str, document_id: str) -> Document | None:
        document: Document | None = await session.scalar(
            select(Document).where(Document.id == document_id, Document.user_id == user_id)
        )
        return document

    async def list(self, session: AsyncSession, user_id: str) -> list[Document]:
        return list(
            await session.scalars(
                select(Document)
                .where(Document.user_id == user_id)
                .order_by(Document.publish_date.desc().nullslast(), Document.created_at.desc())
            )
        )

    async def chunks(
        self, session: AsyncSession, user_id: str, document_id: str
    ) -> builtin_list[DocumentChunk]:
        return list(
            await session.scalars(
                select(DocumentChunk)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(Document.user_id == user_id, DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.ordinal)
            )
        )
