from collections.abc import Sequence

from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.entities import Document
from app.models.entities import DocumentChunk as DocumentChunkEntity
from app.models.enums import DocumentStatus
from app.repositories.documents import DocumentRepository
from app.schemas.knowledge import (
    DocumentChunk,
    DocumentFileType,
    DocumentMetadata,
    DocumentRecord,
)


class DuplicateDocumentError(RuntimeError):
    pass


class KnowledgePersistenceError(RuntimeError):
    pass


class SqlAlchemyKnowledgeRepository:
    """Durable user-scoped adapter with transactional write and post-commit verification."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        user_id: str,
    ) -> None:
        self._session_factory = session_factory
        self._user_id = user_id
        self._documents = DocumentRepository()

    async def save(self, document: DocumentRecord, chunks: Sequence[DocumentChunk]) -> None:
        if document.user_id != self._user_id:
            raise KnowledgePersistenceError("document owner does not match repository boundary")
        entity = Document(
            id=document.id,
            user_id=self._user_id,
            title=document.metadata.title,
            department=document.metadata.department,
            publish_date=document.metadata.publish_date,
            applicable_group=document.metadata.applicable_group,
            source_url=(
                str(document.metadata.source_url) if document.metadata.source_url else None
            ),
            version=document.metadata.version,
            file_type=document.metadata.file_type.value,
            storage_path=f"database://document_chunks/{document.id}",
            content_sha256=document.content_sha256,
            status=DocumentStatus.READY,
        )
        chunk_entities = [
            DocumentChunkEntity(
                id=chunk.id,
                document_id=document.id,
                ordinal=chunk.ordinal,
                content=chunk.content,
                page_number=chunk.page_number,
                embedding=chunk.embedding,
                metadata_json={},
            )
            for chunk in chunks
        ]
        try:
            async with self._session_factory() as session, session.begin():
                session.add(entity)
                session.add_all(chunk_entities)
        except IntegrityError as exc:
            raise DuplicateDocumentError("document content already exists for this user") from exc

        # A new session proves the committed state rather than trusting ORM in-memory objects.
        async with self._session_factory() as verification_session:
            verified = await self._documents.get(
                verification_session,
                self._user_id,
                document.id,
            )
            verified_chunks = list(
                await verification_session.scalars(
                    select(DocumentChunkEntity)
                    .where(DocumentChunkEntity.document_id == document.id)
                    .order_by(DocumentChunkEntity.ordinal)
                )
            )
        if verified is None or verified.content_sha256 != document.content_sha256:
            raise KnowledgePersistenceError("document write verification failed")
        expected = [(chunk.id, chunk.ordinal, chunk.content, chunk.embedding) for chunk in chunks]
        actual = [
            (chunk.id, chunk.ordinal, chunk.content, chunk.embedding) for chunk in verified_chunks
        ]
        if actual != expected:
            raise KnowledgePersistenceError("document chunk write verification failed")

    async def list_documents(self) -> list[DocumentRecord]:
        async with self._session_factory() as session:
            entities = await self._documents.list(session, self._user_id)
            records: list[DocumentRecord] = []
            for entity in entities:
                chunks = await self._documents.chunks(session, self._user_id, entity.id)
                records.append(self._to_record(entity, len(chunks)))
            return records

    async def list_chunks(self) -> list[DocumentChunk]:
        async with self._session_factory() as session:
            entities = list(
                await session.scalars(
                    select(DocumentChunkEntity)
                    .join(Document, Document.id == DocumentChunkEntity.document_id)
                    .where(Document.user_id == self._user_id)
                    .order_by(DocumentChunkEntity.document_id, DocumentChunkEntity.ordinal)
                )
            )
        return [
            DocumentChunk(
                id=entity.id,
                document_id=entity.document_id,
                ordinal=entity.ordinal,
                content=entity.content,
                page_number=entity.page_number,
                embedding=entity.embedding,
            )
            for entity in entities
        ]

    def _to_record(self, entity: Document, chunk_count: int) -> DocumentRecord:
        try:
            file_type = DocumentFileType(entity.file_type)
        except ValueError as exc:
            raise KnowledgePersistenceError(
                f"unsupported stored document file type: {entity.file_type}"
            ) from exc
        return DocumentRecord(
            id=entity.id,
            user_id=entity.user_id,
            metadata=DocumentMetadata(
                title=entity.title,
                department=entity.department,
                publish_date=entity.publish_date,
                applicable_group=entity.applicable_group,
                source_url=HttpUrl(entity.source_url) if entity.source_url else None,
                version=entity.version,
                file_type=file_type,
            ),
            content_sha256=entity.content_sha256,
            status=entity.status.value,
            chunk_count=chunk_count,
            created_at=entity.created_at,
        )
