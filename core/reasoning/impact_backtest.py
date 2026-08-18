"""Past-only walk of the event-impact gate — not the paper book.

`backtest.walk_forward` marks the locked three-year continuation call through
the pack's paper books. This module asks a different question: given a
headline cell formed from STRICTLY PRIOR clean measurements, would
`strategy.assess_cell` have traded this event, and what did the event's own
CAR then do?

Spine events first. GDELT often holds only `car_0_3`, so session 0 is not a
tradeable leg (`pg_store.drift_after_impact` already states that). Nothing
here is fitted to the curve it produces, and the paper books are untouched.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from core.reasoning import strategy
from core.reasoning.transmission_skill import (
    CLEAN_P_GATE,
    HEADLINE_WINDOW,
    MIN_CELL,
    _clean_prior,
    _median,
    _regime_id,
    happening_key,
    is_gdelt,
)


def walk(
    observations: list[dict[str, Any]],
    *,
    window: str = HEADLINE_WINDOW,
    min_cell: int = MIN_CELL,
    p_gate: float | None = CLEAN_P_GATE,
    spine_only: bool = True,
    transaction_cost_bps: float = strategy.ROUND_TRIP_COST_BPS,
) -> dict[str, Any]:
    """Each event is a mark: past-only kind cell → assess_cell → this CAR."""
    rows = [
        obs for obs in observations
        if obs.get("ar") is not None and str(obs.get("window") or window) == window
    ]
    if spine_only:
        rows = [obs for obs in rows if not is_gdelt(obs.get("event_id"))]
    rows.sort(key=lambda r: (str(r["date"]), str(r.get("event_id") or "")))

    history: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    seen: set[tuple[str, str, float]] = set()
    ledger: list[dict[str, Any]] = []
    equity = 1.0

    for obs in rows:
        ticker = str(obs["ticker"])
        kind = str(obs["kind"])
        regime = _regime_id(obs["date"]) or ""
        values = history.get((ticker, kind, regime)) or []
        cell = None
        if len(values) >= min_cell:
            med = _median(values)
            cell = {"median": med, "n": len(values), "thin": False}
        decision = strategy.assess_cell(
            cell, transaction_cost_bps=transaction_cost_bps, min_measurements=min_cell,
        )
        realized = float(obs["ar"])
        cost = transaction_cost_bps / 10_000.0
        pnl = 0.0
        if decision["action"] == "trade":
            sign = 1.0 if decision["direction"] == "long" else -1.0
            pnl = sign * realized - cost
            equity *= 1.0 + pnl
        ledger.append({
            "event_id": obs.get("event_id"),
            "date": obs["date"],
            "ticker": ticker,
            "kind": kind,
            "action": decision["action"],
            "direction": decision["direction"],
            "realized": round(realized, 6),
            "pnl": round(pnl, 6),
        })
        if not _clean_prior(obs, p_gate=p_gate):
            continue
        happening = happening_key(obs)
        if happening in seen:
            continue
        seen.add(happening)
        history[(ticker, kind, regime)].append(realized)

    trades = [row for row in ledger if row["action"] == "trade"]
    wins = [row for row in trades if row["pnl"] > 0]
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    types = {str(obs.get("ticker")): str(obs.get("market_type") or "") for obs in rows}
    for row in trades:
        by_type[types.get(str(row["ticker"]), "") or "unknown"].append(row)

    def _summary(rows_: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows_:
            return {"trades": 0, "hit_rate": None, "mean_pnl": None}
        return {
            "trades": len(rows_),
            "hit_rate": round(sum(1 for r in rows_ if r["pnl"] > 0) / len(rows_), 4),
            "mean_pnl": round(sum(r["pnl"] for r in rows_) / len(rows_), 6),
        }

    return {
        "spine_only": spine_only,
        "window": window,
        "events": len(ledger),
        "trades": len(trades),
        "watches": sum(1 for row in ledger if row["action"] == "watch"),
        "stand_asides": sum(1 for row in ledger if row["action"] == "stand_aside"),
        "hit_rate": (
            round(len(wins) / len(trades), 4) if trades else None
        ),
        "final_equity": round(equity, 6),
        "by_market_type": {k: _summary(v) for k, v in sorted(by_type.items())},
        "transaction_cost_bps": transaction_cost_bps,
        "note": (
            "marks this event's own headline CAR after a past-only cell; "
            "session 0 is not treated as tradeable; the paper book is untouched"
        ),
    }
