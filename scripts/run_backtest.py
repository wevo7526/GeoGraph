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
from core.panel import pg_store
from core.reasoning import backtest, forecasting
from core.reasoning import strategy


def run(pack_name: str) -> dict[str, Any] | None:
    """One pack's walk-forward, persisted. None if the pack declares no books."""
    pack = packs.load(pack_name)
    books = pack.paper_books
    if books is None:
        print(f"{pack_name}: no paper_books declared — nothing to backtest")
        return None

    settings = settings_module.load()
    # Both stores, one read — the same union the freeze reasons from, so the
    # backtest walks the archive the forecasts were computed over.
    rows = forecasting.all_dyad_event_rows(settings.kuzu_db_path)

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
            # The direct helper defaults to zero for backwards-compatible
            # notebooks/tests; the persisted platform ledger uses the actual
            # versioned round-trip hurdle.
            transaction_cost_bps=strategy.ROUND_TRIP_COST_BPS,
        )
        written = pg_store.record_backtest(panel, pack_name, result)
        # The skips and the summary travel with the ledger — a region whose
        # every quarter was a recorded skip must say so, not read as unrun.
        pg_store.record_backtest_run(panel, pack_name, result)
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
