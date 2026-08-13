"""Long-horizon structural forecasting: 5–20 years — build-spec section 13.

Structural forecasting on slow-moving variables: the power balance from CINC
trajectories and power-transition dynamics, the alliance-network structure,
and the accumulation of systemic pressure — grounded in structural-demographic
and long-cycle theory (the Turchin frame, decision 4).

EVERYTHING HERE IS DETERMINISTIC (section 17): the components are arithmetic
over the graph's CINC estimates and coded events, each component is
percentile-ranked against its own history (self-normalizing — no tuned
thresholds), and the composite is their mean. The LLM narrates a scenario
space around these numbers later; it never originates one of them.

OUTPUT IS A SCENARIO SPACE with crisis-probability windows and structural
trajectories. EXPLICITLY NOT DATED POINT PREDICTIONS — and every output
carries boundary_statement saying so. Evaluated by retrodiction
(calibration.retrodict), never point-calibrated: `as_of` truncates every
input series, which is what makes an honest as-of-1970 run possible.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from core.graph import kuzu_store

BOUNDARY_STATEMENT = (
    "This is structural forecasting: it maps the accumulation of systemic "
    "pressure and crisis probability over a window. It does not — and cannot — "
    "call exact dates or events."
)

#: Trailing window, years, for the event-derived components: long enough to
#: smooth single crises, short enough that pressure can actually move.
_EVENT_WINDOW_YEARS = 5

#: A pressure run must clear these percentile ranks of ITS OWN history to be
#: flagged. Percentiles, not absolute levels, because the components have no
#: natural units in common.
_ELEVATED = 0.6
_HIGH = 0.8

#: Coded events a trailing window must hold before its share/intensity counts
#: as a MEASUREMENT of that window rather than an anecdote about it.
#:
#: This floor is not fussiness. The archive's density is wildly uneven — the
#: GDELT wire runs 1979–2005 and puts thousands of coded events in a five-year
#: window, while the years after it hold only the curated spine, six or eight
#: events. Percentile-ranking a six-event window against a five-thousand-event
#: window ranks sampling noise against a measurement, and it did exactly the
#: damage you would predict: mean |goldstein| over the two curated conflicts of
#: 2023–2025 pinned conflict_intensity at 1.0, its all-time high, which read
#: off the chart as the most dangerous moment in 120 years. A window under the
#: floor yields NO value for that year — dropped and counted (`coverage`),
#: never smoothed over.
_MIN_WINDOW_SAMPLE = 30


def _concentration(shares: list[float]) -> float:
    """Singer's CON: 0 when capability is evenly spread, 1 when one state
    holds everything. Deterministic function of the year's CINC shares."""
    n = len(shares)
    if n < 2:
        return 0.0
    total = sum(shares)
    if total <= 0:
        return 0.0
    normalized = [s / total for s in shares]
    return math.sqrt(max(0.0, sum(s * s for s in normalized) - 1 / n) / (1 - 1 / n))


def _percentile_ranks(series: dict[int, float]) -> dict[int, float]:
    """Each year's value ranked against the WHOLE series (0..1). Ties share
    the lower rank, so a flat series ranks 0 everywhere rather than 0.5 —
    a component that never moves contributes no pressure."""
    years = sorted(series)
    values = sorted(series.values())
    out: dict[int, float] = {}
    for year in years:
        below = sum(1 for v in values if v < series[year])
        out[year] = below / max(1, len(values) - 1)
    return out


def pressure_components(db_path: Path, *, as_of: str | None = None) -> dict[str, Any]:
    """The slow variables, per year, from the graph — truncated at `as_of`.

    - concentration: Singer's CON over CINC (capability concentration).
    - transition_proximity: challenger/leader CINC ratio — the Organski
      window opens as the ratio approaches 1.
    - escalation_share: share of events in the trailing window Head B coded
      escalating.
    - conflict_intensity: mean |goldstein| of material-conflict events in the
      trailing window, on the Goldstein scale's own [0, 10].
    """
    conn = kuzu_store.connect(db_path, read_only=True)
    try:
        estimates = kuzu_store.query(
            conn,
            "MATCH (:Actor)-[:HAS_ESTIMATE]->(s:AttributeEstimate) "
            "WHERE s.attribute = 'clout' "
            "RETURN s.as_of AS as_of, s.value_mean AS value ORDER BY s.as_of",
        )
        events = kuzu_store.query(
            conn,
            "MATCH (e:Event) RETURN e.event_time AS event_time, "
            "e.goldstein AS goldstein, e.quad_class AS quad_class, "
            "e.escalation_direction AS direction ORDER BY e.event_time",
        )
    finally:
        kuzu_store.close(conn)

    cutoff = as_of or "9999"
    clout_by_year: dict[int, list[float]] = {}
    for row in estimates:
        stamp = str(row["as_of"] or "")
        if not stamp or stamp > cutoff or row["value"] is None:
            continue
        clout_by_year.setdefault(int(stamp[:4]), []).append(float(row["value"]))

    concentration: dict[int, float] = {}
    proximity: dict[int, float] = {}
    for year, values in sorted(clout_by_year.items()):
        ordered = sorted(values, reverse=True)
        concentration[year] = _concentration(ordered)
        if len(ordered) >= 2 and ordered[0] > 0:
            proximity[year] = ordered[1] / ordered[0]

    dated = [
        (int(str(e["event_time"])[:4]), e)
        for e in events
        if str(e["event_time"]) <= cutoff
    ]
    escalation_share: dict[int, float] = {}
    intensity: dict[int, float] = {}
    if dated:
        first, last = dated[0][0], dated[-1][0]
        for year in range(first, last + 1):
            window = [e for y, e in dated if year - _EVENT_WINDOW_YEARS < y <= year]
            if not window:
                continue
            coded = [e for e in window if e["direction"]]
            if len(coded) >= _MIN_WINDOW_SAMPLE:
                escalation_share[year] = sum(
                    1 for e in coded if e["direction"] == "escalating"
                ) / len(coded)
            conflicts = [
                abs(float(e["goldstein"]))
                for e in window
                if e["quad_class"] == "material_conflict" and e["goldstein"] is not None
            ]
            if len(conflicts) >= _MIN_WINDOW_SAMPLE:
                intensity[year] = sum(conflicts) / len(conflicts) / 10.0

    return {
        "concentration": concentration,
        "transition_proximity": proximity,
        "escalation_share": escalation_share,
        "conflict_intensity": intensity,
    }


def structural_forecast(
    db_path: Path,
    *,
    region_pack: str,
    horizon_years: int = 20,
    as_of: str | None = None,
) -> dict[str, Any]:
    """A long-horizon Forecast payload: mode='long_horizon', pressure
    trajectory, crisis-probability windows, scenario space with likelihoods
    null, boundary_statement REQUIRED.

    Pure computation — the caller stamps generated_at and freezes the
    payload; nothing here reads a clock, which is what keeps a retrodiction
    identical to what a real run would have produced on that date.
    """
    components = pressure_components(db_path, as_of=as_of)
    ranked = {name: _percentile_ranks(series) for name, series in components.items()}

    # THE COMPOSITE IS ONLY COMPARABLE ACROSS YEARS IF IT IS THE SAME COMPOSITE.
    # Each component ends where its source ends — capability estimates at the
    # last CINC year, the event-derived pair at the last year with a window
    # above the sample floor — so a mean over "whatever exists this year"
    # silently changes definition mid-series. It did: past the capability data
    # the mean of four components became the mean of the two noisiest, and the
    # series ended on a fabricated all-time high. A year contributes a pressure
    # reading when EVERY component the archive can compute has a value for it,
    # and otherwise contributes a coverage row saying what was missing.
    expected = {name for name, series in components.items() if series}
    years = sorted({year for series in components.values() for year in series})
    pressure: dict[int, float] = {}
    coverage: dict[int, list[str]] = {}
    for year in years:
        present = {name for name in expected if year in components[name]}
        if present != expected:
            coverage[year] = sorted(expected - present)
            continue
        ranks = [ranked[name][year] for name in sorted(expected)]
        pressure[year] = sum(ranks) / len(ranks)

    windows: list[dict[str, Any]] = []
    run_start: int | None = None
    run_level = ""
    previous_year: int | None = None

    def close_run(end_year: int) -> None:
        if run_start is not None:
            windows.append({"start": run_start, "end": end_year, "level": run_level})

    for year in sorted(pressure):
        level = (
            "high" if pressure[year] >= _HIGH
            else "elevated" if pressure[year] >= _ELEVATED
            else ""
        )
        contiguous = previous_year is not None and year == previous_year + 1
        if level != run_level or not contiguous:
            if previous_year is not None:
                close_run(previous_year)
            run_start = year if level else None
            run_level = level
        previous_year = year
    if previous_year is not None:
        close_run(previous_year)

    latest = max(pressure) if pressure else 0
    latest_pressure = pressure.get(latest, 0.0)
    drivers = sorted(
        ((name, series.get(latest, 0.0)) for name, series in ranked.items()),
        key=lambda pair: -pair[1],
    )

    scenarios: list[dict[str, Any]] = [
        {
            "scenario_name": "pressure_release_through_crisis",
            "likelihood": None,
            "market_implication": (
                "Risk assets in the region price a discount during high-pressure "
                "windows; energy benchmarks carry the event premium."
            ),
            "rationale": (
                "Composite structural pressure at the latest reading is "
                f"{latest_pressure:.2f} (percentile of its own history), "
                f"led by {drivers[0][0] if drivers else 'no component'}. High-pressure "
                "windows historically precede clustered material-conflict events; "
                "the release path, not its date, is the forecastable object."
            ),
            "analogue_ids": [],
        },
        {
            "scenario_name": "pressure_decay_through_accommodation",
            "likelihood": None,
            "market_implication": (
                "Compression of the regional risk premium; relative outperformance "
                "of the markets most exposed to the pressure's driver."
            ),
            "rationale": (
                "Every component is a percentile of its own history, so decay is "
                "measurable against the same series that flagged the pressure — "
                "de-escalating baselines and falling transition proximity are the "
                "observable signature."
            ),
            "analogue_ids": [],
        },
    ]

    return {
        "mode": "long_horizon",
        "region_pack": region_pack,
        "as_of": as_of,
        "horizon_years": horizon_years,
        "boundary_statement": BOUNDARY_STATEMENT,
        "components": {name: dict(sorted(series.items())) for name, series in components.items()},
        "pressure": dict(sorted(pressure.items())),
        # Where the composite stops, and what stopped it — the front end draws
        # the boundary from this rather than running the line into thin air.
        "coverage": {str(year): missing for year, missing in sorted(coverage.items())},
        "pressure_span": (
            [min(pressure), max(pressure)] if pressure else None
        ),
        "windows": windows,
        "scenarios": scenarios,
        "method": (
            "components percentile-ranked against own history; composite=mean; "
            f"elevated>={_ELEVATED}, high>={_HIGH}; "
            f"event window={_EVENT_WINDOW_YEARS}y; CON over CINC shares"
        ),
    }
