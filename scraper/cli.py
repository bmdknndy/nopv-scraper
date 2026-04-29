from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import typer

from scraper.fetch.browser_gate import fetch_pdf_via_browser, is_valid_pdf_payload
from scraper.fetch.direct_http import fetch_pdf_direct
from scraper.parse.nopv_extract import parse_nopv_pdf, record_to_dict
from scraper.parse.pdf_classify import classify_pdf
from scraper.storage.files import meta_path, pdf_path, write_meta, write_pdf
from scraper.url_builder import build_nopv_url_plan

app = typer.Typer(help="NYC DOF NOPV scraper v2 CLI", no_args_is_help=True)


@app.command("hello")
def hello() -> None:
    typer.echo("NOPV scraper v2 is set up.")


@app.command("verify-pdf")
def verify_pdf(
    path: str = typer.Option(..., "--path", help="Path to a downloaded PDF to validate")
) -> None:
    p = Path(path)
    if not p.exists():
        typer.secho(f"❌ File not found: {p}", fg=typer.colors.RED)
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

    if looks_valid:
        typer.secho("✅ PDF validation passed.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    typer.secho("❌ Invalid PDF payload.", fg=typer.colors.RED)
    raise typer.Exit(code=1)


@app.command("classify-pdf")
def classify_pdf_cmd(
    path: str = typer.Option(..., "--path", help="Path to downloaded PDF")
) -> None:
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
    else:
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

    typer.secho("✅ Input validated", fg=typer.colors.GREEN)
    typer.echo(f"BBL: {plan.bbl}")
    typer.echo(f"Statement date: {plan.stmt_date}")
    typer.echo(f"Year: {plan.year}")
    typer.echo(f"Preferred strategy: {plan.preferred_strategy}")
    typer.echo(f"Headed mode: {headed}")
    typer.echo(f"Force overwrite: {force}")

    typer.echo("\nCandidate URLs:")
    typer.echo(f"- modern: {plan.modern_url}")
    typer.echo(f"- legacy: {plan.legacy_url}")

    if print_plan:
        typer.echo("\nPlan JSON:")
        typer.echo(json.dumps(asdict(plan), indent=2))

    pdf_file = pdf_path(plan.bbl, plan.stmt_date, plan.stmt_type)
    meta_file = meta_path(plan.bbl, plan.stmt_date, plan.stmt_type)

    typer.echo("\n🔎 Attempting direct HTTP fetch (modern URL)...")
    direct_result = fetch_pdf_direct(plan.modern_url)

    if direct_result.ok and is_valid_pdf_payload(direct_result.pdf_bytes, direct_result.content_type):
        write_pdf(pdf_file, direct_result.pdf_bytes, force=force)
        semantic = classify_pdf(pdf_file)

        write_meta(meta_file, {
            "status": "success",
            "strategy_used": "direct_http",
            "bbl": plan.bbl,
            "stmt_date": plan.stmt_date,
            "stmt_type": plan.stmt_type,
            "year": plan.year,
            "url_attempted": plan.modern_url,
            "legacy_url": plan.legacy_url,
            "http_status": direct_result.status_code,
            "content_type": direct_result.content_type,
            "reason": direct_result.reason,
            "pdf_path": str(pdf_file),
            "headed_requested": headed,
            "force": force,
            "semantic_status": semantic.status,
            "semantic_no_data_patterns": semantic.matched_no_data_patterns,
            "semantic_nopv_patterns": semantic.matched_nopv_patterns,
            "semantic_page_count": semantic.page_count,
            "semantic_preview": semantic.text_preview,
        })

        typer.secho("\n✅ Download succeeded via direct HTTP.", fg=typer.colors.GREEN)
        typer.echo(f"Saved PDF: {pdf_file}")
        typer.echo(f"Saved metadata: {meta_file}")
        typer.echo(f"Semantic classification: {semantic.status}")
        return 0, semantic.status

    typer.secho("\n⚠️ Direct fetch not usable; trying browser strategy.", fg=typer.colors.YELLOW)
    typer.echo(f"Direct reason: {direct_result.reason}")

    browser_target_url = plan.legacy_url if plan.year < 2020 else plan.modern_url
    typer.echo("🌐 Browser fetch starting...")
    typer.echo(f"Target URL: {browser_target_url}")
    typer.echo("If challenge appears, solve it in the browser window.")

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
            "stmt_type": plan.stmt_type,
            "year": plan.year,
            "modern_url": plan.modern_url,
            "legacy_url": plan.legacy_url,
            "direct_reason": direct_result.reason,
            "url_attempted": browser_result.final_url or browser_target_url,
            "content_type": browser_result.content_type,
            "browser_reason": browser_result.reason,
            "pdf_path": str(pdf_file),
            "headed_requested": headed,
            "force": force,
            "semantic_status": semantic.status,
            "semantic_no_data_patterns": semantic.matched_no_data_patterns,
            "semantic_nopv_patterns": semantic.matched_nopv_patterns,
            "semantic_page_count": semantic.page_count,
            "semantic_preview": semantic.text_preview,
        })

        typer.secho("\n✅ Download succeeded via browser fallback.", fg=typer.colors.GREEN)
        typer.echo(f"Saved PDF: {pdf_file}")
        typer.echo(f"Saved metadata: {meta_file}")
        typer.echo(f"Semantic classification: {semantic.status}")
        return 0, semantic.status

    write_meta(meta_file, {
        "status": "error",
        "strategy_used": "direct_then_browser_failed",
        "bbl": plan.bbl,
        "stmt_date": plan.stmt_date,
        "stmt_type": plan.stmt_type,
        "year": plan.year,
        "modern_url": plan.modern_url,
        "legacy_url": plan.legacy_url,
        "direct_reason": direct_result.reason,
        "direct_http_status": direct_result.status_code,
        "browser_reason": browser_result.reason,
        "browser_final_url": browser_result.final_url,
        "headed_requested": headed,
        "force": force,
        "semantic_status": "not_downloaded",
    })

    typer.secho("\n❌ Browser fallback failed.", fg=typer.colors.RED)
    typer.echo(f"Reason: {browser_result.reason}")
    typer.echo(f"Metadata saved: {meta_file}")
    return 1, "not_downloaded"


@app.command("scrape-nopv")
def scrape_nopv(
    bbl: str = typer.Option(..., "--bbl", help="10-digit BBL, e.g. 1012530021"),
    stmt_date: str = typer.Option(..., "--stmt-date", help="YYYYMMDD, e.g. 20260116"),
    headed: bool = typer.Option(True, "--headed/--no-headed", help="Run browser headed for challenge workflows"),
    force: bool = typer.Option(False, "--force/--no-force", help="Overwrite existing files"),
    print_plan: bool = typer.Option(False, "--print-plan/--no-print-plan", help="Print computed URL plan"),
    interactive_wait_ms: int = typer.Option(30_000, "--interactive-wait-ms", help="Wait window for manual challenge solve"),
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


@app.command("scrape-batch")
def scrape_batch(
    input_csv: str = typer.Option(..., "--input-csv", help="CSV with columns: bbl,stmt_date"),
    headed: bool = typer.Option(True, "--headed/--no-headed", help="Use visible browser for challenge flow"),
    force: bool = typer.Option(False, "--force/--no-force", help="Overwrite existing files"),
    limit: int = typer.Option(0, "--limit", help="Process first N rows only (0 = all)"),
    interactive_wait_ms: int = typer.Option(30_000, "--interactive-wait-ms", help="Wait window for manual challenge solve"),
    print_plan: bool = typer.Option(False, "--print-plan/--no-print-plan", help="Print plan for each row"),
) -> None:
    in_path = Path(input_csv)
    if not in_path.exists():
        typer.secho(f"❌ Input CSV not found: {in_path}", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    rows = []
    with in_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        expected = {"bbl", "stmt_date"}
        if not reader.fieldnames or not expected.issubset(set(reader.fieldnames)):
            typer.secho(
                f"❌ CSV must contain headers: bbl,stmt_date (got: {reader.fieldnames})",
                fg=typer.colors.RED,
            )
            raise typer.Exit(code=2)

        for r in reader:
            bbl = (r.get("bbl") or "").strip()
            stmt_date = (r.get("stmt_date") or "").strip()
            if bbl and stmt_date:
                rows.append({"bbl": bbl, "stmt_date": stmt_date})

    if limit > 0:
        rows = rows[:limit]

    total = len(rows)
    typer.echo(f"Loaded {total} rows from {in_path}")
    if total == 0:
        typer.secho("⚠️ No rows to process.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    success = 0
    failed = 0
    skipped = 0
    no_data_found = 0
    unreadable = 0
    run_started = datetime.now(timezone.utc)

    for idx, row in enumerate(rows, start=1):
        bbl = row["bbl"]
        stmt_date = row["stmt_date"]

        typer.echo("\n" + "=" * 72)
        typer.echo(f"[{idx}/{total}] bbl={bbl} stmt_date={stmt_date}")

        out_pdf = pdf_path(bbl, stmt_date, "NPV")
        if out_pdf.exists() and not force:
            b = out_pdf.read_bytes()
            if is_valid_pdf_payload(b, content_type=""):
                typer.secho(f"⏭️  Skipping existing valid PDF: {out_pdf}", fg=typer.colors.BLUE)
                skipped += 1
                continue

        code, semantic = _scrape_one(
            bbl=bbl,
            stmt_date=stmt_date,
            headed=headed,
            force=force,
            interactive_wait_ms=interactive_wait_ms,
            print_plan=print_plan,
        )

        if code == 0:
            success += 1
            if semantic == "no_data_found":
                no_data_found += 1
            elif semantic in {"unreadable_pdf", "empty_text"}:
                unreadable += 1
        else:
            failed += 1

    run_ended = datetime.now(timezone.utc)

    typer.echo("\n" + "=" * 72)
    typer.secho("Batch complete", fg=typer.colors.GREEN)
    typer.echo(f"Started (UTC): {run_started.isoformat()}")
    typer.echo(f"Ended   (UTC): {run_ended.isoformat()}")
    typer.echo(f"Total:   {total}")
    typer.echo(f"Success (downloaded): {success}")
    typer.echo(f"  ├─ no_data_found:   {no_data_found}")
    typer.echo(f"  └─ unreadable/empty:{unreadable}")
    typer.echo(f"Skipped: {skipped}")
    typer.echo(f"Failed:  {failed}")

    raise typer.Exit(code=1 if failed > 0 else 0)


@app.command("parse-nopv")
def parse_nopv_cmd(
    pdf_path_arg: str = typer.Option(..., "--pdf-path", help="Path to one downloaded NOPV PDF"),
) -> None:
    p = Path(pdf_path_arg)
    if not p.exists():
        typer.secho(f"❌ File not found: {p}", fg=typer.colors.RED)
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
    raw_root: str = typer.Option("data/raw", "--raw-root", help="Root directory containing downloaded PDFs"),
    out_dir: str = typer.Option("data/processed", "--out-dir", help="Output directory for parsed JSONL files"),
    limit: int = typer.Option(0, "--limit", help="Process first N PDF files only (0 = all)"),
) -> None:
    raw = Path(raw_root)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    all_pdfs = sorted(raw.glob("*/*_NPV.pdf"))
    if limit > 0:
        all_pdfs = all_pdfs[:limit]

    if not all_pdfs:
        typer.secho("⚠️ No PDF files found to parse.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=0)

    records_file = out / "nopv_records.jsonl"
    no_data_file = out / "nopv_no_data_found.jsonl"
    errors_file = out / "nopv_parse_errors.jsonl"

    ok_count = 0
    partial_count = 0
    no_data_count = 0
    failed_count = 0

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


if __name__ == "__main__":
    app()