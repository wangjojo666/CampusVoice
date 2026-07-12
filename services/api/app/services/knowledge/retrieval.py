import math
import re
import threading
from collections import Counter
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

import numpy as np

from app.schemas.knowledge import DocumentChunk


class ChunkRetriever(Protocol):
    def rank(
        self,
        query: str,
        chunks: Sequence[DocumentChunk],
        *,
        limit: int,
    ) -> list[tuple[DocumentChunk, float]]: ...


class RetrievalModelError(RuntimeError):
    """A configured retrieval model could not be loaded or executed."""


def lexical_tokens(text: str) -> list[str]:
    lowered = text.lower()
    latin = re.findall(r"[a-z0-9]+", lowered)
    chinese_runs = re.findall(r"[\u3400-\u9fff]+", lowered)
    chinese: list[str] = []
    for run in chinese_runs:
        chinese.extend(run)
        chinese.extend(run[index : index + 2] for index in range(len(run) - 1))
    return latin + chinese


class LexicalRetriever:
    """Deterministic TF-IDF cosine retriever; interchangeable with an embedding retriever."""

    def rank(
        self,
        query: str,
        chunks: Sequence[DocumentChunk],
        *,
        limit: int,
    ) -> list[tuple[DocumentChunk, float]]:
        query_counts = Counter(lexical_tokens(query))
        if not query_counts or not chunks:
            return []
        chunk_counts = [Counter(lexical_tokens(chunk.content)) for chunk in chunks]
        document_frequency: Counter[str] = Counter()
        for counts in chunk_counts:
            document_frequency.update(counts.keys())
        count = len(chunks)

        def weight(token: str) -> float:
            return math.log((count + 1) / (document_frequency[token] + 1)) + 1

        query_norm = math.sqrt(
            sum((frequency * weight(token)) ** 2 for token, frequency in query_counts.items())
        )
        scored: list[tuple[DocumentChunk, float]] = []
        normalized_query = re.sub(r"\s+", "", query.lower())
        for chunk, counts in zip(chunks, chunk_counts, strict=True):
            dot = sum(
                query_frequency * counts[token] * weight(token) ** 2
                for token, query_frequency in query_counts.items()
            )
            chunk_norm = math.sqrt(
                sum((frequency * weight(token)) ** 2 for token, frequency in counts.items())
            )
            cosine = dot / (query_norm * chunk_norm) if query_norm and chunk_norm else 0.0
            if normalized_query and normalized_query in re.sub(r"\s+", "", chunk.content.lower()):
                cosine = min(1.0, cosine + 0.15)
            if cosine > 0:
                scored.append((chunk, round(min(1.0, cosine), 6)))
        scored.sort(key=lambda item: (-item[1], item[0].document_id, item[0].ordinal))
        return scored[:limit]


class _SentenceTransformerHandle:
    def __init__(self, model_name: str, device: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer(model_name, device=device)
        except Exception as exc:
            raise RetrievalModelError(
                f"无法加载中文向量模型 {model_name}，请检查模型缓存和网络配置。"
            ) from exc
        self.lock = threading.Lock()


@lru_cache(maxsize=4)
def _get_sentence_transformer(model_name: str, device: str) -> _SentenceTransformerHandle:
    return _SentenceTransformerHandle(model_name, device)


class SentenceTransformerRetriever:
    """Lazy Chinese dense retriever with persisted normalized chunk embeddings."""

    _QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

    def __init__(self, model_name: str, *, device: str = "cpu") -> None:
        self.model_name = model_name
        self.device = device

    def _encode(self, texts: Sequence[str]) -> np.ndarray:
        handle = _get_sentence_transformer(self.model_name, self.device)
        try:
            with handle.lock:
                encoded = handle.model.encode(
                    list(texts),
                    convert_to_numpy=True,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                )
        except Exception as exc:
            raise RetrievalModelError("中文向量编码失败，请稍后重试。") from exc
        return np.asarray(encoded, dtype=np.float32)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return [[float(value) for value in row] for row in self._encode(texts)]

    def rank(
        self,
        query: str,
        chunks: Sequence[DocumentChunk],
        *,
        limit: int,
    ) -> list[tuple[DocumentChunk, float]]:
        if not chunks:
            return []
        query_vector = self._encode([self._QUERY_INSTRUCTION + query])[0]
        if all(chunk.embedding is not None for chunk in chunks):
            chunk_vectors = np.asarray([chunk.embedding for chunk in chunks], dtype=np.float32)
        else:
            chunk_vectors = self._encode([chunk.content for chunk in chunks])
        similarities = chunk_vectors @ query_vector
        ranked = sorted(
            (
                (chunk, round(float(np.clip(score, 0.0, 1.0)), 6))
                for chunk, score in zip(chunks, similarities, strict=True)
                if score > 0
            ),
            key=lambda item: (-item[1], item[0].document_id, item[0].ordinal),
        )
        return ranked[:limit]
