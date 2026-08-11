"""The transmission engine: a DETERMINISTIC event study — build-spec section 11.

This is the layer that makes the geopolitics-to-money link SHOWN, not
asserted. For each event and each market that existed at event time, compute
the effect at the finest frequency the era allows:

  intraday_open_close   recent only (~60 days of yfinance intraday)
  car_0_1/car_0_3/car_0_5  daily CAR — the modern-era workhorse
  monthly               Shiller era, US
  annual                JST era, advanced economies

Method: market-model expected return over a clean pre-event estimation window
scaled to the frequency; abnormal = actual − expected; significance test on
the estimation-window residuals. The `resolution` is recorded on every
AFFECTED edge so the reasoning layer can down-weight coarse effects — a 1912
annual abnormal return is not a 2012 daily CAR and is never allowed to look
like one.

HONESTY RULES, locked:
  - Measure realized effects; NEVER assert a sign.
  - SKIP a market that did not exist at event time (Market.inception_date) —
    recorded as a skip in event_study_runs, not silently absent.
  - FLAG overlapping event windows (`overlapping` on the edge) rather than
    averaging them away.
  - Every number on an AFFECTED edge is computed HERE. The AI never
    originates one.

PHASE 1. Reads the panel from Postgres (core.panel.pg_store), computes in
pandas/statsmodels (`analysis` extra), writes through
core.transmission.effects. The dataclass and signatures are the contract.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class EffectResult:
    """One measured effect: one event, one market, one window."""

    event_node_id: str
    market_ticker: str
    window: str            # EffectWindow enum value
    resolution: str        # TemporalResolution enum value
    raw_return: float
    expected_return: float
    abnormal_return: float
    t_stat: float
    p_value: float
    first_mover: bool
    overlapping: bool
    method: str            # estimation window, model, frequency — reproducibility line


#: Estimation-window lengths per resolution, in periods of that resolution.
#: Scaled, not shared: 120 daily observations and 10 annual ones are both
#: "the pre-event window", at very different confidence — which the t-stat
#: then carries honestly.
ESTIMATION_PERIODS: dict[str, int] = {
    "intraday": 60,
    "day": 120,
    "month": 60,
    "year": 10,
}


def finest_window(event_date: dt.date, market: dict) -> str:
    """The finest EffectWindow the era and this market allow, from the
    market's era-keyed native frequency. Phase 1."""
    raise NotImplementedError("Phase 1 — see docs/build-spec.md section 11")


def compute_effects(event_node_id: str) -> list[EffectResult]:
    """Run the study for one event across every market alive at its date.
    Deterministic and reproducible: same panel, same result. Phase 1."""
    raise NotImplementedError("Phase 1 — see docs/build-spec.md section 11")
