"""Near-term forecasting: 0–3 years — build-spec section 13.

Temporal-graph forecasting over the modern-tier graph plus the game-theoretic
agent (agent.py), producing CALIBRATED PROBABILISTIC SCENARIOS: each with a
likelihood, a market-direction implication, historical analogues, and a
rationale traced to graph evidence — never a single number, never a raw
signal (decision 1, locked).

Forecasts freeze their inputs at generation (Forecast.frozen_inputs_json) so
calibration.py can Brier-score them honestly later. Backtesting is rolling-
window over the modern tier.

PHASE 5.
"""

from __future__ import annotations

from typing import Any


def forecast(question: str, *, region_pack: str, horizon_years: int = 3) -> dict[str, Any]:
    """A near-term Forecast node's payload: mode='near_term', scenario list
    with likelihoods, frozen inputs, generated_at."""
    raise NotImplementedError("Phase 5 — see docs/build-spec.md section 13")
