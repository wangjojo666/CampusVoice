import io
import re
from dataclasses import dataclass

from docx import Document as DocxDocument
from pypdf import PdfReader

from app.schemas.knowledge import DocumentFileType


class DocumentParseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ParsedSection:
    text: str
    page_number: int | None


def _clean_text(value: str) -> str:
    value = value.replace("\x00", "")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def parse_document(content: bytes, file_type: DocumentFileType) -> list[ParsedSection]:
    if not content:
        raise DocumentParseError("empty_document", "上传的文档为空。")
    try:
        if file_type == DocumentFileType.PDF:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                try:
                    unlocked = reader.decrypt("")
                except Exception as exc:
                    raise DocumentParseError(
                        "encrypted_document", "无法读取加密 PDF，请先移除密码。"
                    ) from exc
                if not unlocked:
                    raise DocumentParseError(
                        "encrypted_document", "无法读取加密 PDF，请先移除密码。"
                    )
            sections = [
                ParsedSection(_clean_text(page.extract_text() or ""), page_number=index + 1)
                for index, page in enumerate(reader.pages)
            ]
        elif file_type == DocumentFileType.DOCX:
            document = DocxDocument(io.BytesIO(content))
            paragraphs = [_clean_text(paragraph.text) for paragraph in document.paragraphs]
            table_rows = [
                "\t".join(_clean_text(cell.text) for cell in row.cells)
                for table in document.tables
                for row in table.rows
            ]
            # DOCX has no reliable page mapping without a layout engine, so page is null.
            sections = [
                ParsedSection(
                    "\n".join(filter(None, [*paragraphs, *table_rows])),
                    page_number=None,
                )
            ]
        elif file_type in {DocumentFileType.TXT, DocumentFileType.MARKDOWN}:
            try:
                decoded = content.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise DocumentParseError(
                    "unsupported_encoding", "TXT/Markdown 文档必须使用 UTF-8 编码。"
                ) from exc
            sections = [ParsedSection(_clean_text(decoded), page_number=None)]
        else:
            raise DocumentParseError("unsupported_file_type", "不支持的文档类型。")
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError("document_parse_failed", "文档解析失败，请检查文件内容。") from exc

    nonempty = [section for section in sections if section.text]
    if not nonempty:
        raise DocumentParseError(
            "no_extractable_text",
            "文档中没有可提取的文本；扫描版 PDF 需要先进行 OCR。",
        )
    return nonempty


def split_sections(
    sections: list[ParsedSection],
    *,
    chunk_size: int = 700,
    overlap: int = 100,
) -> list[ParsedSection]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap smaller than chunk_size")
    chunks: list[ParsedSection] = []
    for section in sections:
        text = section.text
        start = 0
        while start < len(text):
            hard_end = min(len(text), start + chunk_size)
            end = hard_end
            if hard_end < len(text):
                boundary = max(
                    text.rfind("\n", start + chunk_size // 2, hard_end),
                    text.rfind("。", start + chunk_size // 2, hard_end),
                    text.rfind("；", start + chunk_size // 2, hard_end),
                )
                if boundary > start:
                    end = boundary + 1
            piece = text[start:end].strip()
            if piece:
                chunks.append(ParsedSection(piece, section.page_number))
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks
