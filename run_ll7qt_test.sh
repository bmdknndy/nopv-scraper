#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# Test run for LL7QT BBLs: scrape 50 tasks → parse → build CSV
# Usage: ./run_ll7qt_test.sh /path/to/ll7qt_bbls.csv
# ─────────────────────────────────────────────────────────────────────────

set -e  # Exit immediately if any command fails

# Where your input CSV lives. Pass as first argument, or hardcode below.
INPUT_CSV="/Users/bradykennedy/Documents/GitHub/rr-distress-nyc/data/interim/ll7qt_bbls.csv"

# Output manifest for this test run (kept separate from prior runs)
MANIFEST="data/processed/ll7qt_test_manifest.csv"

# Dataset output
DATASET_CSV="data/processed/ll7qt_test_financials.csv"

echo "================================================================"
echo "  LL7QT TEST RUN"
echo "================================================================"
echo "  Input CSV:  $INPUT_CSV"
echo "  Manifest:   $MANIFEST"
echo "  Dataset:    $DATASET_CSV"
echo "================================================================"
echo ""

# Verify the input CSV exists before starting
if [ ! -f "$INPUT_CSV" ]; then
    echo "❌ Input CSV not found at: $INPUT_CSV"
    echo "   Pass the correct path as the first argument, e.g.:"
    echo "   ./run_ll7qt_test.sh /Users/yourname/path/to/ll7qt_bbls.csv"
    exit 1
fi

# Step 1: Scrape PDFs (max 50 tasks, headed so you can solve any failures, force=true to overwrite, max 3 attempts)
echo "▶  Step 1/3: Scraping..."
python -m scraper.cli scrape-bbl-batch \
    --input-csv "$INPUT_CSV" \
    --year-start 2017 \
    --year-end 2026 \
    --limit-tasks 50 \
    --headed \
    --force \
    --max-retries 2 \
    --interactive-wait-ms 90000 \
    --manifest-csv "$MANIFEST"

# Step 2: Parse all downloaded PDFs into JSONL
echo ""
echo "▶  Step 2/3: Parsing PDFs..."
python -m scraper.cli parse-batch

# Step 3: Build the final CSV from the parsed JSONL
echo ""
echo "▶  Step 3/3: Building final CSV..."
python -m scraper.cli build-dataset-csv --out-csv "$DATASET_CSV"

echo ""
echo "================================================================"
echo "✅ Done!"
echo "   Manifest:  $MANIFEST"
echo "   Dataset:   $DATASET_CSV"
echo "================================================================"