"""The paper book — a frozen forecast's market implications, marked to market.

A MECHANICAL TRANSLATION, and it says so: the near-term scenarios imply a
direction, and this module turns that sentence into fixed paper positions
weighted by the scenario's own base-rate likelihood, enters at the first
panel close after the forecast's data cutoff, and marks at the latest close
the panel holds (or at `mark_through`, for the walk-forward backtest). Gain
or loss on the notional IS the calibration instrument: did acting on the
frozen call make or lose paper money — reported beside the rule that
generated it, never fitted to make it look better, never advice.

THE BOOKS COME FROM THE PACK (`paper_books` in assets.yaml): which tickers
express "escalation" is a regional reading — an oil premium in the Gulf,
strait risk in East Asia — and core code that hardcoded one region's tickers
would trade Brent on a Taiwan forecast. Region packs are a contract; this
module blends whatever books the pack declares and never names a ticker.

Section 17 note: every number here is panel arithmetic. The likelihood came
from counted base rates; the prices come from the panel; the rule is fixed
and printed. Nothing is originated by a model.
"""

from __future__ import annotations

from typing import Any

NOTIONAL_USD = 1_000_000


def method_for(
    escalation_book: dict[str, float], reversion_book: dict[str, float]
) -> str:
    """The rule, printed in full beside every result it produced."""
    def _side(book: dict[str, float]) -> str:
        return ", ".join(
            f"{weight:+g} {ticker}" for ticker, weight in sorted(book.items())
        )

    return (
        "paper book: net weight per ticker = p*escalation + (1-p)*reversion "
        f"with fixed books (escalation: {_side(escalation_book)}; "
        f"reversion: {_side(reversion_book)}); enter first close after the "
        "forecast's data cutoff, mark at latest close; P&L = "
        "weight * notional * (mark/entry - 1). Mechanical, unfitted, not advice."
    )


def build_book(
    scenarios: list[dict[str, Any]],
    *,
    escalation_book: dict[str, float],
    reversion_book: dict[str, float],
) -> tuple[float, dict[str, float]]:
    """(escalation likelihood used, net signed weight per ticker).

    The near-term scenario pairs share one base rate by construction; the
    mean over the escalation scenarios is taken so a future forecast with
    per-dyad rates still nets to one book.
    """
    rates = [
        float(s["likelihood"])
        for s in scenarios
        if s.get("likelihood") is not None
        and str(s.get("scenario_name", "")).startswith("further_escalation")
    ]
    if not rates:
        raise ValueError(
            "no escalation scenarios with likelihoods — the paper book "
            "translates the NEAR-TERM mode only (long-horizon carries no "
            "likelihoods, by design)."
        )
    p = sum(rates) / len(rates)
    tickers = set(escalation_book) | set(reversion_book)
    net = {
        ticker: round(
            p * escalation_book.get(ticker, 0.0)
            + (1.0 - p) * reversion_book.get(ticker, 0.0),
            6,
        )
        for ticker in sorted(tickers)
    }
    return p, net


def mark_book(
    net: dict[str, float],
    series_by_ticker: dict[str, list[dict[str, Any]]],
    *,
    entry_after: str,
    mark_through: str | None = None,
) -> dict[str, Any]:
    """Mark the book against panel series. Pure — testable exactly.

    A ticker with no close after the entry date is a recorded skip: the
    position could not have been entered, so it contributes nothing rather
    than a fabricated fill. `mark_through` bounds the mark date (inclusive) —
    the walk-forward backtest marks each quarter's book at the NEXT quarter
    end, never at prices the quarter could not have seen.
    """
    positions: list[dict[str, Any]] = []
    total = 0.0
    deployed = 0.0
    for ticker, weight in net.items():
        series = [
            row for row in series_by_ticker.get(ticker, [])
            if str(row["obs_date"]) > entry_after
            and (mark_through is None or str(row["obs_date"]) <= mark_through)
        ]
        if len(series) < 2:
            positions.append({
                "ticker": ticker, "weight": weight, "status": "skipped",
                "reason": f"no panel closes after {entry_after}",
            })
            continue
        entry = float(series[0]["price"])
        mark = float(series[-1]["price"])
        pnl = weight * NOTIONAL_USD * (mark / entry - 1.0)
        deployed += abs(weight) * NOTIONAL_USD
        total += pnl
        positions.append({
            "ticker": ticker,
            "weight": weight,
            "status": "marked",
            "entry_date": str(series[0]["obs_date"]),
            "entry": entry,
            "mark_date": str(series[-1]["obs_date"]),
            "mark": mark,
            "pnl_usd": round(pnl, 2),
        })
    return {
        "notional_usd": NOTIONAL_USD,
        "deployed_usd": round(deployed, 2),
        "pnl_usd": round(total, 2),
        "return_on_notional": round(total / NOTIONAL_USD, 6),
        "positions": positions,
    }
