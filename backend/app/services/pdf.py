"""PDF ingestion and text/table extraction.

Safety rules applied here (also enforced in the upload route): only ``.pdf``,
size limits, safe filenames, no content execution. Extraction is performed with
pypdf (text) and pdfplumber (tables). Every extracted artifact is page-scoped so
the evidence layer can record provenance.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pdfplumber
from pypdf import PdfReader

from backend.app.config import settings


class PdfError(Exception):
    pass


class UnsupportedFileTypeError(PdfError):
    pass


class FileTooLargeError(PdfError):
    pass


@dataclass
class PageTable:
    page: int
    table: list[list[str]]
    text_rows: list[str] = field(default_factory=list)


@dataclass
class PdfExtraction:
    filename: str
    page_count: int
    pages: list[str] = field(default_factory=list)
    tables: list[PageTable] = field(default_factory=list)
    sha256: str = ""
    warnings: list[str] = field(default_factory=list)

    def total_chars(self) -> int:
        return sum(len(p) for p in self.pages)


def validate_upload(filename: str, content: bytes) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in settings.allowed_extensions:
        raise UnsupportedFileTypeError(f"File type '{ext or 'unknown'}' not allowed. Only {settings.allowed_extensions}.")
    if len(content) > settings.max_upload_size_mb * 1024 * 1024:
        raise FileTooLargeError(
            f"File exceeds the {settings.max_upload_size_mb} MB upload limit."
        )
    # MIME / magic-byte validation: the bytes must actually look like a PDF.
    # A matching extension with arbitrary content is rejected here before any
    # pypdf parsing or disk persistence happens.
    head = content.lstrip()[:4]
    if head != b"%PDF":
        raise UnsupportedFileTypeError(
            "File content is not a PDF (missing %PDF magic bytes)."
        )


def safe_filename(filename: str) -> str:
    """Strip path separators and control characters; keep a sane name."""
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip()
    return name[:255] or "document.pdf"


def sha256_of(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def extract_pdf(filename: str, content: bytes) -> PdfExtraction:
    """Extract page text and tables from PDF bytes."""
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:  # pragma: no cover - pypdf raises many variants
        raise PdfError(f"Failed to open PDF: {exc}") from exc

    pages: list[str] = []
    page_count = len(reader.pages)

    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append(text.strip())

    tables: list[PageTable] = []
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for idx, page in enumerate(pdf.pages):
                extracted = page.extract_text() or ""
                try:
                    raw_tables = page.extract_tables() or []
                except Exception:
                    raw_tables = []
                for table in raw_tables:
                    clean: list[list[str]] = []
                    for row in table:
                        clean.append(
                            [(c or "").strip() for c in row]
                        )
                    if clean and any(any(c for c in row) for row in clean):
                        tables.append(PageTable(page=idx + 1, table=clean, text_rows=[extracted]))
    except Exception:  # pdfplumber failure should not kill ingestion; text fallback exists
        # tables stay empty
        pass

    return PdfExtraction(
        filename=filename,
        page_count=page_count,
        pages=pages,
        tables=tables,
        sha256=sha256_of(content),
    )


# ---------------------------------------------------------------------------
# Document type detection
# ---------------------------------------------------------------------------

_KEYWORDS = {
    "rfq": ["request for quotation", "rfq", "request for quote", "enquiry", "tender reference"],
    "quote": ["quotation", "quote no", "price quotation", "offer no", "quotation no", "proforma"],
    "policy": ["procurement policy", "purchase policy", "procurement guidelines", "policy"],
    "spec": ["technical specification", "specification", "spec no", "technical data sheet"],
    "certificate": ["certificate", "certification", "iso 9001", "iso 14001", "compliance certificate"],
    "history": ["purchase history", "procurement history", "past purchases", "previous order", "historic"],
}


def detect_document_type(filename: str, text: str) -> str:
    """Best-effort document type detection without an LLM.

    Order matters: certificate documents also mention ISO standards which appear
    in RFQs; keyword priority + filename cues are used.
    """
    haystack = f"{safe_filename(filename)} {text[:3000]}".lower()
    for doc_type, keywords in _KEYWORDS.items():
        first = keywords[0]
        if first in haystack:
            return doc_type
    for doc_type, keywords in _KEYWORDS.items():
        if any(kw in haystack for kw in keywords[1:]):
            return doc_type
    return "other"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

_MIN_CHUNK_CHARS = 180
_MAX_CHUNK_CHARS = 1400


def chunk_page(page_text: str, page: int, *, doc_type: str) -> list[dict]:
    """Split a page into overlapping-ish sentence-aligned chunks with metadata."""
    text = page_text.strip()
    if not text:
        return []
    # split into paragraph-ish units
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[dict] = []
    buffer = ""
    section = _detect_section(text)

    def flush(buf: str):
        if len(buf.strip()) >= _MIN_CHUNK_CHARS or (chunks == [] and buf.strip()):
            chunks.append(
                {
                    "page": page,
                    "section": section,
                    "content": buf.strip(),
                }
            )

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(buffer) + len(para) > _MAX_CHUNK_CHARS:
            flush(buffer)
            buffer = para
        else:
            buffer = f"{buffer}\n{para}" if buffer else para
    flush(buffer)
    return chunks


_SECTION_PATTERNS = [
    (re.compile(r"requirements", re.I), "requirements"),
    (re.compile(r"delivery", re.I), "delivery"),
    (re.compile(r"payment", re.I), "payment"),
    (re.compile(r"certification", re.I), "certification"),
    (re.compile(r"price|quotation", re.I), "commercial"),
    (re.compile(r"specification", re.I), "specification"),
    (re.compile(r"terms and conditions", re.I), "terms"),
]


def _detect_section(text: str) -> str | None:
    for pattern, name in _SECTION_PATTERNS:
        if pattern.search(text):
            return name
    return None


def tables_to_text(page_table: PageTable) -> str:
    """Render a PageTable to readable text for evidence + retrieval."""
    lines = [page_table.text_rows[0]] if page_table.text_rows else []
    for row in page_table.table:
        lines.append(" | ".join(cell for cell in row).rstrip(" |"))
    return "\n".join(lines)


def render_table_texts(pages_text: list[str], tables: list[PageTable]) -> list[str]:
    """Return a list of page texts with table rows appended (for extraction)."""
    result = list(pages_text)
    for pt in tables:
        if pt.page - 1 < len(result):
            append = tables_to_text(pt)
            if append not in result[pt.page - 1]:
                result[pt.page - 1] = f"{result[pt.page - 1]}\n\n{append}"
    return result