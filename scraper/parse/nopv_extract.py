##### nopv_extract.py #####
##### brdyknndy #####
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from pypdf import PdfReader
from scraper.parse.pdf_classify import classify_pdf

logging.getLogger("pypdf").setLevel(logging.ERROR)


# Write helpers

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


# Identify financial patterns


# MARKET VALUE 
# Modern (2020+): "GLANCE2024-25 Market Value:$3,663,0002024-25..."
# Legacy 2017:    "MarketValue $2,350,000 +$859,000 $3,209,000"  (spaces between values)
# Legacy 2018:    "MarketValue $1,205,000+$190,000$1,395,000"    (NO spaces between values)
# Legacy 2010-16: "Market Value = $726,000 +$19,000 $745,000"   (= sign, space in name)
# In all legacy cases I want the LAST (upcoming/next year) value!
MV_PATTERNS = [
    # Modern AT A GLANCE: "YYYY-YY Market Value:$N,NNN,NNN"
    re.compile(r"\d{4}-\d{2}\s+Market Value:\s*\$([\d]{1,3}(?:,\d{3})*)", re.I),
    # Legacy 2017-2019: spaces may or may not exist between values (fixes 2018)
    re.compile(r"MarketValue\s+\$([\d]{1,3}(?:,\d{3})*)\s*[+-]\$([\d]{1,3}(?:,\d{3})*)\s*\$([\d]{1,3}(?:,\d{3})*)", re.I),
    # Legacy 2010-2016: "Market Value = $CURRENT +/-$CHANGE $UPCOMING"
    re.compile(r"Market Value\s*=\s*\$([\d]{1,3}(?:,\d{3})*)\s*[+-]\$([\d]{1,3}(?:,\d{3})*)\s*\$([\d]{1,3}(?:,\d{3})*)", re.I),
    # Prose fallback: "market value for this property is $N"
    re.compile(r"\bmarket value for this property is\s*\$([\d,]+(?:\.\d+)?)", re.I),
    # Loose fallback (original)
    re.compile(r"\bmarket value\s*\$([\d,]+(?:\.\d+)?)", re.I),
]

# ASSESSED VALUE 
# Modern:      "YYYY-YY Assessed Value:$N,NNN,NNN"
# Legacy 2017: "ActualAssessedValue $1,057,500 +$386,550 $1,444,050" (spaces)
# Legacy 2018: "ActualAssessedValue $542,250+$85,500$627,750"         (no spaces)
# Legacy 2010: "Actual Assessed Value = $326,700 +$8,550 $335,250"   (= sign, spaces in name)
AV_PATTERNS = [
    # Modern AT A GLANCE
    re.compile(r"\d{4}-\d{2}\s+Assessed Value:\s*\$([\d]{1,3}(?:,\d{3})*)", re.I),
    # Legacy 2017-2019: optional spaces between values (fixes 2018)
    re.compile(r"ActualAssessedValue\s+\$([\d]{1,3}(?:,\d{3})*)\s*[+-]\$([\d]{1,3}(?:,\d{3})*)\s*\$([\d]{1,3}(?:,\d{3})*)", re.I),
    # Legacy 2010-2016: "Actual Assessed Value = $CURRENT +/-$CHANGE $UPCOMING"
    re.compile(r"Actual Assessed Value\s*=\s*\$([\d]{1,3}(?:,\d{3})*)\s*[+-]\$([\d]{1,3}(?:,\d{3})*)\s*\$([\d]{1,3}(?:,\d{3})*)", re.I),
    # Original fallbacks
    re.compile(r"\b\d{4}-\d{2}\s+assessed value:\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"\bactual assessed value\s*\$([\d,]+(?:\.\d+)?)\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"\bactual assessed value\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"\bassessed value\s*\$([\d,]+(?:\.\d+)?)", re.I),
]

# TAXABLE VALUE + ESTIMATED PROPERTY TAX 
# Modern run-on: "YYYY-YY$TAXABLE x RATE = $TAX"
# Legacy 2017:   "TaxableValue $CURRENT +/-$CHANGE $UPCOMING" (spaces)
# Legacy 2018:   "TaxableValue $CURRENT+/-$CHANGE$UPCOMING"   (no spaces)
# Legacy 2010:   "Taxable Value = $CURRENT +/-$CHANGE $UPCOMING"
TAXABLE_TAX_PATTERNS = [
    # Modern run-on table
    re.compile(
        r"\d{4}-\d{2}\$([\d]{1,3}(?:,\d{3})*)\s*x\s*[\d.]+\s*=\s*\$([\d,]+(?:\.\d+)?)",
        re.I,
    ),
    # Original spaced patterns
    re.compile(
        r"taxable value\s*\$([\d,]+(?:\.\d+)?)\s*x\s*[\d.]+\s*=\s*\$([\d,]+(?:\.\d+)?)",
        re.I,
    ),
    re.compile(
        r"\$([\d,]+(?:\.\d+)?)\s*x\s*0?\.\d+\s*=\s*\$([\d,]+(?:\.\d+)?)",
        re.I,
    ),
]

# Legacy taxable value only (no tax formula in legacy PDFs)
## This handles both 2017 (spaces) and 2018 (no spaces) and 2010 (= sign)
TAXABLE_LEGACY_PATTERNS = [
    # 2017-2019: optional spaces
    re.compile(
        r"TaxableValue\s+\$([\d]{1,3}(?:,\d{3})*)\s*[+-]\$([\d]{1,3}(?:,\d{3})*)\s*\$([\d]{1,3}(?:,\d{3})*)",
        re.I,
    ),
    # 2010-2016: "Taxable Value = $CURRENT +/-$CHANGE $UPCOMING"
    re.compile(
        r"Taxable Value\s*=\s*\$([\d]{1,3}(?:,\d{3})*)\s*[+-]\$([\d]{1,3}(?:,\d{3})*)\s*\$([\d]{1,3}(?:,\d{3})*)",
        re.I,
    ),
]

# GROSS INCOME
# 2017-2026 income approach: "Estimated Gross Income: $N"
# 2010 multiplier method:    "gross income at $N" (different phrasing)
GROSS_INCOME_PATTERNS = [
    re.compile(r"estimated gross income:\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"gross income:\s*\$([\d,]+(?:\.\d+)?)", re.I),
    # 2010 multiplier method phrasing
    re.compile(r"gross income at\s*\$([\d,]+(?:\.\d+)?)", re.I),
]

# EXPENSES
EXPENSES_PATTERNS = [
    re.compile(r"estimated expenses:\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"\bexpenses:\s*\$([\d,]+(?:\.\d+)?)", re.I),
]

# NET OPERATING INCOME
# Legacy spaced:  "net operating income of $402,977"
# Modern run-on:  "netoperatingincomeof$463,908"
NOI_PATTERNS = [
    re.compile(r"net operating income of\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"resulting in a net operating income of\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"net operating income:\s*\$([\d,]+(?:\.\d+)?)", re.I),
    re.compile(r"netoperatingincomeof\$([\d,]+(?:\.\d+)?)", re.I),
]

# BASE CAPITALIZATION RATE 
# Legacy run-on:  "BaseCapRate:Weusedacapitalizationrateof6.756%"
# Modern run-on:  "Basecapitalizationrate:Weusedacapitalizationrateof7.04%"
# Note: 2010 uses gross income multiplier — no cap rate exists in those PDFs.
BASE_CAP_PATTERNS = [
    re.compile(r"capitalizationrateof(\d{1,2}(?:\.\d+)?)%", re.I),
    re.compile(r"capitalization rate of\s+(\d{1,2}(?:\.\d+)?)%", re.I),
    re.compile(r"base (?:capitalization )?rate:\s*.*?(\d{1,2}(?:\.\d+)?)%", re.I),
    re.compile(r"used a capitalization rate of\s*(\d{1,2}(?:\.\d+)?)%", re.I),
]

# OVERALL CAPITALIZATION RATE 
OVERALL_CAP_PATTERNS = [
    re.compile(r"overallcapitalizationrateis(\d{1,2}(?:\.\d+)?)%", re.I),
    re.compile(r"overallcapitalization rate is (\d{1,2}(?:\.\d+)?)%", re.I),
    re.compile(r"overall capitalization rate.*?is\s*(\d{1,2}(?:\.\d+)?)%", re.I),
    re.compile(r"overall cap(?:italization)? rate.*?(\d{1,2}(?:\.\d+)?)%", re.I),
]



# Write output model
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

    market_value_source: str
    assessed_value_source: str
    taxable_value_source: str
    estimated_property_tax_source: str
    estimated_gross_income_source: str
    estimated_expenses_source: str
    net_operating_income_source: str
    base_cap_rate_percent_source: str
    overall_cap_rate_percent_source: str

    parse_status: str
    parse_notes: str
    text_preview: str


def parse_nopv_pdf(pdf_path: Path) -> NOPVRecord:
    bbl = pdf_path.parent.name
    stmt_date = pdf_path.stem.split("_")[0] if "_" in pdf_path.stem else "unknown"

    semantic = classify_pdf(pdf_path)

    if semantic.status == "no_data_found":
        return NOPVRecord(
            bbl=bbl, stmt_date=stmt_date, source_pdf_path=str(pdf_path),
            semantic_status="no_data_found", page_count=semantic.page_count,
            market_value=None, assessed_value=None, taxable_value=None,
            estimated_property_tax=None, estimated_gross_income=None,
            estimated_expenses=None, net_operating_income=None,
            base_cap_rate_percent=None, overall_cap_rate_percent=None,
            market_value_source="missing_in_document",
            assessed_value_source="missing_in_document",
            taxable_value_source="missing_in_document",
            estimated_property_tax_source="missing_in_document",
            estimated_gross_income_source="missing_in_document",
            estimated_expenses_source="missing_in_document",
            net_operating_income_source="missing_in_document",
            base_cap_rate_percent_source="missing_in_document",
            overall_cap_rate_percent_source="missing_in_document",
            parse_status="no_data_found", parse_notes="No data statement.",
            text_preview=semantic.text_preview,
        )

    try:
        raw_text, page_count = _extract_text(pdf_path)
    except Exception as e:
        return NOPVRecord(
            bbl=bbl, stmt_date=stmt_date, source_pdf_path=str(pdf_path),
            semantic_status="unreadable_pdf", page_count=0,
            market_value=None, assessed_value=None, taxable_value=None,
            estimated_property_tax=None, estimated_gross_income=None,
            estimated_expenses=None, net_operating_income=None,
            base_cap_rate_percent=None, overall_cap_rate_percent=None,
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
    market_value = None
    for p in MV_PATTERNS:
        m = p.search(text)
        if not m:
            continue
        # Multi-group patterns: last group is the upcoming/next-year value
        val = _to_int_money(m.group(m.lastindex))
        if val is not None:
            market_value = val
            market_value_source = "parsed_direct"
            break

    # assessed value 
    assessed_value = None
    for p in AV_PATTERNS:
        m = p.search(text)
        if not m:
            continue
        val = _to_int_money(m.group(m.lastindex))
        if val is not None:
            assessed_value = val
            assessed_value_source = "parsed_direct"
            break

    # taxable value + estimated property tax 
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

    # Legacy taxable value fallback (no tax formula in pre-2020 PDFs...)
    if taxable_value is None:
        for p in TAXABLE_LEGACY_PATTERNS:
            m = p.search(text)
            if m:
                val = _to_int_money(m.group(m.lastindex))
                if val is not None:
                    taxable_value = val
                    taxable_value_source = "parsed_direct"
                    break

    # gross income + expenses 
    gi_raw = _first_match(GROSS_INCOME_PATTERNS, text)
    ex_raw = _first_match(EXPENSES_PATTERNS, text)
    estimated_gross_income = _to_float_money(gi_raw) if gi_raw else None
    estimated_expenses = _to_float_money(ex_raw) if ex_raw else None
    if estimated_gross_income is not None:
        estimated_gross_income_source = "parsed_direct"
    if estimated_expenses is not None:
        estimated_expenses_source = "parsed_direct"

    # net operating income 
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

    # quality score
    core = [
        market_value, assessed_value, estimated_gross_income,
        estimated_expenses, net_operating_income, overall_cap_rate_percent,
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
        bbl=bbl, stmt_date=stmt_date, source_pdf_path=str(pdf_path),
        semantic_status="valid_statement", page_count=page_count,
        market_value=market_value, assessed_value=assessed_value,
        taxable_value=taxable_value, estimated_property_tax=estimated_property_tax,
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
        parse_status=parse_status, parse_notes=notes,
        text_preview=text[:320],
    )


def record_to_dict(record: NOPVRecord) -> dict:
    return asdict(record)