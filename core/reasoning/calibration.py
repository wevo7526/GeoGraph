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


def retrodict(
    db_path: Any,
    *,
    as_of: str,
    region_pack: str,
    lookahead_years: int = 10,
) -> dict[str, Any]:
    """The Turchin retrospective: run the structural layer AS OF a past date
    and check whether the pressure it flagged preceded the conflict that
    followed.

    Deterministic on both sides: the as-of forecast sees only pre-`as_of`
    data (structural_forecast truncates every series), and "what followed" is
    the same conflict-intensity metric computed on the full archive. The
    verdict is a hit rate beside its base rate — reported, not adjudicated:
    a hit rate that fails to beat the base rate is exactly the finding.
    """
    from core.reasoning import structural

    forecast = structural.structural_forecast(
        db_path, region_pack=region_pack, as_of=as_of
    )
    realized = structural.pressure_components(db_path)["conflict_intensity"]

    horizon_start = int(as_of[:4]) + 1
    horizon_end = horizon_start + lookahead_years - 1
    horizon = {y: v for y, v in realized.items() if horizon_start <= y <= horizon_end}
    if len(horizon) < 3:
        return {
            "as_of": as_of, "region_pack": region_pack,
            "verdict": "insufficient realized data in the lookahead horizon",
            "flagged_years": [], "hits": [], "hit_rate": None, "base_rate": None,
            # This return is still long-horizon output, so it still carries the
            # boundary statement. Dropping it here made the ONE shape a reader
            # meets when the archive is thin the one shape missing its caveat.
            "boundary_statement": structural.BOUNDARY_STATEMENT,
        }

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
    hit_rate = len(hits) / len(flagged) if flagged else None
    base_rate = len(hot_years) / len(horizon)
    return {
        "as_of": as_of,
        "region_pack": region_pack,
        "flagged_years": sorted(flagged),
        "hot_years": sorted(hot_years),
        "hits": hits,
        "hit_rate": hit_rate,
        "base_rate": base_rate,
        "boundary_statement": forecast["boundary_statement"],
        "method": (
            f"as-of-truncated structural pressure vs realized conflict intensity; "
            f"hot = >= median of the {lookahead_years}y horizon; "
            "hit rate reported beside base rate, never adjudicated"
        ),
    }
