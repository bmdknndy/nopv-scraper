from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Tuple

from scraper.config import YEAR_TO_STMT_DATE


def read_bbls_from_csv(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "bbl" not in reader.fieldnames:
            raise ValueError(f"CSV must contain a 'bbl' column. Got: {reader.fieldnames}")

        out: List[str] = []
        for row in reader:
            bbl = (row.get("bbl") or "").strip()
            if bbl:
                out.append(bbl)
    return out


def dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def expand_bbls_to_tasks(
    bbls: List[str],
    year_start: int,
    year_end: int,
    year_to_stmt_date: Dict[int, str] | None = None
) -> List[Tuple[str, str, int]]:
    if year_to_stmt_date is None:
        year_to_stmt_date = YEAR_TO_STMT_DATE

    tasks: List[Tuple[str, str, int]] = []
    for bbl in bbls:
        for year in range(year_start, year_end + 1):
            stmt_date = year_to_stmt_date.get(year)
            if not stmt_date:
                # fallback: Jan 15 convention
                stmt_date = f"{year}0115"
            tasks.append((bbl, stmt_date, year))
    return tasks


def write_jsonl_rows(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")