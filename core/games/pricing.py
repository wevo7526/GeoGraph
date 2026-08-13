"""A predicted step → what markets did after comparable events, MEASURED.

The half of the deliverable that makes it "a sequenced event WITH its market
movement" rather than just a sequence. And the half that must not be modelled:
`AFFECTED` edges are what the transmission engine measured from the price
panel, and every number here is a quantile of those. The game predicts events;
the archive prices them (build-spec section 17).

THE MATCHING IS DELIBERATELY COARSE. A step is matched to past events by quad
class, intensity band and regime — not by dyad, and not by exact magnitude.
Tightening it produces samples of two, and a median of two abnormal returns is
not a market implication, it is an anecdote with a percent sign. Where even
the coarse match is thin, the row SAYS the sample is thin rather than quietly
reporting a number nobody should act on.

NOTHING IS PREDICTED HERE. If a dyad's comparable events never touched a
market that existed at the time, the answer is that there is no measurement —
which is a real and common outcome, because the transmission engine records a
SKIP for markets that did not exist at event time.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from core.games import state as state_module
from core.graph import kuzu_store
from core.reasoning import regimes

#: Measurements a (quad, band, market) cell needs before its quantiles are
#: reported as a market implication rather than flagged as thin.
MIN_MEASUREMENTS = 8


def measured_effects(conn: Any, *, region_pack: str | None = None) -> list[dict[str, Any]]:
    """Every measured event→market effect, with the event's coded shape.

    One query. The alternative — fetching effects per predicted step — would
    hit the graph once per path per step per market, which for a distribution
    over eight paths is hundreds of round trips for data that fits in memory.
    """
    where = "WHERE e.region_pack = $pack " if region_pack else ""
    return kuzu_store.query(
        conn,
        "MATCH (e:Event)-[a:AFFECTED]->(m:Market) "
        f"{where}"
        "RETURN e.node_id AS event_id, e.event_time AS event_time, "
        "e.quad_class AS quad_class, "
        "e.escalation_magnitude AS magnitude, "
        "m.node_id AS market_id, m.name AS market_name, "
        "a.abnormal_return AS abnormal_return, a.window AS window, "
        "a.resolution AS resolution",
        {"pack": region_pack} if region_pack else {},
    )


def _quantiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def at(share: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * share))]

    return {
        "min": round(ordered[0], 6),
        "p25": round(at(0.25), 6),
        "median": round(at(0.5), 6),
        "p75": round(at(0.75), 6),
        "max": round(ordered[-1], 6),
    }


def build_index(
    effects: list[dict[str, Any]],
    *,
    as_of: str,
    scale: float,
) -> dict[tuple[str, int], dict[str, list[float]]]:
    """(quad class, intensity band) → market → the measured abnormal returns.

    Regime-gated against `as_of` by the same admissibility test the analogy
    engine uses: a 2026 question is never answered with Bretton Woods
    evidence, however similar the event shape.
    """
    index: dict[tuple[str, int], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for effect in effects:
        if effect["abnormal_return"] is None or not effect["quad_class"]:
            continue
        if not regimes.comparable(as_of, str(effect["event_time"])):
            continue
        magnitude = effect["magnitude"]
        band = state_module.intensity_band(
            float(magnitude) if magnitude is not None else 0.0, scale
        )
        key = (str(effect["quad_class"]), band)
        index[key][str(effect["market_id"])].append(float(effect["abnormal_return"]))
    return index


def price_step(
    step: dict[str, Any],
    index: dict[tuple[str, int], dict[str, list[float]]],
    names: dict[str, str],
    *,
    min_measurements: int = MIN_MEASUREMENTS,
) -> list[dict[str, Any]]:
    """One predicted step → a measured distribution per market.

    Falls back from (quad, band) to (quad, any band) when the exact cell is
    thin, and says which match it used. A reader who knows the row came from
    a looser match reads it differently, which is the entire reason to say so.
    """
    exact = index.get((str(step["quad"]), int(step["intensity_band"])), {})
    loosened: dict[str, list[float]] = defaultdict(list)
    for (quad, _band), markets in index.items():
        if quad == step["quad"]:
            for market, values in markets.items():
                loosened[market].extend(values)

    rows: list[dict[str, Any]] = []
    for market in sorted(set(exact) | set(loosened)):
        values = exact.get(market, [])
        match = "quad+band"
        if len(values) < min_measurements:
            values = loosened.get(market, [])
            match = "quad only"
        if not values:
            continue
        rows.append({
            "market_id": market,
            "market_name": names.get(market, market),
            "n": len(values),
            "match": match,
            # Thin is reported, not hidden and not dropped: an implication
            # resting on five measurements is still evidence, but a reader is
            # entitled to know it is five.
            "thin": len(values) < min_measurements,
            **_quantiles(values),
        })
    rows.sort(key=lambda r: (-r["n"], r["market_id"]))
    return rows


def price_paths(
    paths: dict[str, Any],
    effects: list[dict[str, Any]],
    *,
    as_of: str,
    scale: float,
    min_measurements: int = MIN_MEASUREMENTS,
) -> dict[str, Any]:
    """Attach measured market distributions to every step of every path."""
    index = build_index(effects, as_of=as_of, scale=scale)
    names = {
        str(e["market_id"]): str(e["market_name"])
        for e in effects if e.get("market_name")
    }
    priced = []
    for path in paths.get("paths", []):
        steps = [
            {**step, "market": price_step(step, index, names,
                                          min_measurements=min_measurements)}
            for step in path["steps"]
        ]
        priced.append({**path, "steps": steps})

    measured = sum(len(m) for markets in index.values() for m in markets.values())
    return {
        **paths,
        "paths": priced,
        "pricing": {
            "measurements": measured,
            "cells": len(index),
            "regime_gated_to": as_of,
            "min_measurements": min_measurements,
            "method": (
                "measured AFFECTED abnormal returns for events of the same quad "
                "class and intensity band, regime-gated; quantiles over the "
                "matched set, never a modelled price"
            ),
            # An empty index is a FINDING. It means the transmission engine has
            # measured nothing admissible for this region and regime — usually
            # because the markets did not exist at event time — and the paths
            # should render with no prices rather than with invented ones.
            "note": (
                None if measured else
                "no measured market effects are admissible for this regime; "
                "the sequence stands without prices"
            ),
        },
    }
