"""Market-implied duration: what the yield curve says about how LONG.

THE SECOND MOMENT, and the reason the term structure was added to every pack.
docs/game-spec.md §2.2 flagged that patience and cost are not separately
identified from behaviour alone — both make a dyad fight longer — and named
this as the measurement that breaks the tie.

The mechanism is standard fixed-income reasoning applied to an event study.
Equity prices how BAD a shock is. The curve prices how LONG:

  - a disruption the market expects to pass moves the FRONT end (3M, 2Y) and
    leaves the long bond alone;
  - one it expects to persist repricies the LONG end (10Y) too.

So the share of a shock's total curve response sitting at the long end is a
market-implied statement about persistence — and persistence in quarters is
exactly what the game predicts from δ. A dyad whose crises repeatedly move
the long end is one the market believes cannot de-escalate quickly, which the
event record alone never says.

EVERY NUMBER HERE IS MEASURED. The inputs are AFFECTED abnormal returns the
transmission engine computed from the panel; nothing is modelled, and a
missing tenor is reported rather than imputed (build-spec §17).

HONEST GAP, STATED RATHER THAN BURIED: the mapping from "share of the move at
the long end" to "expected quarters" is NOT established. Until it is, this is
a validation target — compare its ordering across dyads against the
equilibrium's own run lengths — not a moment to fit against. Fitting to an
uncalibrated mapping would recover δ in units nobody can name.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

#: The curve, by node id, shortest first. Node ids rather than tickers because
#: the packs share these instruments and describe them identically — the id is
#: the thing that is guaranteed stable across lenses.
TENORS: tuple[tuple[str, str], ...] = (
    ("market:dgs3mo", "front"),
    ("market:dgs2", "belly"),
    ("market:dgs10", "long"),
)

#: Events a dyad needs before its implied persistence is reported rather than
#: flagged. A duration read off two crises is not a market view.
MIN_EVENTS = 6


def curve_response(effects: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """event id → {tenor: abnormal return}, for events the curve reacted to.

    Only events with at least a front and a long reading are kept: the whole
    statistic is a RATIO between the two ends, and one end alone cannot say
    anything about duration.
    """
    by_market = {node_id: label for node_id, label in TENORS}
    per_event: dict[str, dict[str, float]] = defaultdict(dict)
    for effect in effects:
        label = by_market.get(str(effect.get("market_id")))
        if label is None or effect.get("abnormal_return") is None:
            continue
        per_event[str(effect["event_id"])][label] = float(effect["abnormal_return"])
    return {
        event: readings
        for event, readings in per_event.items()
        if "front" in readings and "long" in readings
    }


def implied_persistence(readings: dict[str, float]) -> float | None:
    """Share of the curve's total move sitting at the LONG end, in [0, 1].

    Near 0 — the front end moved and the long bond did not: the market
    expects this to pass. Near 1 — the long end carried it: the market is
    repricing something it expects to last.

    Absolute values, because direction is a separate question (a flight to
    quality and an inflation scare move yields opposite ways while both
    saying "this matters"). Duration is about WHERE on the curve, not which
    way.
    """
    front = abs(readings.get("front", 0.0))
    long_end = abs(readings.get("long", 0.0))
    total = front + long_end
    if total <= 0.0:
        return None
    return float(long_end / total)


def by_dyad(
    effects: list[dict[str, Any]],
    dyad_of_event: dict[str, str],
    *,
    min_events: int = MIN_EVENTS,
) -> list[dict[str, Any]]:
    """Per-dyad market-implied persistence, with the sample behind it.

    Thin dyads are RETURNED AND FLAGGED rather than dropped: an implied
    duration resting on three events is still evidence about that dyad, and a
    reader is entitled to see both the number and how little is under it.
    """
    per_dyad: dict[str, list[float]] = defaultdict(list)
    for event, readings in curve_response(effects).items():
        share = implied_persistence(readings)
        dyad = dyad_of_event.get(event)
        if share is not None and dyad:
            per_dyad[dyad].append(share)

    rows: list[dict[str, Any]] = []
    for dyad, shares in per_dyad.items():
        rows.append({
            "dyad_id": dyad,
            "n": len(shares),
            "implied_persistence": round(float(np.median(shares)), 4),
            "p25": round(float(np.percentile(shares, 25)), 4),
            "p75": round(float(np.percentile(shares, 75)), 4),
            "thin": len(shares) < min_events,
        })
    rows.sort(key=lambda r: (-int(r["n"]), str(r["dyad_id"])))
    return rows


def simulated_persistence(rows: list[dict[str, Any]]) -> float:
    """The equilibrium's own persistence: mean length of an escalation run.

    The quantity the curve statistic is a market's estimate OF, computed
    directly from a simulated panel — so the two can be compared even though
    the units differ, by their ordering across dyads rather than their levels.
    """
    runs: list[int] = []
    current = 0
    escalate = 2  # ACTIONS index; a run is consecutive quarters of escalation
    for row in rows:
        hot = int(row.get("action_a", 0)) == escalate or int(row.get("action_b", 0)) == escalate
        if hot:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return float(np.mean(runs)) if runs else 0.0


def report(
    effects: list[dict[str, Any]],
    dyad_of_event: dict[str, str],
) -> dict[str, Any]:
    """What the curve can and cannot say, in one payload."""
    responded = curve_response(effects)
    tenors_seen = {
        label for readings in responded.values() for label in readings
    }
    rows = by_dyad(effects, dyad_of_event)
    return {
        "events_with_a_curve_response": len(responded),
        "tenors_measured": sorted(tenors_seen),
        "dyads": rows,
        "usable_dyads": sum(1 for r in rows if not r["thin"]),
        # The gap, carried with the numbers rather than left to memory.
        "calibration": (
            "the mapping from long-end share to expected QUARTERS is not "
            "established; use this to compare dyads against each other and "
            "against the equilibrium's run lengths, not as a fitted moment"
        ),
        "note": (
            None if responded else
            "no event has both a front-end and a long-end measurement — either "
            "the yields are not in the panel yet (FRED_API_KEY) or the "
            "transmission engine has not measured them"
        ),
        "method": (
            "share of |abnormal return| at the 10Y over the sum of |3M| and "
            "|10Y|, per event, median per dyad; measured AFFECTED edges only"
        ),
    }
