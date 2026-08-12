"""Load the market panel: yfinance daily bars + FRED yields → Postgres.

  python scripts/load_panel.py                          # every market, full history
  python scripts/load_panel.py --start 2025-01-01       # a window
  python scripts/load_panel.py --tickers "^GSPC,BZ=F"   # a subset
  python scripts/load_panel.py --coverage               # report only, fetch nothing

Idempotent: every row is an upsert keyed on (ticker, date, frequency), so a
re-run after a partial failure resumes rather than duplicating. Concurrent-safe
too — this is Postgres, not the single-writer graph, so it can run while the
API serves.

THE DEPTH REPORT IS THE POINT of running this in the foreground: a ticker whose
history starts years after the pack's inception_date silently shortens every
study that uses it, so the gap is printed per ticker (build-spec section 5.2).
"""

from __future__ import annotations

import argparse
import sys

from core import packs
from core import settings as settings_module
from core.ingestion import market_data
from core.panel import pg_store


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", nargs="?", default="mena")
    parser.add_argument("--start", help="ISO date; default is each market's inception_date")
    parser.add_argument("--end", help="ISO date; default is today")
    parser.add_argument("--tickers", help="comma-separated subset")
    parser.add_argument(
        "--coverage", action="store_true", help="report what the panel holds and exit"
    )
    parser.add_argument("--skip-yields", action="store_true", help="prices only")
    args = parser.parse_args()

    settings = settings_module.load()
    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else None

    try:
        pack = packs.load(args.pack)
    except packs.PackError as exc:
        sys.exit(str(exc))

    if args.coverage:
        conn = pg_store.connect(settings)
        try:
            for market in pack.markets:
                row = pg_store.coverage(conn, market["ticker"])
                held = (
                    f"{row['rows']} rows, {row['first']} → {row['last']}"
                    if row["rows"]
                    else "EMPTY"
                )
                print(f"{market['ticker']:>12}  {held}")
        finally:
            conn.close()
        return

    try:
        prices = market_data.load_daily(
            settings, region_pack=pack.name, start=args.start, end=args.end, tickers=tickers
        )
    except (market_data.IngestError, pg_store.PanelUnavailable) as exc:
        sys.exit(str(exc))

    print("daily prices:")
    for depth in prices:
        print(f"  {depth.report()}")

    if args.skip_yields:
        return
    try:
        yields = market_data.load_yields(
            settings, region_pack=pack.name, start=args.start, end=args.end
        )
    except market_data.IngestError as exc:
        # One disabled series is not a failed load — say so and stop cleanly.
        print(f"yields: skipped — {exc}")
        return
    print("yields:")
    for depth in yields:
        print(f"  {depth.report()}")


if __name__ == "__main__":
    main()
