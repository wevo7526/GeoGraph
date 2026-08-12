"""The walk-forward paper backtest — the paper model run through history.

Each past quarter end, the archive is TRUNCATED to the events that existed
then and the near-term forecast is recomputed through the same code path
that freezes live calls (`forecasting.forecast_from_rows` with a cutoff —
never a backtest-only estimator). The forecast's scenario likelihoods build
that quarter's book through the pack's own paper translation, the book is
entered at the first panel close after the cutoff and marked at the NEXT
quarter end, and the quarterly returns chain into an equity curve.

What this measures: whether ACTING on the frozen rule made or lost paper
money, quarter by quarter, with no hindsight anywhere — the base rates each
quarter are counted from that quarter's past only, the books are fixed in
the pack, and a quarter the panel cannot fill is a RECORDED SKIP, never a
fabricated fill. The curve is a calibration instrument, not a strategy
pitch, and it ships with the rule that generated it.

Section 17: every number is counted or panel arithmetic. Nothing here is
originated by a model, and nothing is fitted to the curve it produces.
"""

from __future__ import annotations

from typing import Any

from core.reasoning import forecasting, paper

#: A quarter only trades when its base rate rests on at least this many
#: episodes — a likelihood counted from three episodes is noise wearing a
#: number. Skipped quarters are recorded, not hidden.
MIN_EPISODES = 8

_QUARTER_END = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}

_QUESTION = "Walk-forward: which focal dyads escalate again within the horizon?"


def quarter_ends(start: str, end: str) -> list[str]:
    """Every ISO quarter-end date in [start, end], ascending."""
    out: list[str] = []
    year, quarter = int(start[:4]), (int(start[5:7]) - 1) // 3 + 1
    while True:
        date = f"{year}-{_QUARTER_END[quarter]}"
        if date > end:
            return out
        if date >= start:
            out.append(date)
        quarter += 1
        if quarter == 5:
            year, quarter = year + 1, 1


def walk_forward(
    rows: list[dict[str, Any]],
    series_by_ticker: dict[str, list[dict[str, Any]]],
    *,
    region_pack: str,
    escalation_book: dict[str, float],
    reversion_book: dict[str, float],
    horizon_years: int = 3,
) -> dict[str, Any]:
    """The whole backtest, pure: dyad-event rows + panel series in, the
    quarterly ledger and its summary out. Deterministic — same archive, same
    panel, same curve."""
    if not rows:
        raise ValueError("no dyad-coded events — nothing to walk forward over")

    event_dates = sorted(str(r["event_time"]) for r in rows)
    panel_dates = sorted(
        str(obs["obs_date"])
        for series in series_by_ticker.values()
        for obs in series
    )
    if not panel_dates:
        raise ValueError("the panel holds no closes for the book's tickers")

    # Quarters from the archive's first event to the last quarter end the
    # panel can still MARK (each book needs the following quarter's closes).
    candidates = quarter_ends(event_dates[0], min(event_dates[-1], panel_dates[-1]))
    ledger: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    equity = float(paper.NOTIONAL_USD)

    for cutoff, mark_through in zip(candidates, candidates[1:], strict=False):
        try:
            payload = forecasting.forecast_from_rows(
                rows, _QUESTION,
                region_pack=region_pack,
                horizon_years=horizon_years,
                cutoff=cutoff,
            )
        except ValueError as exc:
            skipped.append({"quarter_end": cutoff, "reason": str(exc)})
            continue
        episodes = int(payload["frozen_inputs"]["episodes"])
        if episodes < MIN_EPISODES:
            skipped.append({
                "quarter_end": cutoff,
                "reason": f"base rate rests on {episodes} episodes "
                          f"(< {MIN_EPISODES}) — too thin to trade",
            })
            continue
        try:
            likelihood, net = paper.build_book(
                payload["scenarios"],
                escalation_book=escalation_book,
                reversion_book=reversion_book,
            )
        except ValueError as exc:
            skipped.append({"quarter_end": cutoff, "reason": str(exc)})
            continue
        marked = paper.mark_book(
            net, series_by_ticker,
            entry_after=cutoff, mark_through=mark_through,
        )
        if marked["deployed_usd"] == 0:
            skipped.append({
                "quarter_end": cutoff,
                "reason": "no panel closes in the quarter — no position "
                          "could have been entered",
            })
            continue
        quarter_return = marked["pnl_usd"] / paper.NOTIONAL_USD
        equity *= 1.0 + quarter_return
        ledger.append({
            "quarter_end": cutoff,
            "marked_through": mark_through,
            "escalation_likelihood": likelihood,
            "episodes": episodes,
            "pnl_usd": marked["pnl_usd"],
            "quarter_return": round(quarter_return, 6),
            "equity_usd": round(equity, 2),
            "positions": marked["positions"],
        })

    total_return = equity / paper.NOTIONAL_USD - 1.0
    peak = float(paper.NOTIONAL_USD)
    max_drawdown = 0.0
    for entry in ledger:
        peak = max(peak, entry["equity_usd"])
        max_drawdown = max(max_drawdown, 1.0 - entry["equity_usd"] / peak)
    wins = sum(1 for entry in ledger if entry["pnl_usd"] > 0)

    return {
        "region_pack": region_pack,
        "quarters_traded": len(ledger),
        "quarters_skipped": len(skipped),
        "ledger": ledger,
        "skipped": skipped,
        "summary": {
            "notional_usd": paper.NOTIONAL_USD,
            "final_equity_usd": round(equity, 2),
            "total_return": round(total_return, 6),
            "hit_rate": round(wins / len(ledger), 4) if ledger else None,
            "max_drawdown": round(max_drawdown, 6),
        },
        "method": (
            "walk-forward: each quarter end, forecast from events <= cutoff "
            "only (same estimator as live freezes), build the pack's paper "
            f"book, enter first close after cutoff, mark at next quarter end; "
            f"quarters with < {MIN_EPISODES} base-rate episodes or no panel "
            "closes are recorded skips. "
            + paper.method_for(escalation_book, reversion_book)
        ),
    }
