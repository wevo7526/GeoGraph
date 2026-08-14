"""Calibration — build-spec section 13. Honesty about forecasts, mechanised.

Two modes, two evaluations, because they answer different questions:

- NEAR-TERM forecasts carry probabilities, so they are Brier-scored against
  realized outcomes. Implemented here — it is arithmetic, and keeping it pure
  means a frozen Forecast can be scored the day its horizon closes.
- LONG-HORIZON forecasts are scenario spaces with crisis-probability windows;
  point calibration DOES NOT APPLY. They are evaluated by RETRODICTION (the
  Turchin retrospective method): run the structural layer as of past dates and
  check whether the pressure it flagged preceded the crises that followed.

Forecasts are frozen at generation time with their inputs (section 17), which
is the only reason either evaluation can be honest.
"""

from __future__ import annotations

from typing import Any


def brier_score(forecasts: list[tuple[float, bool]]) -> float:
    """Mean Brier score over (probability, outcome) pairs. 0 is perfect,
    0.25 is what always saying 50% earns, 1 is perfectly wrong."""
    if not forecasts:
        raise ValueError("Brier score of an empty forecast set is undefined.")
    for p, _ in forecasts:
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"probability {p} is outside [0, 1]")
    return sum((p - (1.0 if outcome else 0.0)) ** 2 for p, outcome in forecasts) / len(forecasts)


def score_forecast(scenarios: list[dict[str, Any]], outcomes: dict[str, bool]) -> float:
    """Brier-score one near-term Forecast's scenario list against resolved
    outcomes, keyed by scenario_name. Scenarios without a likelihood (the
    long-horizon shape) are refused — that mode is retrodicted, not scored."""
    pairs: list[tuple[float, bool]] = []
    for scenario in scenarios:
        name = scenario["scenario_name"]
        if name not in outcomes:
            continue
        likelihood = scenario.get("likelihood")
        if likelihood is None:
            raise ValueError(
                f"scenario {name!r} carries no likelihood — long-horizon output "
                "is evaluated by retrodiction (see retrodict), never Brier-scored."
            )
        pairs.append((float(likelihood), outcomes[name]))
    return brier_score(pairs)


def _retrodict_one(
    archive: Any,
    *,
    region_pack: str,
    as_of: str,
    realized: dict[int, float],
    lookahead_years: int,
) -> dict[str, Any] | None:
    """One anchor of the retrospective — None when the horizon lacks data."""
    forecast = archive.forecast(region_pack=region_pack, as_of=as_of)
    horizon_start = int(as_of[:4]) + 1
    horizon_end = horizon_start + lookahead_years - 1
    horizon = {y: v for y, v in realized.items() if horizon_start <= y <= horizon_end}
    if len(horizon) < 3:
        return None

    ordered = sorted(horizon.values())
    median = ordered[len(ordered) // 2]
    hot_years = {y for y, v in horizon.items() if v >= median}

    flagged: set[int] = set()
    for window in forecast["windows"]:
        flagged.update(
            y for y in range(window["start"], window["end"] + 1)
            if horizon_start <= y <= horizon_end
        )
    # Pressure flagged BEFORE the horizon also counts toward the claim "the
    # years right after as_of were loaded": a window still open at as_of
    # projects one event-window forward.
    open_at_cutoff = [
        w for w in forecast["windows"] if w["end"] >= int(as_of[:4]) - 1
    ]
    if open_at_cutoff:
        flagged.update(range(horizon_start, min(horizon_start + 5, horizon_end + 1)))

    hits = sorted(flagged & hot_years)
    return {
        "as_of": as_of,
        "flagged_years": sorted(flagged),
        "hot_years": sorted(hot_years),
        "hits": hits,
        "hit_rate": len(hits) / len(flagged) if flagged else None,
        "base_rate": len(hot_years) / len(horizon),
        "horizon_years_observed": len(horizon),
    }


def retrodict(
    db_path: Any,
    *,
    as_of: str,
    region_pack: str,
    lookahead_years: int = 10,
    anchor_step_years: int = 5,
) -> dict[str, Any]:
    """The Turchin retrospective: stand the structural method at MANY past
    dates and check whether the pressure it flagged preceded the conflict
    that followed.

    Deterministic on both sides: each as-of forecast sees only pre-anchor
    data (the archive truncates every series), and "what followed" is the
    same conflict-intensity metric computed on the full archive. The verdict
    is an aggregate hit rate beside its base rate — reported, not
    adjudicated: a hit rate that fails to beat the base rate is exactly the
    finding.

    MANY ANCHORS, NOT ONE. The frozen retrodiction used to stand at a single
    date (ten years back), and at that date the region's pressure happened to
    flag nothing — so every region's verification record was an empty list
    and a null hit rate, a section that could never verify anything. One
    quiet anchor is not evidence about the method; a century of anchors is.
    `PressureArchive` loads the inputs once, so the extra anchors cost
    milliseconds each.
    """
    from core.reasoning import structural

    archive = structural.PressureArchive.load(db_path, region_pack=region_pack)
    realized = archive.components()["conflict_intensity"]
    end_year = int(as_of[:4])
    first_anchor = (min(realized) + lookahead_years) if realized else end_year

    anchor_years = sorted(
        set(range(first_anchor, end_year + 1, anchor_step_years)) | {end_year}
    )
    anchors = [
        row
        for year in anchor_years
        if (
            row := _retrodict_one(
                archive,
                region_pack=region_pack,
                as_of=f"{year}-12-31",
                realized=realized,
                lookahead_years=lookahead_years,
            )
        )
        is not None
    ]
    if not anchors:
        return {
            "as_of": as_of, "region_pack": region_pack,
            "verdict": "insufficient realized data in every lookahead horizon",
            "anchors": [], "flagged_years": [], "hits": [],
            "hit_rate": None, "base_rate": None,
            # This return is still long-horizon output, so it still carries the
            # boundary statement. Dropping it here made the ONE shape a reader
            # meets when the archive is thin the one shape missing its caveat.
            "boundary_statement": structural.BOUNDARY_STATEMENT,
        }

    total_flagged = sum(len(a["flagged_years"]) for a in anchors)
    total_hits = sum(len(a["hits"]) for a in anchors)
    total_hot = sum(len(a["hot_years"]) for a in anchors)
    total_years = sum(a["horizon_years_observed"] for a in anchors)
    latest = anchors[-1]
    return {
        "as_of": as_of,
        "region_pack": region_pack,
        "anchors": anchors,
        "anchors_evaluated": len(anchors),
        # The latest anchor's detail keeps the old top-level shape readable…
        "flagged_years": latest["flagged_years"],
        "hot_years": latest["hot_years"],
        "hits": latest["hits"],
        # …while the RATES are the aggregate over every anchor — the record.
        "hit_rate": (total_hits / total_flagged) if total_flagged else None,
        "base_rate": (total_hot / total_years) if total_years else None,
        "flagged_total": total_flagged,
        "hits_total": total_hits,
        "boundary_statement": structural.BOUNDARY_STATEMENT,
        "method": (
            f"as-of-truncated structural pressure vs realized conflict intensity, "
            f"restricted to this lens, at {len(anchors)} anchors "
            f"({anchor_step_years}y apart); hot = >= median of each "
            f"{lookahead_years}y horizon; aggregate hit rate reported beside "
            "base rate, never adjudicated"
        ),
    }
