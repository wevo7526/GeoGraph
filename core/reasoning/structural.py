"""Long-horizon structural forecasting: 5–20 years — build-spec section 13.

Structural forecasting on slow-moving variables: the power balance from CINC
trajectories and power-transition dynamics, the monetary and debt regime, the
alliance-network structure, and the accumulation of systemic pressure —
grounded in structural-demographic and long-cycle theory (the Turchin frame,
decision 4).

OUTPUT IS A SCENARIO SPACE with crisis-probability windows and structural
trajectories, conditioned on the current regime and matched to deep
analogues. EXPLICITLY NOT DATED POINT PREDICTIONS — and every output carries
Forecast.boundary_statement saying so: this maps pressure and probability
over a window; it does not call exact dates or events. Evaluated by
retrodiction (calibration.retrodict), never point-calibrated.

PHASE 5.
"""

from __future__ import annotations

from typing import Any

BOUNDARY_STATEMENT = (
    "This is structural forecasting: it maps the accumulation of systemic "
    "pressure and crisis probability over a window. It does not — and cannot — "
    "call exact dates or events."
)


def structural_forecast(*, region_pack: str, horizon_years: int = 20) -> dict[str, Any]:
    """A long-horizon Forecast payload: mode='long_horizon', scenario space
    with pressure trajectories and crisis-probability windows, likelihoods
    null, boundary_statement REQUIRED."""
    raise NotImplementedError("Phase 5 — see docs/build-spec.md section 13")
