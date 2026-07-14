import io
import threading
import zipfile

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.schemas.knowledge import DocumentFileType, DocumentMetadata
from app.services.knowledge import InMemoryKnowledgeRepository, KnowledgeService
from app.services.knowledge import parser as document_parser
from app.services.knowledge import service as knowledge_service_module
from app.services.knowledge.parser import (
    DEFAULT_DOCUMENT_LIMITS,
    DocumentLimitError,
    DocumentLimits,
    ParsedSection,
    split_sections,
)


def _assert_upload_limit(response: Response, code: str) -> None:
    assert response.status_code == 413
    payload = response.json()
    assert payload["error"]["code"] == code
    assert payload["error"]["details"] == {}


def test_oversized_extracted_text_is_rejected_without_persisting(client: TestClient) -> None:
    content = "文" * (DEFAULT_DOCUMENT_LIMITS.max_extracted_characters + 1)

    response = client.post(
        "/api/documents",
        files={"file": ("oversized.txt", content.encode(), "text/plain")},
        data={"title": "超限文本"},
    )

    _assert_upload_limit(response, "document_text_too_large")
    assert client.get("/api/documents").json() == []


def test_pdf_over_page_limit_is_rejected_before_page_extraction(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Page:
        def extract_text(self) -> str:
            raise AssertionError("page text must not be extracted after the page limit is exceeded")

    class Reader:
        is_encrypted = False
        pages = [Page()] * (DEFAULT_DOCUMENT_LIMITS.max_pdf_pages + 1)

        def __init__(self, stream: object) -> None:
            del stream

    monkeypatch.setattr(document_parser, "PdfReader", Reader)

    response = client.post(
        "/api/documents",
        files={"file": ("too-many-pages.pdf", b"synthetic-pdf", "application/pdf")},
        data={"title": "超页数 PDF"},
    )

    _assert_upload_limit(response, "document_page_limit_exceeded")
    assert client.get("/api/documents").json() == []


def test_suspicious_docx_compression_is_rejected_before_docx_parser(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "A" * 2_000_000)

    def fail_if_parsed(stream: object) -> None:
        del stream
        raise AssertionError("python-docx must not open a suspicious archive")

    monkeypatch.setattr(document_parser, "DocxDocument", fail_if_parsed)

    response = client.post(
        "/api/documents",
        files={
            "file": (
                "compression-bomb.docx",
                stream.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"title": "异常压缩 DOCX"},
    )

    _assert_upload_limit(response, "suspicious_docx_compression")
    assert "AssertionError" not in response.text
    assert client.get("/api/documents").json() == []


def test_malformed_docx_returns_stable_error_without_zip_details(client: TestClient) -> None:
    response = client.post(
        "/api/documents",
        files={
            "file": (
                "malformed.docx",
                b"this-is-not-a-zip-archive",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"title": "损坏 DOCX"},
    )

    assert response.status_code == 422
    assert response.json()["error"] == {
        "code": "document_parse_failed",
        "message": "文档解析失败，请检查文件内容。",
        "details": {},
    }
    assert "BadZipFile" not in response.text
    assert "File is not a zip file" not in response.text
    assert client.get("/api/documents").json() == []


def test_docx_expanded_size_limit_is_checked_from_zip_metadata() -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("word/document.xml", b"A" * 129)
    limits = DocumentLimits(
        max_docx_uncompressed_bytes=128,
        max_docx_compression_ratio=1_000,
    )

    with pytest.raises(DocumentLimitError) as error:
        document_parser.parse_document(
            stream.getvalue(),
            DocumentFileType.DOCX,
            limits=limits,
        )

    assert error.value.code == "document_expanded_size_too_large"


def test_docx_archive_entry_limit_is_checked_before_parsing() -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for index in range(4):
            archive.writestr(f"word/junk-{index}.xml", b"")
    limits = DocumentLimits(max_docx_entries=3)

    with pytest.raises(DocumentLimitError) as error:
        document_parser.parse_document(
            stream.getvalue(),
            DocumentFileType.DOCX,
            limits=limits,
        )

    assert error.value.code == "document_archive_entry_limit_exceeded"


def test_chunk_limit_stops_document_splitting() -> None:
    with pytest.raises(DocumentLimitError) as error:
        split_sections(
            [ParsedSection("A" * 301, page_number=None)],
            chunk_size=100,
            overlap=0,
            max_chunks=3,
        )

    assert error.value.code == "document_chunk_limit_exceeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("file_type", [DocumentFileType.PDF, DocumentFileType.DOCX])
async def test_pdf_and_docx_parsing_runs_outside_the_event_loop_thread(
    file_type: DocumentFileType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_loop_thread = threading.get_ident()
    parser_threads: list[int] = []

    def fake_parse_document(
        content: bytes,
        parsed_file_type: DocumentFileType,
        *,
        limits: DocumentLimits,
    ) -> list[ParsedSection]:
        del content, limits
        assert parsed_file_type == file_type
        parser_threads.append(threading.get_ident())
        return [ParsedSection("可提取文本", page_number=None)]

    monkeypatch.setattr(knowledge_service_module, "parse_document", fake_parse_document)
    service = KnowledgeService(InMemoryKnowledgeRepository())

    record = await service.ingest(
        user_id="user_demo",
        metadata=DocumentMetadata(title="线程测试", file_type=file_type),
        content=b"synthetic-document",
    )

    assert record.chunk_count == 1
    assert parser_threads and parser_threads[0] != event_loop_thread
