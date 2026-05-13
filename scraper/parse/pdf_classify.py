##### pdf_classify.py #####
##### brdyknndy #####


from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List

from pypdf import PdfReader


NO_DATA_PATTERNS = [
    "no data found",
    "no records found",
    "record not found",
    "there is no data",
]

LIKELY_NOPV_PATTERNS = [
    "notice of property value",
    "market value",
    "assessed value",
    "tax class",
]


@dataclass
class PDFClassification:
    status: str  # valid_statement | no_data_found | unreadable_pdf | empty_text
    page_count: int
    matched_no_data_patterns: List[str]
    matched_nopv_patterns: List[str]
    text_preview: str


def extract_pdf_text(path: Path, max_pages: int = 5) -> tuple[str, int]:
    reader = PdfReader(str(path))
    pages = reader.pages
    page_count = len(pages)
    chunks: List[str] = []

    for i, page in enumerate(pages[:max_pages]):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        chunks.append(txt)

    return "\n".join(chunks), page_count


def classify_pdf(path: Path) -> PDFClassification:
    if not path.exists():
        return PDFClassification(
            status="unreadable_pdf",
            page_count=0,
            matched_no_data_patterns=[],
            matched_nopv_patterns=[],
            text_preview="FILE_NOT_FOUND",
        )

    try:
        text, page_count = extract_pdf_text(path)
    except Exception as e:
        return PDFClassification(
            status="unreadable_pdf",
            page_count=0,
            matched_no_data_patterns=[],
            matched_nopv_patterns=[],
            text_preview=f"UNREADABLE: {type(e).__name__}: {e}",
        )

    normalized = (text or "").lower().strip()
    preview = (text or "").replace("\n", " ")[:300]

    if not normalized:
        return PDFClassification(
            status="empty_text",
            page_count=page_count,
            matched_no_data_patterns=[],
            matched_nopv_patterns=[],
            text_preview=preview,
        )

    matched_no_data = [p for p in NO_DATA_PATTERNS if p in normalized]
    matched_nopv = [p for p in LIKELY_NOPV_PATTERNS if p in normalized]

    if matched_no_data:
        status = "no_data_found"
    elif len(matched_nopv) >= 2:
        status = "valid_statement"
    else:
        # still parseable, but uncertain...
        status = "valid_statement"

    return PDFClassification(
        status=status,
        page_count=page_count,
        matched_no_data_patterns=matched_no_data,
        matched_nopv_patterns=matched_nopv,
        text_preview=preview,
    )