"""Shiller's long US series (section 5.1): monthly equity index and long
rates since 1871 — the resolution step between JST's annual and yfinance's
daily. Rows land in `market_observations` with frequency='monthly'.

The pack already models the join: ^GSPC and DGS10 carry era-keyed native
frequencies whose monthly era STARTS in 1871 — these rows are that era.
Levels, not returns: P is the composite price and GS10 a yield, so the
transmission engine differences them exactly as it differences daily closes.

THE DATE COLUMN IS A FLOAT TRAP. Shiller writes 1880.1 for October (and
1880.01 for January): read as a float, October loses its zero. Parsed as
round(date * 100) % 100 both survive. The sheet also ends in prose footnotes;
non-numeric rows are dropped and counted, never coerced.

Free download (ie_data.xls). PHASE 3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SOURCE_SHILLER = "source:shiller"

#: Sheet layout: the data sheet, seven header rows, then columns of which
#: this loader reads Date, P (S&P composite level) and Rate GS10.
_SHEET = "Data"
_SKIP_ROWS = 7

#: Shiller column → (panel ticker, what the value is).
_SERIES: dict[str, str] = {
    "P": "^GSPC",
    "Rate GS10": "DGS10",
}


def parse_monthly(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Shiller data rows → panel observation rows. Pure, so the parse is
    testable without the workbook. Returns (observations, dropped)."""
    out: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        try:
            stamp = round(float(row["Date"]) * 100)
            year, month = stamp // 100, stamp % 100
            if not 1 <= month <= 12:
                raise ValueError(f"month {month}")
        except (KeyError, TypeError, ValueError):
            dropped += 1  # the footnote rows at the sheet's tail land here
            continue
        obs_date = f"{year}-{month:02d}-01"
        for column, ticker in _SERIES.items():
            try:
                value = float(row[column])
            except (KeyError, TypeError, ValueError):
                dropped += 1
                continue
            out.append({
                "market_ticker": ticker, "obs_date": obs_date,
                "frequency": "monthly", "value": value, "close": value,
                "source_ref": SOURCE_SHILLER,
            })
    return out, dropped


def load_monthly(panel_conn: Any, xls_path: Path) -> int:
    """Shiller monthly rows → market_observations (monthly, US).

    Imported lazily: pandas + xlrd live in the ingest extra, and the
    deterministic layers must import this module without them.
    """
    import pandas as pd

    from core.panel import pg_store

    frame = pd.read_excel(xls_path, sheet_name=_SHEET, skiprows=_SKIP_ROWS)
    observations, dropped = parse_monthly(frame.to_dict("records"))
    written = pg_store.upsert_observations(panel_conn, observations)
    if dropped:
        print(f"shiller: {written} rows written, {dropped} non-numeric cells dropped")
    return written
