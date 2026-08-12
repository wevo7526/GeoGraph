"""Run the walk-forward paper backtest and persist the ledger.

  python scripts/run_backtest.py             # every pack that declares books
  python scripts/run_backtest.py mena        # one pack

Reads the graph READ-ONLY (safe beside a running API) and writes only to
Postgres. Each pack's backtest recomputes the near-term forecast at every
past quarter end from the events that existed then — the same estimator the
live freeze uses, truncated, never a lookahead — builds the pack's own paper
book, marks it at the next quarter end, and chains the equity curve. The
ledger REPLACES the pack's previous one: a backtest is a function of
(archive, panel, books), and history recomputed under a new estimator should
look recomputed.
"""

from __future__ import annotations

import sys
from typing import Any

from core import packs
from core import settings as settings_module
from core.graph import kuzu_store
from core.panel import pg_store
from core.reasoning import backtest, forecasting


def run(pack_name: str) -> dict[str, Any] | None:
    """One pack's walk-forward, persisted. None if the pack declares no books."""
    pack = packs.load(pack_name)
    books = pack.paper_books
    if books is None:
        print(f"{pack_name}: no paper_books declared — nothing to backtest")
        return None

    settings = settings_module.load()
    conn = kuzu_store.connect(settings.kuzu_db_path, read_only=True)
    try:
        rows = forecasting.dyad_event_rows(conn)
    finally:
        kuzu_store.close(conn)

    tickers = sorted(set(books["escalation"]) | set(books["reversion"]))
    panel = pg_store.connect(settings)
    try:
        pg_store.apply_schema(panel)
        series = {
            ticker: pg_store.series(
                panel, ticker, start="1800-01-01", end="2100-01-01"
            )
            for ticker in tickers
        }
        result = backtest.walk_forward(
            rows, series,
            region_pack=pack_name,
            escalation_book=books["escalation"],
            reversion_book=books["reversion"],
        )
        written = pg_store.record_backtest(panel, pack_name, result)
    finally:
        panel.close()

    summary: dict[str, Any] = result["summary"]
    print(
        f"{pack_name}: {written} quarters traded, "
        f"{result['quarters_skipped']} skipped, "
        f"final equity ${summary['final_equity_usd']:,.0f} "
        f"({summary['total_return']:+.1%}), "
        f"hit rate {summary['hit_rate']}, "
        f"max drawdown {summary['max_drawdown']:.1%}"
    )
    return summary


def main() -> None:
    settings = settings_module.load()
    if not settings.kuzu_db_path.exists():
        sys.exit(f"no graph at {settings.kuzu_db_path} — seed first")
    names = [sys.argv[1]] if len(sys.argv) > 1 else packs.available()
    for name in names:
        try:
            run(name)
        except (ValueError, pg_store.PanelUnavailable) as exc:
            print(f"{name}: not backtested — {exc}")


if __name__ == "__main__":
    main()
