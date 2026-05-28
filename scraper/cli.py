##### cli.py #####
##### brdyknndy #####


from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import csv
import json
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import typer

from scraper.fetch.browser_gate import fetch_pdf_via_browser, is_valid_pdf_payload, check_2captcha_balance
from scraper.fetch.direct_http import fetch_pdf_direct
from scraper.parse.nopv_extract import parse_nopv_pdf, record_to_dict
from scraper.parse.pdf_classify import classify_pdf
from scraper.storage.files import meta_path, pdf_path, write_meta, write_pdf
from scraper.url_builder import build_nopv_url_plan

app = typer.Typer(help="NYC DOF NOPV scraper v2 CLI", no_args_is_help=True)

DEFAULT_YEAR_START = 2010
DEFAULT_YEAR_END = 2026
YEAR_TO_STMT_DATE = {
    2010: "20100115",
    2011: "20110115",
    2012: "20120115",
    2013: "20130115",
    2014: "20140115",
    2015: "20150115",
    2016: "20160115",
    2017: "20170115",
    2018: "20180115",
    2019: "20190115",
    2020: "20200115",
    2021: "20210115",
    2022: "20220115",
    2023: "20230115",
    2024: "20240115",
    2025: "20250115",
    2026: "20260116",
}


def _read_bbls_from_csv(path: Path) -> List[str]:
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "bbl" not in reader.fieldnames:
            raise ValueError(f"CSV must contain 'bbl' column, got: {reader.fieldnames}")
        out = []
        for row in reader:
            bbl = (row.get("bbl") or "").strip()
            if bbl:
                out.append(bbl)
        return out


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _expand_bbls_to_tasks(bbls: List[str], year_start: int, year_end: int) -> List[Tuple[str, str, int]]:
    tasks = []
    for bbl in bbls:
        for y in range(year_start, year_end + 1):
            stmt = YEAR_TO_STMT_DATE.get(y, f"{y}0115")
            tasks.append((bbl, stmt, y))
    return tasks


def _append_task_manifest_row(manifest_path: Path, row: dict) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = manifest_path.exists()
    fieldnames = [
        "run_started_utc",
        "bbl",
        "stmt_date",
        "year",
        "attempt",
        "result_code",
        "semantic_status",
        "status_label",
        "error_note",
    ]
    with manifest_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fieldnames})


def _archive_manifest_if_exists(manifest_path: Path) -> None:
    """If the manifest already exists, rename it with a timestamp suffix
    so this run starts fresh. Pure file-system operation, never raises fatally."""
    if not manifest_path.exists():
        return
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archived = manifest_path.with_name(f"{manifest_path.stem}_{ts}{manifest_path.suffix}")
        shutil.move(str(manifest_path), str(archived))
        typer.secho(f" Archived prior manifest → {archived.name}", fg=typer.colors.CYAN)
    except Exception as e:
        typer.secho(f"  Could not archive prior manifest ({e}); will append instead.", fg=typer.colors.YELLOW)


def _read_completed_tasks_from_manifest(manifest_path: Path) -> Set[Tuple[str, str]]:
    """Returns a set of (bbl, stmt_date) tuples that have a successful row in the manifest.
    Used by --resume mode. Returns empty set if the manifest does not exist or is unreadable."""
    if not manifest_path.exists():
        return set()
    completed: Set[Tuple[str, str]] = set()
    try:
        with manifest_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                label = (row.get("status_label") or "").strip()
                if label in {"success", "skipped", "retry_success"}:
                    bbl = (row.get("bbl") or "").strip()
                    sd = (row.get("stmt_date") or "").strip()
                    if bbl and sd:
                        completed.add((bbl, sd))
    except Exception as e:
        typer.secho(f"  Could not read manifest for resume ({e}); proceeding without resume.", fg=typer.colors.YELLOW)
        return set()
    return completed


@app.command("hello")
def hello() -> None:
    typer.echo("NOPV scraper v2 is set up !!!! :)")


@app.command("verify-pdf")
def verify_pdf(path: str = typer.Option(..., "--path")) -> None:
    p = Path(path)
    if not p.exists():
        typer.secho(f" File not found: {p}", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    b = p.read_bytes()
    looks_valid = is_valid_pdf_payload(b, content_type="")
    starts_pdf = b.startswith(b"%PDF")
    looks_html = b[:300].lower().startswith(b"<!doctype") or b[:300].lower().startswith(b"<html")

    typer.echo(f"File: {p}")
    typer.echo(f"Size bytes: {len(b)}")
    typer.echo(f"Starts with %PDF: {starts_pdf}")
    typer.echo(f"Looks like HTML: {looks_html}")
    typer.echo(f"is_valid_pdf_payload: {looks_valid}")

    raise typer.Exit(code=0 if looks_valid else 1)


@app.command("classify-pdf")
def classify_pdf_cmd(path: str = typer.Option(..., "--path")) -> None:
    p = Path(path)
    result = classify_pdf(p)

    typer.echo(f"File: {p}")
    typer.echo(f"Status: {result.status}")
    typer.echo(f"Page count: {result.page_count}")
    typer.echo(f"No-data matches: {result.matched_no_data_patterns}")
    typer.echo(f"NOPV matches: {result.matched_nopv_patterns}")
    typer.echo(f"Preview: {result.text_preview}")

    if result.status == "valid_statement":
        raise typer.Exit(code=0)
    elif result.status == "no_data_found":
        raise typer.Exit(code=3)
    raise typer.Exit(code=1)


def _scrape_one(
    bbl: str,
    stmt_date: str,
    headed: bool,
    force: bool,
    interactive_wait_ms: int,
    print_plan: bool = False,
) -> tuple[int, str]:
    try:
        plan = build_nopv_url_plan(bbl=bbl, stmt_date=stmt_date, stmt_type="NPV")
    except ValueError as e:
        typer.secho(f"Input error: {e}", fg=typer.colors.RED, err=True)
        return 2, "not_downloaded"

    if print_plan:
        typer.echo(json.dumps(asdict(plan), indent=2))

    pdf_file = pdf_path(plan.bbl, plan.stmt_date, plan.stmt_type)
    meta_file = meta_path(plan.bbl, plan.stmt_date, plan.stmt_type)

    direct_result = fetch_pdf_direct(plan.modern_url)
    if direct_result.ok and is_valid_pdf_payload(direct_result.pdf_bytes, direct_result.content_type):
        write_pdf(pdf_file, direct_result.pdf_bytes, force=force)
        semantic = classify_pdf(pdf_file)
        write_meta(meta_file, {
            "status": "success",
            "strategy_used": "direct_http",
            "bbl": plan.bbl,
            "stmt_date": plan.stmt_date,
            "year": plan.year,
            "url_attempted": plan.modern_url,
            "reason": direct_result.reason,
            "pdf_path": str(pdf_file),
            "semantic_status": semantic.status,
        })
        return 0, semantic.status

    browser_target_url = plan.legacy_url if plan.year < 2020 else plan.modern_url
    browser_result = fetch_pdf_via_browser(
        browser_target_url,
        headed=headed,
        interactive_wait_ms=interactive_wait_ms,
    )

    if browser_result.ok:
        write_pdf(pdf_file, browser_result.pdf_bytes, force=force)
        semantic = classify_pdf(pdf_file)
        write_meta(meta_file, {
            "status": "success",
            "strategy_used": "browser_gate",
            "bbl": plan.bbl,
            "stmt_date": plan.stmt_date,
            "year": plan.year,
            "url_attempted": browser_result.final_url or browser_target_url,
            "reason": browser_result.reason,
            "pdf_path": str(pdf_file),
            "semantic_status": semantic.status,
        })
        return 0, semantic.status

    write_meta(meta_file, {
        "status": "error",
        "strategy_used": "direct_then_browser_failed",
        "bbl": plan.bbl,
        "stmt_date": plan.stmt_date,
        "year": plan.year,
        "direct_reason": direct_result.reason,
        "browser_reason": browser_result.reason,
        "browser_final_url": browser_result.final_url,
        "semantic_status": "not_downloaded",
    })
    return 1, "not_downloaded"


@app.command("scrape-nopv")
def scrape_nopv(
    bbl: str = typer.Option(..., "--bbl"),
    stmt_date: str = typer.Option(..., "--stmt-date"),
    headed: bool = typer.Option(True, "--headed/--no-headed"),
    force: bool = typer.Option(False, "--force/--no-force"),
    print_plan: bool = typer.Option(False, "--print-plan/--no-print-plan"),
    interactive_wait_ms: int = typer.Option(30_000, "--interactive-wait-ms"),
) -> None:
    code, semantic = _scrape_one(
        bbl=bbl,
        stmt_date=stmt_date,
        headed=headed,
        force=force,
        interactive_wait_ms=interactive_wait_ms,
        print_plan=print_plan,
    )
    typer.echo(f"Final semantic status: {semantic}")
    raise typer.Exit(code=code)


@app.command("scrape-bbl-batch")
def scrape_bbl_batch(
    input_csv: str = typer.Option(..., "--input-csv", help="CSV with ONLY bbl column"),
    year_start: int = typer.Option(DEFAULT_YEAR_START, "--year-start"),
    year_end: int = typer.Option(DEFAULT_YEAR_END, "--year-end"),
    headed: bool = typer.Option(True, "--headed/--no-headed"),
    force: bool = typer.Option(False, "--force/--no-force"),
    limit_tasks: int = typer.Option(0, "--limit-tasks"),
    interactive_wait_ms: int = typer.Option(30_000, "--interactive-wait-ms"),
    max_retries: int = typer.Option(2, "--max-retries"),
    manifest_csv: str = typer.Option("data/processed/task_status.csv", "--manifest-csv"),
    throttle_ms: int = typer.Option(0, "--throttle-ms", help="Sleep N milliseconds between tasks (e.g. 1000 = 1 sec). Recommended for large runs."),
    resume: bool = typer.Option(False, "--resume/--no-resume", help="Skip tasks already marked success/skipped in the manifest."),
    archive_manifest: bool = typer.Option(False, "--archive-manifest/--no-archive-manifest", help="Rename existing manifest with timestamp before starting."),
) -> None:
    bbls = _dedupe_keep_order(_read_bbls_from_csv(Path(input_csv)))
    tasks = _expand_bbls_to_tasks(bbls, year_start, year_end)
    if limit_tasks > 0:
        tasks = tasks[:limit_tasks]

    manifest_path = Path(manifest_csv)

    # Optionally read prior completions for --resume BEFORE archiving!
    completed_keys: Set[Tuple[str, str]] = set()
    if resume:
        completed_keys = _read_completed_tasks_from_manifest(manifest_path)
        if completed_keys:
            typer.secho(f"↻ Resume mode: {len(completed_keys)} prior tasks will be skipped.", fg=typer.colors.CYAN)

    # Optionally archive an existing manifest so this run starts fresh
    if archive_manifest:
        _archive_manifest_if_exists(manifest_path)

    typer.secho(f"BBL count: {len(bbls)}", fg=typer.colors.GREEN)
    typer.secho(f"Expanded tasks: {len(tasks)}", fg=typer.colors.GREEN)
    typer.echo(f"Year range: {year_start}-{year_end}")

    # Check 2Captcha balance
    balance_start = check_2captcha_balance()

    success = failed = skipped = no_data_found = unreadable = resumed = 0
    run_started = datetime.now(timezone.utc)

    for idx, (bbl, stmt_date, year) in enumerate(tasks, start=1):
        typer.echo("\n" + "=" * 72)
        typer.echo(f"[{idx}/{len(tasks)}] bbl={bbl} year={year} stmt_date={stmt_date}")

        # Resume: skip if already completed in a prior run....
        if resume and (bbl, stmt_date) in completed_keys:
            resumed += 1
            typer.echo("↻ Already completed in a prior run. Skipping.")
            continue

        out_pdf = pdf_path(bbl, stmt_date, "NPV")
        if out_pdf.exists() and not force:
            b = out_pdf.read_bytes()
            if is_valid_pdf_payload(b, content_type=""):
                skipped += 1
                _append_task_manifest_row(manifest_path, {
                    "run_started_utc": run_started.isoformat(),
                    "bbl": bbl,
                    "stmt_date": stmt_date,
                    "year": year,
                    "attempt": 0,
                    "result_code": 0,
                    "semantic_status": "skipped_existing",
                    "status_label": "skipped",
                    "error_note": "",
                })
                continue

        last_code = 1
        last_semantic = "not_downloaded"

        task_start = time.time()
        for attempt in range(1, max_retries + 2):
            typer.echo(f"Attempt {attempt}/{max_retries + 1}")
            code, semantic = _scrape_one(
                bbl=bbl,
                stmt_date=stmt_date,
                headed=headed,
                force=force,
                interactive_wait_ms=interactive_wait_ms,
                print_plan=False,
            )
            last_code = code
            last_semantic = semantic
            if code == 0:
                break
        task_elapsed = time.time() - task_start
        typer.echo(f"  Task time: {task_elapsed:.1f}s")

        if last_code == 0:
            success += 1
            if last_semantic == "no_data_found":
                no_data_found += 1
            elif last_semantic in {"unreadable_pdf", "empty_text"}:
                unreadable += 1
            _append_task_manifest_row(manifest_path, {
                "run_started_utc": run_started.isoformat(),
                "bbl": bbl,
                "stmt_date": stmt_date,
                "year": year,
                "attempt": attempt,
                "result_code": 0,
                "semantic_status": last_semantic,
                "status_label": "success",
                "error_note": "",
            })
        else:
            failed += 1
            _append_task_manifest_row(manifest_path, {
                "run_started_utc": run_started.isoformat(),
                "bbl": bbl,
                "stmt_date": stmt_date,
                "year": year,
                "attempt": attempt,
                "result_code": last_code,
                "semantic_status": last_semantic,
                "status_label": "failed",
                "error_note": f"code={last_code}, semantic={last_semantic}",
            })

        # Throttle between tasks (if requested)
        if throttle_ms > 0 and idx < len(tasks):
            time.sleep(throttle_ms / 1000.0)

    run_ended = datetime.now(timezone.utc)
    elapsed_total = (run_ended - run_started).total_seconds()

    typer.echo("\n" + "=" * 72)
    typer.secho("BBL batch complete", fg=typer.colors.GREEN)
    typer.echo(f"Started (UTC):  {run_started.isoformat()}")
    typer.echo(f"Ended   (UTC):  {run_ended.isoformat()}")
    typer.echo(f"Wall time:      {elapsed_total/60:.1f} min ({elapsed_total:.0f}s)")
    typer.echo(f"Task total:     {len(tasks)}")
    typer.echo(f"Success:        {success}")
    typer.echo(f"  ├─ no_data_found:    {no_data_found}")
    typer.echo(f"  └─ unreadable/empty: {unreadable}")
    typer.echo(f"Skipped (file): {skipped}")
    typer.echo(f"Resumed (skip): {resumed}")
    typer.echo(f"Failed:         {failed}")
    typer.echo(f"Manifest:       {manifest_path}")

    # End-of-run balance check
    balance_end = check_2captcha_balance()
    if balance_start is not None and balance_end is not None:
        spent = balance_start - balance_end
        typer.echo(f"2Captcha spent: ${spent:.4f}")

    raise typer.Exit(code=1 if failed > 0 else 0)


@app.command("retry-failures")
def retry_failures(
    manifest_csv: str = typer.Option("data/processed/task_status.csv", "--manifest-csv"),
    headed: bool = typer.Option(True, "--headed/--no-headed"),
    force: bool = typer.Option(False, "--force/--no-force"),
    interactive_wait_ms: int = typer.Option(30_000, "--interactive-wait-ms"),
    max_retries: int = typer.Option(2, "--max-retries"),
) -> None:
    manifest_path = Path(manifest_csv)
    if not manifest_path.exists():
        typer.secho(f" Manifest not found: {manifest_path}", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    rows = list(csv.DictReader(manifest_path.open("r", encoding="utf-8", newline="")))
    failed_tasks = []
    for r in rows:
        if r.get("status_label") == "failed":
            failed_tasks.append((r["bbl"], r["stmt_date"], int(r["year"])))

    seen = set()
    unique_failed = []
    for t in failed_tasks:
        if t not in seen:
            seen.add(t)
            unique_failed.append(t)

    if not unique_failed:
        typer.secho("No failed tasks to retry.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    typer.secho(f"Retrying {len(unique_failed)} failed tasks", fg=typer.colors.YELLOW)
    success = failed = 0
    run_started = datetime.now(timezone.utc)

    for idx, (bbl, stmt_date, year) in enumerate(unique_failed, start=1):
        typer.echo(f"[retry {idx}/{len(unique_failed)}] {bbl} {stmt_date}")
        last_code = 1
        last_semantic = "not_downloaded"

        for attempt in range(1, max_retries + 2):
            code, semantic = _scrape_one(
                bbl=bbl,
                stmt_date=stmt_date,
                headed=headed,
                force=force,
                interactive_wait_ms=interactive_wait_ms,
                print_plan=False,
            )
            last_code = code
            last_semantic = semantic
            if code == 0:
                break

        if last_code == 0:
            success += 1
            _append_task_manifest_row(manifest_path, {
                "run_started_utc": run_started.isoformat(),
                "bbl": bbl,
                "stmt_date": stmt_date,
                "year": year,
                "attempt": attempt,
                "result_code": 0,
                "semantic_status": last_semantic,
                "status_label": "retry_success",
                "error_note": "",
            })
        else:
            failed += 1
            _append_task_manifest_row(manifest_path, {
                "run_started_utc": run_started.isoformat(),
                "bbl": bbl,
                "stmt_date": stmt_date,
                "year": year,
                "attempt": attempt,
                "result_code": last_code,
                "semantic_status": last_semantic,
                "status_label": "retry_failed",
                "error_note": f"code={last_code}, semantic={last_semantic}",
            })

    typer.secho("Retry run complete", fg=typer.colors.GREEN)
    typer.echo(f"retry_success={success}")
    typer.echo(f"retry_failed={failed}")
    raise typer.Exit(code=1 if failed > 0 else 0)


@app.command("parse-nopv")
def parse_nopv_cmd(pdf_path_arg: str = typer.Option(..., "--pdf-path")) -> None:
    p = Path(pdf_path_arg)
    if not p.exists():
        typer.secho(f"File not found: {p}", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    record = parse_nopv_pdf(p)
    typer.echo(json.dumps(record_to_dict(record), indent=2))

    if record.parse_status == "ok":
        raise typer.Exit(code=0)
    elif record.parse_status == "no_data_found":
        raise typer.Exit(code=3)
    elif record.parse_status == "partial":
        raise typer.Exit(code=4)
    else:
        raise typer.Exit(code=1)


@app.command("parse-batch")
def parse_batch_cmd(
    raw_root: str = typer.Option("data/raw", "--raw-root"),
    out_dir: str = typer.Option("data/processed", "--out-dir"),
    limit: int = typer.Option(0, "--limit"),
) -> None:
    raw = Path(raw_root)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    all_pdfs = sorted(raw.glob("*/*_NPV.pdf"))
    if limit > 0:
        all_pdfs = all_pdfs[:limit]

    if not all_pdfs:
        typer.secho("No PDF files found to parse.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    records_file = out / "nopv_records.jsonl"
    no_data_file = out / "nopv_no_data_found.jsonl"
    errors_file = out / "nopv_parse_errors.jsonl"

    ok_count = partial_count = no_data_count = failed_count = 0

    with records_file.open("w", encoding="utf-8") as f_records, \
         no_data_file.open("w", encoding="utf-8") as f_no_data, \
         errors_file.open("w", encoding="utf-8") as f_errors:

        for i, pdf_path in enumerate(all_pdfs, start=1):
            typer.echo(f"[{i}/{len(all_pdfs)}] Parsing {pdf_path}")
            rec = parse_nopv_pdf(pdf_path)
            row = record_to_dict(rec)

            if rec.parse_status == "ok":
                f_records.write(json.dumps(row) + "\n")
                ok_count += 1
            elif rec.parse_status == "partial":
                f_records.write(json.dumps(row) + "\n")
                partial_count += 1
            elif rec.parse_status == "no_data_found":
                f_no_data.write(json.dumps(row) + "\n")
                no_data_count += 1
            else:
                f_errors.write(json.dumps(row) + "\n")
                failed_count += 1

    typer.secho("\nParse batch complete", fg=typer.colors.GREEN)
    typer.echo(f"Total parsed: {len(all_pdfs)}")
    typer.echo(f"OK:          {ok_count}")
    typer.echo(f"Partial:     {partial_count}")
    typer.echo(f"No data:     {no_data_count}")
    typer.echo(f"Failed:      {failed_count}")
    typer.echo(f"Records:     {records_file}")
    typer.echo(f"No-data:     {no_data_file}")
    typer.echo(f"Errors:      {errors_file}")

    raise typer.Exit(code=1 if failed_count > 0 else 0)


@app.command("build-dataset-csv")
def build_dataset_csv(
    records_jsonl: str = typer.Option("data/processed/nopv_records.jsonl", "--records-jsonl"),
    no_data_jsonl: str = typer.Option("data/processed/nopv_no_data_found.jsonl", "--no-data-jsonl"),
    out_csv: str = typer.Option("data/processed/nopv_financials.csv", "--out-csv"),
) -> None:
    records_path = Path(records_jsonl)
    no_data_path = Path(no_data_jsonl)
    out_path = Path(out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    rows = _load_jsonl(records_path) + _load_jsonl(no_data_path)
    for r in rows:
        sd = str(r.get("stmt_date") or "")
        r["tax_year"] = int(sd[:4]) if len(sd) >= 4 and sd[:4].isdigit() else None

    cols = [
        "bbl", "tax_year", "stmt_date", "semantic_status", "parse_status",
        "market_value", "assessed_value", "taxable_value", "estimated_property_tax",
        "estimated_gross_income", "estimated_expenses", "net_operating_income",
        "base_cap_rate_percent", "overall_cap_rate_percent",
        "market_value_source", "assessed_value_source", "taxable_value_source",
        "estimated_property_tax_source", "estimated_gross_income_source",
        "estimated_expenses_source", "net_operating_income_source",
        "base_cap_rate_percent_source", "overall_cap_rate_percent_source",
        "source_pdf_path", "parse_notes",
    ]

    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c) for c in cols})

    typer.secho(f"Wrote dataset CSV: {out_path}", fg=typer.colors.GREEN)
    typer.echo(f"Rows: {len(rows)}")


if __name__ == "__main__":
    app()
