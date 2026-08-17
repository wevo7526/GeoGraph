"""The shared, deliberately small trading-signal contract.

The platform has two different clocks: the archive measures what happened after
an event, while the live wire decides whether a newly published event clears a
usable historical edge.  Keeping the gate here prevents the wire and markets
pages from inventing slightly different meanings for the same median.

This is a signal layer, not an optimiser.  It never fits a threshold to the
equity curve and it never turns a thin cell into a trade.  The pack's fixed
paper books remain the portfolio translation; this module supplies the common
measurement, cost hurdle, and decision vocabulary around them.
"""

from __future__ import annotations

from typing import Any

STRATEGY_ID = "regional_event_impact_v1"
HEADLINE_WINDOW = "car_0_3"
MIN_MEASUREMENTS = 8
ROUND_TRIP_COST_BPS = 10.0


def strategy_contract(
    *,
    transaction_cost_bps: float = ROUND_TRIP_COST_BPS,
    min_measurements: int = MIN_MEASUREMENTS,
) -> dict[str, Any]:
    """Return the versioned rule as data for API payloads and ledgers."""
    return {
        "id": STRATEGY_ID,
        "name": "regional event impact",
        "window": HEADLINE_WINDOW,
        "min_measurements": min_measurements,
        "round_trip_cost_bps": round(float(transaction_cost_bps), 4),
        "rule": (
            "use the measured median abnormal return for the event kind; trade "
            "only when the cell clears the sample floor and its absolute median "
            "clears the round-trip cost hurdle; otherwise watch or stand aside"
        ),
        "thin_sample_action": "watch",
        "below_cost_action": "stand_aside",
    }


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def assess_cell(
    cell: dict[str, Any] | None,
    *,
    transaction_cost_bps: float = ROUND_TRIP_COST_BPS,
    min_measurements: int = MIN_MEASUREMENTS,
) -> dict[str, Any]:
    """Turn one measured response cell into a bounded trading decision.

    The returned ``expected_return`` is signed. ``edge_after_cost`` is always
    a positive magnitude only for a trade, which makes sorting impact rows
    safe without losing the direction field.
    """
    if not cell:
        return {
            "action": "watch",
            "direction": "flat",
            "expected_return": None,
            "edge_after_cost": None,
            "n": 0,
            "thin": True,
            "confidence": "none",
            "reason": "no measured response in the headline window",
        }

    median = _number(cell.get("median"))
    n = int(_number(cell.get("n"), 0.0) or 0)
    thin = bool(cell.get("thin")) or n < min_measurements or median is None
    cost = max(0.0, float(transaction_cost_bps)) / 10_000.0
    if thin:
        return {
            "action": "watch",
            "direction": "flat" if median in (None, 0.0) else ("long" if median > 0 else "short"),
            "expected_return": median,
            "edge_after_cost": None,
            "n": n,
            "thin": True,
            "confidence": "low" if n else "none",
            "reason": f"only {n} measured reactions; minimum is {min_measurements}",
        }

    assert median is not None  # narrowed by the thin branch above
    direction = "long" if median > 0 else "short" if median < 0 else "flat"
    edge = abs(median) - cost
    if direction == "flat" or edge <= 0.0:
        return {
            "action": "stand_aside",
            "direction": direction,
            "expected_return": round(median, 6),
            "edge_after_cost": round(max(0.0, edge), 6),
            "n": n,
            "thin": False,
            "confidence": "medium",
            "reason": (
                f"the measured median does not clear the {transaction_cost_bps:g} "
                "bps round-trip hurdle"
            ),
        }

    return {
        "action": "trade",
        "direction": direction,
        "expected_return": round(median, 6),
        "edge_after_cost": round(edge, 6),
        "n": n,
        "thin": False,
        "confidence": "high" if n >= 30 else "medium",
        "reason": "measured median clears the sample and round-trip cost hurdles",
    }


def market_impact(
    markets: list[dict[str, Any]],
    *,
    transaction_cost_bps: float = ROUND_TRIP_COST_BPS,
    min_measurements: int = MIN_MEASUREMENTS,
) -> list[dict[str, Any]]:
    """Attach the shared strategy decision to each region market."""
    rows: list[dict[str, Any]] = []
    for market in markets:
        signal = assess_cell(
            market.get("headline"),
            transaction_cost_bps=transaction_cost_bps,
            min_measurements=min_measurements,
        )
        rows.append({
            "ticker": market.get("ticker"),
            "market": market.get("name", market.get("ticker")),
            "kind": (market.get("headline") or {}).get("kind"),
            **signal,
        })
    rows.sort(
        key=lambda row: (
            row["action"] != "trade",
            -(abs(float(row["edge_after_cost"] or 0.0))),
        )
    )
    return rows

