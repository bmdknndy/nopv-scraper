from __future__ import annotations

# Default scrape year range
DEFAULT_YEAR_START = 2017
DEFAULT_YEAR_END = 2026

# Default statement date mapping by tax year.
# Most years use Jan 15; 2026 is Jan 16 in your observed data.
YEAR_TO_STMT_DATE = {
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