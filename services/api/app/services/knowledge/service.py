import asyncio
import hashlib
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from app.schemas.knowledge import (
    ApplicabilityConflict,
    DocumentChunk,
    DocumentMetadata,
    DocumentRecord,
    KnowledgeAskResponse,
    KnowledgeCitation,
    KnowledgeSearchResponse,
    VersionConflict,
)
from app.services.knowledge.answering import KnowledgeAnswerer, KnowledgeAnswererError
from app.services.knowledge.parser import DocumentParseError, parse_document, split_sections
from app.services.knowledge.retrieval import ChunkRetriever, LexicalRetriever


class KnowledgeRepository(Protocol):
    async def save(self, document: DocumentRecord, chunks: Sequence[DocumentChunk]) -> None: ...

    async def list_documents(self) -> list[DocumentRecord]: ...

    async def list_chunks(self) -> list[DocumentChunk]: ...


class InMemoryKnowledgeRepository:
    """Process-local development repository; production should wire the SQLAlchemy adapter."""

    def __init__(self) -> None:
        self._documents: dict[str, DocumentRecord] = {}
        self._chunks: dict[str, DocumentChunk] = {}

    async def save(self, document: DocumentRecord, chunks: Sequence[DocumentChunk]) -> None:
        self._documents[document.id] = document
        self._chunks.update({chunk.id: chunk for chunk in chunks})

    async def list_documents(self) -> list[DocumentRecord]:
        return sorted(self._documents.values(), key=lambda item: item.created_at, reverse=True)

    async def list_chunks(self) -> list[DocumentChunk]:
        return sorted(self._chunks.values(), key=lambda item: (item.document_id, item.ordinal))


class KnowledgeService:
    def __init__(
        self,
        repository: KnowledgeRepository,
        *,
        retriever: ChunkRetriever | None = None,
        answerer: KnowledgeAnswerer | None = None,
    ) -> None:
        self._repository = repository
        self._retriever = retriever or LexicalRetriever()
        self._answerer = answerer

    async def ingest(
        self,
        *,
        user_id: str,
        metadata: DocumentMetadata,
        content: bytes,
    ) -> DocumentRecord:
        sections = split_sections(parse_document(content, metadata.file_type))
        document_id = f"doc_{uuid4().hex}"
        chunks = [
            DocumentChunk(
                id=f"chunk_{hashlib.sha256(f'{document_id}:{index}:{section.text}'.encode()).hexdigest()[:24]}",
                document_id=document_id,
                ordinal=index,
                content=section.text,
                page_number=section.page_number,
            )
            for index, section in enumerate(sections)
        ]
        embed_documents = getattr(self._retriever, "embed_documents", None)
        if callable(embed_documents) and chunks:
            embeddings = await asyncio.to_thread(
                embed_documents,
                [chunk.content for chunk in chunks],
            )
            if len(embeddings) != len(chunks):
                raise RuntimeError("embedding model returned an unexpected vector count")
            chunks = [
                chunk.model_copy(update={"embedding": embedding})
                for chunk, embedding in zip(chunks, embeddings, strict=True)
            ]
        record = DocumentRecord(
            id=document_id,
            user_id=user_id,
            metadata=metadata,
            content_sha256=hashlib.sha256(content).hexdigest(),
            status="ready",
            chunk_count=len(chunks),
            created_at=datetime.now(UTC),
        )
        await self._repository.save(record, chunks)
        return record

    async def list_documents(self) -> list[DocumentRecord]:
        return await self._repository.list_documents()

    async def search(
        self,
        query: str,
        *,
        top_k: int,
        min_similarity: float,
        version: str | None = None,
        applicable_group: str | None = None,
    ) -> KnowledgeSearchResponse:
        documents = await self._repository.list_documents()
        document_map = {
            document.id: document
            for document in documents
            if (version is None or document.metadata.version == version)
            and (
                applicable_group is None
                or _applicable_group_matches(
                    document.metadata.applicable_group,
                    applicable_group,
                )
            )
        }
        chunks = [
            chunk
            for chunk in await self._repository.list_chunks()
            if chunk.document_id in document_map
        ]
        ranked = await asyncio.to_thread(
            self._retriever.rank,
            query,
            chunks,
            limit=top_k * 3,
        )
        citations: list[KnowledgeCitation] = []
        for chunk, similarity in ranked:
            if similarity < min_similarity or chunk.document_id not in document_map:
                continue
            document = document_map[chunk.document_id]
            citations.append(
                KnowledgeCitation(
                    document_id=document.id,
                    chunk_id=chunk.id,
                    original_text=chunk.content,
                    page_number=chunk.page_number,
                    similarity=similarity,
                    file_title=document.metadata.title,
                    publish_date=document.metadata.publish_date,
                    version=document.metadata.version,
                    applicable_group=document.metadata.applicable_group,
                )
            )
            if len(citations) >= top_k:
                break
        return KnowledgeSearchResponse(
            query=query,
            results=citations,
            version_conflicts=self._version_conflicts(citations),
            applicability_conflicts=self._applicability_conflicts(citations),
        )

    async def ask(
        self,
        question: str,
        *,
        top_k: int,
        min_similarity: float,
        version: str | None = None,
        applicable_group: str | None = None,
    ) -> KnowledgeAskResponse:
        search = await self.search(
            question,
            top_k=top_k,
            min_similarity=min_similarity,
            version=version,
            applicable_group=applicable_group,
        )
        if not search.results:
            return KnowledgeAskResponse(
                question=question,
                answer="现有校园通知中没有足够证据回答这个问题。",
                sufficient_evidence=False,
                insufficiency_reason="未检索到达到相似度阈值的原文片段",
                citations=[],
                version_conflicts=[],
                applicability_conflicts=[],
            )
        if search.version_conflicts or search.applicability_conflicts:
            reasons: list[str] = []
            if search.version_conflicts:
                reasons.append("检索到同名通知的多个版本")
            if search.applicability_conflicts:
                reasons.append("检索到面向不同群体的通知")
            return KnowledgeAskResponse(
                question=question,
                answer=("现有证据存在版本或适用群体冲突，请先指定版本与适用对象后再回答。"),
                sufficient_evidence=False,
                insufficiency_reason="；".join(reasons),
                citations=search.results,
                version_conflicts=search.version_conflicts,
                applicability_conflicts=search.applicability_conflicts,
            )
        llm_answer = await self._try_grounded_answer(question, search.results)
        if llm_answer is not None:
            return KnowledgeAskResponse(
                question=question,
                answer=llm_answer[0],
                sufficient_evidence=llm_answer[1],
                insufficiency_reason=llm_answer[2],
                citations=llm_answer[3],
                version_conflicts=[],
                applicability_conflicts=[],
            )
        excerpts = []
        for index, citation in enumerate(search.results[:3], start=1):
            date_label = (
                citation.publish_date.isoformat() if citation.publish_date else "发布日期未知"
            )
            page_label = (
                f"，第 {citation.page_number} 页" if citation.page_number is not None else ""
            )
            excerpts.append(
                f"[{index}]《{citation.file_title}》（{date_label}{page_label}）：{citation.original_text}"
            )
        conflict_notice = (
            " 检索结果存在多个版本，请先核对版本差异。" if search.version_conflicts else ""
        )
        return KnowledgeAskResponse(
            question=question,
            answer="根据检索到的校园通知原文：\n" + "\n".join(excerpts) + conflict_notice,
            sufficient_evidence=True,
            citations=search.results,
            version_conflicts=search.version_conflicts,
            applicability_conflicts=search.applicability_conflicts,
        )

    async def _try_grounded_answer(
        self,
        question: str,
        citations: Sequence[KnowledgeCitation],
    ) -> tuple[str, bool, str | None, list[KnowledgeCitation]] | None:
        """Return a validated provider answer, or None for the evidence-only fallback."""

        if self._answerer is None:
            return None
        try:
            generated = await self._answerer.generate(question, citations)
        except KnowledgeAnswererError:
            return None
        if not generated.sufficient:
            return (
                "现有校园通知证据不足，无法可靠回答这个问题。",
                False,
                "证据问答模型判断现有原文不足",
                list(citations),
            )
        markers = {int(value) for value in re.findall(r"\[(\d+)]", generated.answer)}
        selected_indexes = set(generated.citation_indexes)
        valid_indexes = set(range(1, len(citations) + 1))
        lines = [line.strip() for line in generated.answer.splitlines() if line.strip()]
        if (
            not markers
            or markers != selected_indexes
            or not markers <= valid_indexes
            or any(not re.search(r"\[\d+]", line) for line in lines)
        ):
            return None
        selected = [citations[index - 1] for index in sorted(markers)]
        return generated.answer, True, None, selected

    @staticmethod
    def _version_conflicts(citations: Sequence[KnowledgeCitation]) -> list[VersionConflict]:
        grouped: dict[str, dict[str, KnowledgeCitation]] = defaultdict(dict)
        for citation in citations:
            if citation.version:
                grouped[citation.file_title][citation.document_id] = citation
        conflicts: list[VersionConflict] = []
        for title, by_document in grouped.items():
            versions = sorted({item.version for item in by_document.values() if item.version})
            if len(versions) > 1:
                conflicts.append(
                    VersionConflict(
                        title=title,
                        document_ids=sorted(by_document),
                        versions=versions,
                        message=f"《{title}》存在多个版本，请核对适用版本后再操作。",
                    )
                )
        return sorted(conflicts, key=lambda item: item.title)

    @staticmethod
    def _applicability_conflicts(
        citations: Sequence[KnowledgeCitation],
    ) -> list[ApplicabilityConflict]:
        grouped: dict[str, dict[str, KnowledgeCitation]] = defaultdict(dict)
        for citation in citations:
            if citation.applicable_group:
                grouped[citation.file_title][citation.document_id] = citation
        conflicts: list[ApplicabilityConflict] = []
        for title, by_document in grouped.items():
            groups = sorted(
                {item.applicable_group for item in by_document.values() if item.applicable_group}
            )
            if len(groups) > 1:
                conflicts.append(
                    ApplicabilityConflict(
                        title=title,
                        document_ids=sorted(by_document),
                        applicable_groups=groups,
                        message=f"《{title}》面向多个适用群体，请先确认年级或对象。",
                    )
                )
        return sorted(conflicts, key=lambda item: item.title)


def _applicable_group_matches(stored: str | None, requested: str) -> bool:
    if stored is None:
        return True
    normalized_stored = re.sub(r"\s+", "", stored).casefold()
    normalized_requested = re.sub(r"\s+", "", requested).casefold()
    if any(token in normalized_stored for token in ("全体", "所有", "不限")):
        return True
    return (
        normalized_stored == normalized_requested
        or normalized_stored in normalized_requested
        or normalized_requested in normalized_stored
    )


__all__ = [
    "DocumentParseError",
    "InMemoryKnowledgeRepository",
    "KnowledgeRepository",
    "KnowledgeService",
]
