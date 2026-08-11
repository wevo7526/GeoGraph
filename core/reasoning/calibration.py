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


def retrodict(as_of: str, region_pack: str) -> dict[str, Any]:
    """Run the long-horizon structural layer as of a past date and compare the
    flagged pressure against what followed. Phase 5 — requires structural.py."""
    raise NotImplementedError("Phase 5 — see docs/build-spec.md section 13")
