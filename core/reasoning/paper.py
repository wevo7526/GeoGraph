"""The paper book — a frozen forecast's market implications, marked to market.

A MECHANICAL TRANSLATION, and it says so: the near-term scenarios imply a
direction (escalation prices an energy premium and a regional discount;
reversion prices the normalization), and this module turns that sentence
into fixed paper positions weighted by the scenario's own base-rate
likelihood, enters at the first panel close after the forecast's data
cutoff, and marks at the latest close the panel holds. Gain or loss on the
notional IS the calibration instrument: did acting on the frozen call make
or lose paper money — reported beside the rule that generated it, never
fitted to make it look better, never advice.

Section 17 note: every number here is panel arithmetic. The likelihood came
from counted base rates; the prices come from the panel; the rule is fixed
and printed. Nothing is originated by a model.
"""

from __future__ import annotations

from typing import Any

#: The fixed translation, stated once. Escalation: long the energy premium
#: and the haven, short the exposed regional index. Reversion: long the
#: normalization. Signed weights per book; the two books are blended by the
#: scenario likelihood p, so the net position IS the forecast's balance.
ESCALATION_BOOK: dict[str, float] = {
    "BZ=F": 0.40,
    "GC=F": 0.20,
    "^TASI.SR": -0.20,
    "DFMGI.AE": -0.20,
}
REVERSION_BOOK: dict[str, float] = {
    "^TASI.SR": 0.50,
    "DFMGI.AE": 0.50,
}

NOTIONAL_USD = 1_000_000

METHOD = (
    "paper book: net weight per ticker = p*escalation + (1-p)*reversion with "
    "fixed books (escalation: +0.4 BZ=F, +0.2 GC=F, -0.2 ^TASI.SR, -0.2 "
    "DFMGI.AE; reversion: +0.5 ^TASI.SR, +0.5 DFMGI.AE); enter first close "
    "after the forecast's data cutoff, mark at latest close; P&L = "
    "weight * notional * (mark/entry - 1). Mechanical, unfitted, not advice."
)


def build_book(scenarios: list[dict[str, Any]]) -> tuple[float, dict[str, float]]:
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
    tickers = set(ESCALATION_BOOK) | set(REVERSION_BOOK)
    net = {
        ticker: round(
            p * ESCALATION_BOOK.get(ticker, 0.0)
            + (1.0 - p) * REVERSION_BOOK.get(ticker, 0.0),
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
) -> dict[str, Any]:
    """Mark the book against panel series. Pure — testable exactly.

    A ticker with no close after the entry date is a recorded skip: the
    position could not have been entered, so it contributes nothing rather
    than a fabricated fill.
    """
    positions: list[dict[str, Any]] = []
    total = 0.0
    deployed = 0.0
    for ticker, weight in net.items():
        series = [
            row for row in series_by_ticker.get(ticker, [])
            if str(row["obs_date"]) > entry_after
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
        "method": METHOD,
    }
