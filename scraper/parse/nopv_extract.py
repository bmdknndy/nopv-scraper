from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from pypdf import PdfReader
from scraper.parse.pdf_classify import classify_pdf


# ----------------------------
# Utility helpers
# ----------------------------

def _to_int_money(s: str) -> Optional[int]:
    try:
        return int(float(s.replace("$", "").replace(",", "").strip()))
    except Exception:
        return None


def _to_float_money(s: str) -> Optional[float]:
    try:
        return float(s.replace("$", "").replace(",", "").strip())
    except Exception:
        return None


def _to_pct(s: str) -> Optional[float]:
    try:
        return float(s.replace("%", "").strip())
    except Exception:
        return None


def _normalize_text(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _extract_text(path: Path, max_pages: int = 30) -> tuple[str, int]:
    reader = PdfReader(str(path))
    pages = reader.pages
    page_count = len(pages)
    parts = []
    for p in pages[:max_pages]:
        try:
            parts.append(p.extract_text() or "")
        except Exception:
            parts.append("")
    return "\n".join(parts), page_count


def _first_match(patterns: list[re.Pattern], text: str) -> Optional[str]:
    for p in patterns:
        m = p.search(text)
        if m:
            return m.group(1)
    return None


# ----------------------------
# Financial pattern library
# ----------------------------

MV_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}\s+market value:\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"\bmarket value for this property is\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"\bmarket value\s*\$([\d,]+(?:\.\d+)?)", re.I),
]

AV_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}\s+assessed value:\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"\bactual assessed value\s*\$([\d,]+(?:\.\d+)?)\s*\$([\d,]+(?:\.\d+)?)", re.I),  # current,next
    re.compile(r"\bactual assessed value\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"\bassessed value\s*\$([\d,]+(?:\.\d+)?)", re.I),
]

TAXABLE_TAX_PATTERNS = [
    re.compile(
        r"taxable value\s*\$([\d,]+(?:\.\d+)?)\s*x\s*[\d.]+\s*=\s*\$([\d,]+(?:\.\d+)?)",
        re.I,
    ),
    re.compile(
        r"\$([\d,]+(?:\.\d+)?)\s*x\s*0?\.\d+\s*=\s*\$([\d,]+(?:\.\d+)?)",
        re.I,
    ),
]

GROSS_INCOME_PATTERNS = [
    re.compile(r"estimated gross income:\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"gross income:\s*\$([\d,]+(?:\.\d+)?)", re.I),
]

EXPENSES_PATTERNS = [
    re.compile(r"estimated expenses:\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"\bexpenses:\s*\$([\d,]+(?:\.\d+)?)", re.I),
]

NOI_PATTERNS = [
    re.compile(r"net operating income of\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"resulting in a net operating income of\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"net operating income:\s*\$([\d,]+(?:\.\d+)?)", re.I),
]

BASE_CAP_PATTERNS = [
    re.compile(r"base (?:capitalization )?rate:\s*.*?(\d{1,2}(?:\.\d+)?)%", re.I),
    re.compile(r"used a capitalization rate of\s*(\d{1,2}(?:\.\d+)?)%", re.I),
]

OVERALL_CAP_PATTERNS = [
    re.compile(r"overall capitalization rate.*?is\s*(\d{1,2}(?:\.\d+)?)%", re.I),
    re.compile(r"overall cap(?:italization)? rate.*?(\d{1,2}(?:\.\d+)?)%", re.I),
]


# ----------------------------
# Output model
# ----------------------------

@dataclass
class NOPVRecord:
    bbl: str
    stmt_date: str
    source_pdf_path: str
    semantic_status: str
    page_count: int

    market_value: Optional[int]
    assessed_value: Optional[int]
    taxable_value: Optional[int]
    estimated_property_tax: Optional[float]

    estimated_gross_income: Optional[float]
    estimated_expenses: Optional[float]
    net_operating_income: Optional[float]

    base_cap_rate_percent: Optional[float]
    overall_cap_rate_percent: Optional[float]

    # provenance: parsed_direct | derived | missing_in_document
    market_value_source: str
    assessed_value_source: str
    taxable_value_source: str
    estimated_property_tax_source: str
    estimated_gross_income_source: str
    estimated_expenses_source: str
    net_operating_income_source: str
    base_cap_rate_percent_source: str
    overall_cap_rate_percent_source: str

    parse_status: str      # ok | partial | no_data_found | failed
    parse_notes: str
    text_preview: str


def parse_nopv_pdf(pdf_path: Path) -> NOPVRecord:
    bbl = pdf_path.parent.name
    stmt_date = pdf_path.stem.split("_")[0] if "_" in pdf_path.stem else "unknown"

    semantic = classify_pdf(pdf_path)

    if semantic.status == "no_data_found":
        return NOPVRecord(
            bbl=bbl,
            stmt_date=stmt_date,
            source_pdf_path=str(pdf_path),
            semantic_status="no_data_found",
            page_count=semantic.page_count,
            market_value=None,
            assessed_value=None,
            taxable_value=None,
            estimated_property_tax=None,
            estimated_gross_income=None,
            estimated_expenses=None,
            net_operating_income=None,
            base_cap_rate_percent=None,
            overall_cap_rate_percent=None,
            market_value_source="missing_in_document",
            assessed_value_source="missing_in_document",
            taxable_value_source="missing_in_document",
            estimated_property_tax_source="missing_in_document",
            estimated_gross_income_source="missing_in_document",
            estimated_expenses_source="missing_in_document",
            net_operating_income_source="missing_in_document",
            base_cap_rate_percent_source="missing_in_document",
            overall_cap_rate_percent_source="missing_in_document",
            parse_status="no_data_found",
            parse_notes="No data statement.",
            text_preview=semantic.text_preview,
        )

    try:
        raw_text, page_count = _extract_text(pdf_path)
    except Exception as e:
        return NOPVRecord(
            bbl=bbl,
            stmt_date=stmt_date,
            source_pdf_path=str(pdf_path),
            semantic_status="unreadable_pdf",
            page_count=0,
            market_value=None,
            assessed_value=None,
            taxable_value=None,
            estimated_property_tax=None,
            estimated_gross_income=None,
            estimated_expenses=None,
            net_operating_income=None,
            base_cap_rate_percent=None,
            overall_cap_rate_percent=None,
            market_value_source="missing_in_document",
            assessed_value_source="missing_in_document",
            taxable_value_source="missing_in_document",
            estimated_property_tax_source="missing_in_document",
            estimated_gross_income_source="missing_in_document",
            estimated_expenses_source="missing_in_document",
            net_operating_income_source="missing_in_document",
            base_cap_rate_percent_source="missing_in_document",
            overall_cap_rate_percent_source="missing_in_document",
            parse_status="failed",
            parse_notes=f"PDF read failed: {type(e).__name__}: {e}",
            text_preview="",
        )

    text = _normalize_text(raw_text)

    # initialize sources
    market_value_source = "missing_in_document"
    assessed_value_source = "missing_in_document"
    taxable_value_source = "missing_in_document"
    estimated_property_tax_source = "missing_in_document"
    estimated_gross_income_source = "missing_in_document"
    estimated_expenses_source = "missing_in_document"
    net_operating_income_source = "missing_in_document"
    base_cap_rate_percent_source = "missing_in_document"
    overall_cap_rate_percent_source = "missing_in_document"

    # market value
    mv_raw = _first_match(MV_PATTERNS, text)
    market_value = _to_int_money(mv_raw) if mv_raw else None
    if market_value is not None:
        market_value_source = "parsed_direct"

    # assessed value + fallback from "Actual Assessed Value" pair
    assessed_value = None
    for p in AV_PATTERNS:
        m = p.search(text)
        if not m:
            continue
        # if two groups (current, next), choose second as "next year"
        if len(m.groups()) >= 2 and m.group(2):
            assessed_value = _to_int_money(m.group(2))
        else:
            assessed_value = _to_int_money(m.group(1))
        if assessed_value is not None:
            assessed_value_source = "parsed_direct"
            break

    # taxable + estimated tax
    taxable_value = None
    estimated_property_tax = None
    for p in TAXABLE_TAX_PATTERNS:
        m = p.search(text)
        if m:
            taxable_value = _to_int_money(m.group(1))
            estimated_property_tax = _to_float_money(m.group(2))
            if taxable_value is not None:
                taxable_value_source = "parsed_direct"
            if estimated_property_tax is not None:
                estimated_property_tax_source = "parsed_direct"
            break

    # income, expenses
    gi_raw = _first_match(GROSS_INCOME_PATTERNS, text)
    ex_raw = _first_match(EXPENSES_PATTERNS, text)
    estimated_gross_income = _to_float_money(gi_raw) if gi_raw else None
    estimated_expenses = _to_float_money(ex_raw) if ex_raw else None
    if estimated_gross_income is not None:
        estimated_gross_income_source = "parsed_direct"
    if estimated_expenses is not None:
        estimated_expenses_source = "parsed_direct"

    # NOI direct, else derived
    noi_raw = _first_match(NOI_PATTERNS, text)
    net_operating_income = _to_float_money(noi_raw) if noi_raw else None
    if net_operating_income is not None:
        net_operating_income_source = "parsed_direct"
    elif estimated_gross_income is not None and estimated_expenses is not None:
        net_operating_income = round(estimated_gross_income - estimated_expenses, 2)
        net_operating_income_source = "derived"

    # cap rates
    base_cap_raw = _first_match(BASE_CAP_PATTERNS, text)
    overall_cap_raw = _first_match(OVERALL_CAP_PATTERNS, text)
    base_cap_rate_percent = _to_pct(base_cap_raw) if base_cap_raw else None
    overall_cap_rate_percent = _to_pct(overall_cap_raw) if overall_cap_raw else None
    if base_cap_rate_percent is not None:
        base_cap_rate_percent_source = "parsed_direct"
    if overall_cap_rate_percent is not None:
        overall_cap_rate_percent_source = "parsed_direct"

    # quality score on core financials
    core = [
        market_value,
        assessed_value,
        estimated_gross_income,
        estimated_expenses,
        net_operating_income,
        overall_cap_rate_percent,
    ]
    found_core = sum(v is not None for v in core)

    if found_core >= 3:
        parse_status = "ok"
        notes = "Core financial fields extracted."
    elif found_core >= 1:
        parse_status = "partial"
        notes = "Some financial fields extracted; others missing in doc or unmatched."
    else:
        parse_status = "failed"
        notes = "No core financial fields extracted."

    return NOPVRecord(
        bbl=bbl,
        stmt_date=stmt_date,
        source_pdf_path=str(pdf_path),
        semantic_status="valid_statement",
        page_count=page_count,
        market_value=market_value,
        assessed_value=assessed_value,
        taxable_value=taxable_value,
        estimated_property_tax=estimated_property_tax,
        estimated_gross_income=estimated_gross_income,
        estimated_expenses=estimated_expenses,
        net_operating_income=net_operating_income,
        base_cap_rate_percent=base_cap_rate_percent,
        overall_cap_rate_percent=overall_cap_rate_percent,
        market_value_source=market_value_source,
        assessed_value_source=assessed_value_source,
        taxable_value_source=taxable_value_source,
        estimated_property_tax_source=estimated_property_tax_source,
        estimated_gross_income_source=estimated_gross_income_source,
        estimated_expenses_source=estimated_expenses_source,
        net_operating_income_source=net_operating_income_source,
        base_cap_rate_percent_source=base_cap_rate_percent_source,
        overall_cap_rate_percent_source=overall_cap_rate_percent_source,
        parse_status=parse_status,
        parse_notes=notes,
        text_preview=text[:320],
    )


def record_to_dict(record: NOPVRecord) -> dict:
    return asdict(record)