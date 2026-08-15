"""The trading surface — the paper model's past and its standing present.

Two reads, both honest about what they are:

- `/trading/backtest` serves the PERSISTED walk-forward ledger: the paper
  rule recomputed at every past quarter end from only the events that
  existed then, marked at the next quarter end. History, with the estimator
  and the skips attached.
- `/trading/forward` serves the standing position: the latest frozen
  near-term forecast translated through the pack's books and marked at the
  latest close, beside the long-horizon pressure trajectory — which ALWAYS
  carries the boundary statement, because pressure over windows is not a
  dated prediction (build-spec section 14).

Nothing here computes a forecast at request time; frozen calls and a
persisted ledger are what make the numbers the same for every caller and
scoreable later. Not advice, and the method strings say so.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core import packs
from core import settings as settings_module
from core.graph import kuzu_store
from core.panel import pg_store
from core.reasoning import paper

router = APIRouter(tags=["trading"])


def _conn(request: Request) -> Any:
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    return conn


def _books(region: str) -> dict[str, dict[str, float]]:
    try:
        pack = packs.load(region)
    except packs.PackError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    books = pack.paper_books
    if books is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"packs/{region} declares no paper_books — this region has no "
                "paper model, and borrowing another region's tickers would "
                "trade the wrong thing."
            ),
        )
    return books


@router.get("/trading/backtest")
def backtest_ledger(region: str = "mena") -> dict[str, Any]:
    """The persisted walk-forward ledger, oldest quarter first, with the
    summary recomputed from the rows served (no second source of truth)."""
    _books(region)  # 404 early for a region with no paper model
    settings = settings_module.load()
    try:
        panel = pg_store.connect(settings)
    except pg_store.PanelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        rows = pg_store.backtest_rows(panel, region)
        run = pg_store.backtest_run(panel, region)
    finally:
        panel.close()

    books = _books(region)
    if not rows:
        # A region whose every quarter was a recorded skip is NOT unrun — the
        # run record says why each quarter stood aside.
        if run is not None:
            return {
                "region": region, "rows": [], "summary": None,
                "computed_at": run["computed_at"],
                "quarters_skipped": run["quarters_skipped"],
                "skipped": run["skipped"],
                "skip_reasons": _skip_reasons(run["skipped"]),
                "books": books,
                "method": run["method"],
                "note": (
                    f"the walk ran and every one of its {run['quarters_skipped']} "
                    "quarters was a recorded skip — see skip_reasons; nothing compounded."
                ),
            }
        return {
            "region": region, "rows": [], "summary": None,
            "books": books,
            "note": (
                "no persisted backtest for this region yet — run "
                "scripts/run_backtest.py (or a boot with GEOGRAPH_BACKTEST_ON_BOOT=1)."
            ),
        }

    equity = [row["equity_usd"] for row in rows]
    peak = float(paper.NOTIONAL_USD)
    max_drawdown = 0.0
    drawdown_series = []
    for row, value in zip(rows, equity, strict=True):
        peak = max(peak, value)
        dd = 1.0 - value / peak
        max_drawdown = max(max_drawdown, dd)
        drawdown_series.append({"quarter_end": row["quarter_end"], "drawdown": round(dd, 6)})
    wins = sum(1 for row in rows if row["pnl_usd"] > 0)
    return {
        "region": region,
        "rows": rows,
        # When this history was computed — the reader's staleness check. A
        # ledger without a date on it is how a pre-corpus curve got mistaken
        # for the current rule's record.
        "computed_at": rows[-1]["computed_at"],
        "summary": {
            "notional_usd": paper.NOTIONAL_USD,
            "quarters_traded": len(rows),
            "quarters_skipped": run["quarters_skipped"] if run else None,
            "final_equity_usd": equity[-1],
            "total_return": round(equity[-1] / paper.NOTIONAL_USD - 1.0, 6),
            "hit_rate": round(wins / len(rows), 4),
            "max_drawdown": round(max_drawdown, 6),
            "best_quarter": max(rows, key=lambda r: r["quarter_return"])["quarter_end"],
            "worst_quarter": min(rows, key=lambda r: r["quarter_return"])["quarter_end"],
            "first_quarter": rows[0]["quarter_end"],
            "last_quarter": rows[-1]["quarter_end"],
        },
        "drawdown": drawdown_series,
        # Per-market attribution: where the P&L came from, over the whole
        # ledger, from the positions the walk actually held.
        "attribution": _attribution(rows),
        "skipped": run["skipped"] if run else [],
        "skip_reasons": _skip_reasons(run["skipped"]) if run else [],
        "books": books,
        "method": rows[-1]["method"],
    }


def _attribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ticker: dict[str, dict[str, Any]] = {}
    for row in rows:
        for pos in row.get("positions", []) or []:
            if pos.get("status") != "marked":
                continue
            slot = by_ticker.setdefault(pos["ticker"], {
                "ticker": pos["ticker"], "pnl_usd": 0.0, "quarters": 0, "wins": 0,
                "gross_weight": 0.0,
            })
            pnl = float(pos.get("pnl_usd") or 0.0)
            slot["pnl_usd"] += pnl
            slot["quarters"] += 1
            slot["wins"] += 1 if pnl > 0 else 0
            slot["gross_weight"] += abs(float(pos.get("weight") or 0.0))
    out = []
    for slot in by_ticker.values():
        out.append({
            "ticker": slot["ticker"],
            "pnl_usd": round(slot["pnl_usd"], 2),
            "quarters": slot["quarters"],
            "hit_rate": round(slot["wins"] / slot["quarters"], 4) if slot["quarters"] else None,
            "mean_abs_weight": (
                round(slot["gross_weight"] / slot["quarters"], 4) if slot["quarters"] else None
            ),
        })
    out.sort(key=lambda r: -abs(r["pnl_usd"]))
    return out


def _skip_reasons(skipped: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Skips grouped by reason family, so a page can say 'thin base rate ×12,
    partial book ×30' instead of listing 111 lines."""
    families: dict[str, dict[str, Any]] = {}
    for entry in skipped or []:
        reason = str(entry.get("reason", ""))
        if "legs" in reason or "unenterable" in reason or "no panel" in reason:
            family = "partial or unenterable book"
        elif "episodes" in reason and "focal" not in reason:
            family = "base rate too thin"
        elif "focal" in reason:
            family = "focal dyads below the episode bar"
        elif "deployed" in reason:
            family = "book deployed nothing"
        else:
            family = "estimator declined"
        slot = families.setdefault(family, {"reason": family, "quarters": 0, "example": reason,
                                             "first": entry.get("quarter_end"),
                                             "last": entry.get("quarter_end")})
        slot["quarters"] += 1
        slot["last"] = entry.get("quarter_end")
    return sorted(families.values(), key=lambda r: -r["quarters"])


@router.get("/trading/forward")
def forward_view(request: Request, region: str = "mena") -> dict[str, Any]:
    """The standing book and the long-horizon pressure, in one read.

    The book half needs the panel to mark; without it the book is null with
    the reason attached — never an invented fill. The pressure half is the
    frozen long-horizon forecast and always carries the boundary statement.
    """
    books = _books(region)
    conn = _conn(request)
    rows = kuzu_store.query(
        conn,
        "MATCH (f:Forecast) WHERE f.region_pack = $region "
        "RETURN f.node_id AS node_id, f.mode AS mode, "
        "f.generated_at AS generated_at, f.horizon_end AS horizon_end, "
        "f.question AS question, f.scenarios_json AS scenarios_json, "
        "f.frozen_inputs_json AS frozen_inputs_json, "
        "f.boundary_statement AS boundary_statement "
        "ORDER BY f.generated_at DESC, f.node_id",
        {"region": region},
    )
    near = next((r for r in rows if r["mode"] == "near_term"), None)
    long_horizon = next((r for r in rows if r["mode"] == "long_horizon"), None)
    if near is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no frozen near-term forecast for {region} — run "
                "scripts/run_forecasts.py (or reboot the container)."
            ),
        )

    scenarios = json.loads(near["scenarios_json"] or "[]")
    frozen = json.loads(near["frozen_inputs_json"] or "{}")
    entry_after = str(frozen.get("as_of") or "")
    likelihood, net = paper.build_book(
        scenarios,
        escalation_book=books["escalation"],
        reversion_book=books["reversion"],
    )

    book: dict[str, Any] | None = None
    book_unavailable: str | None = None
    settings = settings_module.load()
    try:
        panel = pg_store.connect(settings)
        try:
            series = {
                ticker: pg_store.series(
                    panel, ticker, start=entry_after, end="2100-01-01"
                )
                for ticker in net
            }
        finally:
            panel.close()
        book = paper.mark_book(net, series, entry_after=entry_after)
        book["method"] = paper.method_for(books["escalation"], books["reversion"])
    except pg_store.PanelUnavailable as exc:
        book_unavailable = str(exc)
    except Exception as exc:  # noqa: BLE001 - the docstring is the contract
        # "Without it the book is null with the reason attached — never an
        # invented fill." A driver error marking the book is the panel being
        # unavailable in a costume; a 500 here took the whole trading page
        # down on 2026-08-13 when the pressure half was fine.
        book_unavailable = f"panel error while marking: {exc}"

    pressure: dict[str, Any] | None = None
    if long_horizon is not None:
        long_frozen = json.loads(long_horizon["frozen_inputs_json"] or "{}")
        pressure = {
            "node_id": long_horizon["node_id"],
            "generated_at": long_horizon["generated_at"],
            "horizon_end": long_horizon["horizon_end"],
            "boundary_statement": long_horizon["boundary_statement"],
            "trajectory": long_frozen.get("pressure", {}),
            "windows": long_frozen.get("windows", []),
            # The composite only exists for years holding EVERY component, so
            # an empty or short trajectory is a coverage fact, not a failure.
            # It travels with the series that lacks it.
            "span": long_frozen.get("pressure_span"),
            "coverage_gaps": long_frozen.get("coverage", {}),
        }

    return {
        "region": region,
        "forecast": {
            "node_id": near["node_id"],
            "generated_at": near["generated_at"],
            "horizon_end": near["horizon_end"],
            "as_of": entry_after,
            "escalation_likelihood": likelihood,
            "scenarios": scenarios,
        },
        "net_weights": net,
        "book": book,
        "book_unavailable": book_unavailable,
        "pressure": pressure,
    }
