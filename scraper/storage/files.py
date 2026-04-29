from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


DATA_ROOT = Path("data/raw")


def ensure_bbl_dir(bbl: str) -> Path:
    bbl_dir = DATA_ROOT / bbl
    bbl_dir.mkdir(parents=True, exist_ok=True)
    return bbl_dir


def pdf_path(bbl: str, stmt_date: str, stmt_type: str = "NPV") -> Path:
    return ensure_bbl_dir(bbl) / f"{stmt_date}_{stmt_type}.pdf"


def meta_path(bbl: str, stmt_date: str, stmt_type: str = "NPV") -> Path:
    return ensure_bbl_dir(bbl) / f"{stmt_date}_{stmt_type}.meta.json"


def write_pdf(path: Path, pdf_bytes: bytes, force: bool = False) -> None:
    if path.exists() and not force:
        return
    path.write_bytes(pdf_bytes)


def write_meta(path: Path, meta: Dict[str, Any]) -> None:
    meta = dict(meta)
    meta["written_at_utc"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(meta, indent=2), encoding="utf-8")