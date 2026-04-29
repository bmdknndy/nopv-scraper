from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode


MODERN_BASE = (
    "https://a836-edms.nyc.gov/dctm-rest/repositories/"
    "dofedmspts/StatementSearch"
)

LEGACY_BASE = "https://a836-mspuvw-dofptsz.nyc.gov/PTSCM/StatementSearch"


@dataclass(frozen=True)
class NOPVUrlPlan:
    bbl: str
    stmt_date: str
    stmt_type: str
    year: int
    preferred_strategy: str  # "direct_http" or "browser_gate"
    modern_url: str
    legacy_url: str


def _validate_bbl(bbl: str) -> str:
    bbl = bbl.strip()
    if not (bbl.isdigit() and len(bbl) == 10):
        raise ValueError("BBL must be exactly 10 digits, e.g. 1012530021")
    return bbl


def _validate_stmt_date(stmt_date: str) -> str:
    stmt_date = stmt_date.strip()
    if not (stmt_date.isdigit() and len(stmt_date) == 8):
        raise ValueError("stmt_date must be YYYYMMDD, e.g. 20260116")
    year = int(stmt_date[:4])
    month = int(stmt_date[4:6])
    day = int(stmt_date[6:8])

    if year < 1900 or year > 2100:
        raise ValueError(f"stmt_date year looks invalid: {year}")
    if month < 1 or month > 12:
        raise ValueError(f"stmt_date month looks invalid: {month}")
    if day < 1 or day > 31:
        raise ValueError(f"stmt_date day looks invalid: {day}")
    return stmt_date


def _build_url(base: str, bbl: str, stmt_date: str, stmt_type: str) -> str:
    params = {
        "bbl": bbl,
        "stmtDate": stmt_date,
        "stmtType": stmt_type,
    }
    return f"{base}?{urlencode(params)}"


def build_nopv_url_plan(
    bbl: str,
    stmt_date: str,
    stmt_type: str = "NPV",
) -> NOPVUrlPlan:
    """
    Build both modern and legacy candidate URLs for a NOPV/statement request
    and return strategy recommendation based on year.
    """
    bbl = _validate_bbl(bbl)
    stmt_date = _validate_stmt_date(stmt_date)
    stmt_type = stmt_type.strip().upper()

    year = int(stmt_date[:4])
    preferred_strategy = "direct_http" if year >= 2020 else "browser_gate"

    modern_url = _build_url(MODERN_BASE, bbl, stmt_date, stmt_type)
    legacy_url = _build_url(LEGACY_BASE, bbl, stmt_date, stmt_type)

    return NOPVUrlPlan(
        bbl=bbl,
        stmt_date=stmt_date,
        stmt_type=stmt_type,
        year=year,
        preferred_strategy=preferred_strategy,
        modern_url=modern_url,
        legacy_url=legacy_url,
    )