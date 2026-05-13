# nopv-scraper

A Python CLI tool for scraping NYC Department of Finance "Notice of Property Value" (NOPV) PDFs and extracting the financial data inside them: assessed value, market value, tax class, cap rates, etc.

Built to handle the DOF's CAPTCHA-gated document portal. Works by trying a fast direct HTTP request first, then falling back to a headed Playwright browser session with optional 2Captcha auto-solve if a challenge appears.

---

## What it does

1. Takes a list of BBLs (Borough-Block-Lot identifiers) and a year range
2. Fetches the NOPV PDF for each BBL × year combination from NYC's DOF portal
3. Classifies each PDF (valid statement, no data found, unreadable)
4. Parses the financial fields out of the text
5. Outputs a clean CSV with one row per BBL per year

---

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

If you want automatic CAPTCHA solving, set your 2Captcha API key:

```bash
export TWOCAPTCHA_API_KEY=your_key_here
```

If the key isn't set, the browser will just open and wait for you to solve it manually.

---

## Usage

**Scrape a single BBL:**
```bash
python -m scraper.cli scrape-nopv --bbl 1012530021 --stmt-date 20260116
```

**Run a batch from a CSV file:**
```bash
python -m scraper.cli scrape-bbl-batch --input-csv runme/your_bbls.csv --year-start 2015 --year-end 2026
```

Your input CSV just needs a `bbl` column. PDFs are saved to `data/raw/<bbl>/`.

**Resume an interrupted run:**
```bash
python -m scraper.cli scrape-bbl-batch --input-csv runme/your_bbls.csv --resume
```

**Parse downloaded PDFs into structured data:**
```bash
python -m scraper.cli parse-batch
```

**Build the final dataset CSV:**
```bash
python -m scraper.cli build-dataset-csv
```

---

## Outputs

`data/raw/<bbl>/<date>_NPV.pdf` = Raw PDF for each BBL/year 
`data/raw/<bbl>/<date>_NPV.meta.json` = Fetch metadata (strategy used, status, URL) 
`data/processed/nopv_records.jsonl` = Parsed financial records 
`data/processed/nopv_no_data_found.jsonl` = BBL/years with no statement on file 
`data/processed/nopv_financials.csv` = Final merged dataset 

---

## Project structure

```
scraper/
  cli.py              # All CLI commands (entry point)
  url_builder.py      # Builds modern + legacy DOF URLs from BBL/date
  fetch/
    browser_gate.py   # Playwright browser fetcher with 2Captcha + manual fallback
    direct_http.py    # Fast direct HTTP attempt (works for most recent years)
  parse/
    pdf_classify.py   # Classifies PDFs: valid statement vs. no data vs. unreadable
    nopv_extract.py   # Extracts financial fields from PDF text
  storage/
    files.py          # Handles file paths and writing PDFs/metadata
```

---

## Notes

- I've tested this on NYC DOF data from 2010–2026
- The DOF portal has two URL formats (modern and legacy); the scraper tries both.
- `state/session.json` stores your browser session between runs so you don't have to re-authenticate each time; keep this file local, don't commit it!
