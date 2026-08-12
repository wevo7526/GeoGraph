"""Near-term forecasting: 0–3 years — build-spec section 13.

The deterministic layer of the near-term mode: CALIBRATED PROBABILISTIC
SCENARIOS whose likelihoods are REGIME-GATED BASE RATES — the historical
frequency, within the current monetary order only, with which a dyad in the
focal dyad's state went on to escalate again inside the horizon. Counted, not
modeled; the counting is in the rationale so a reader can recompute the
likelihood from the archive.

Never a single number, never a raw signal (decision 1): each focal dyad gets
a scenario pair (further escalation / reversion) whose likelihoods sum to
one, each with a market implication and analogues. The game-theoretic agent
(agent.py, LLM half) later drafts richer rationales AROUND these numbers —
it does not change them (section 17).

Pure of clocks: the payload's `as_of` is the archive's own latest event
date, and the caller stamps generated_at when it freezes the Forecast node —
which is what lets calibration.py Brier-score a past call honestly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.graph import kuzu_store
from core.reasoning import regimes

#: How many of the region's most conflictual dyads get scenario pairs.
_FOCAL_DYADS = 3
#: A dyad-episode's continuation window when counting base rates, years.
_DEFAULT_HORIZON_YEARS = 3


def dyad_event_rows(conn: Any) -> list[dict[str, Any]]:
    """Every dyad-coded event with its dyad's standing baseline — the input
    both the live freeze and the walk-forward backtest reason from."""
    return kuzu_store.query(
        conn,
        "MATCH (e:Event)-[:OF_DYAD]->(d:Dyad) "
        "RETURN d.node_id AS dyad_id, d.name AS dyad_name, "
        "d.ewma_baseline AS baseline, e.node_id AS event_id, "
        "e.event_time AS event_time, e.escalation_direction AS direction, "
        "e.region_pack AS region_pack "
        "ORDER BY e.event_time, e.node_id",
    )


def _quarter(date: str) -> tuple[int, int]:
    year = int(date[:4])
    month = int(date[5:7]) if len(date) >= 7 else 1
    return year, (month - 1) // 3 + 1


def _base_rate(
    rows: list[dict[str, Any]], *, regime_anchor: str, horizon_years: int
) -> tuple[int, int]:
    """(continuations, episodes) counted on EPISODES — dyad-QUARTERS with at
    least one escalating event — not raw events.

    The distinction is load-bearing at GDELT density: a week of wire stories
    about one confrontation is dozens of escalating EVENTS but one episode,
    and counting events made the continuation rate measure how much the wire
    kept reporting (96%) rather than whether the dyad strategically
    re-escalated. An episode continues when the SAME dyad has another
    escalating episode in a LATER quarter within the horizon. Cross-dyad
    pooling stays (single-dyad samples are thin) and stays stated in the
    rationale.
    """
    episode_quarters: dict[str, set[tuple[int, int]]] = {}
    for row in rows:
        if row["direction"] != "escalating":
            continue
        date = str(row["event_time"])
        if not regimes.comparable(regime_anchor, date):
            continue
        episode_quarters.setdefault(row["dyad_id"], set()).add(_quarter(date))

    episodes = 0
    continuations = 0
    horizon_quarters = horizon_years * 4
    for quarters in episode_quarters.values():
        ordered = sorted(quarters)
        indexed = [year * 4 + (quarter - 1) for year, quarter in ordered]
        for position, quarter_index in enumerate(indexed):
            episodes += 1
            if any(
                0 < later - quarter_index <= horizon_quarters
                for later in indexed[position + 1 :]
            ):
                continuations += 1
    return continuations, episodes


def forecast(
    db_path: Path,
    question: str,
    *,
    region_pack: str,
    horizon_years: int = _DEFAULT_HORIZON_YEARS,
) -> dict[str, Any]:
    """A near-term Forecast payload: mode='near_term', scenario pairs with
    base-rate likelihoods, frozen inputs. The caller stamps generated_at and
    persists — nothing here reads a clock."""
    conn = kuzu_store.connect(db_path, read_only=True)
    try:
        rows = dyad_event_rows(conn)
    finally:
        kuzu_store.close(conn)
    if not rows:
        raise ValueError("the graph holds no dyad-coded events — seed first")
    return forecast_from_rows(
        rows, question, region_pack=region_pack, horizon_years=horizon_years
    )


def forecast_from_rows(
    rows: list[dict[str, Any]],
    question: str,
    *,
    region_pack: str,
    horizon_years: int = _DEFAULT_HORIZON_YEARS,
    cutoff: str | None = None,
) -> dict[str, Any]:
    """The pure body of `forecast`, over prefetched dyad-event rows.

    `cutoff` truncates the archive to events at or before that date, which is
    what makes an AS-OF forecast honest: the walk-forward backtest recomputes
    the call each past quarter from exactly the events that existed then,
    through this one code path — never a special backtest-only estimator.
    """
    if cutoff is not None:
        rows = [row for row in rows if str(row["event_time"]) <= cutoff]
    if not rows:
        raise ValueError(
            f"no dyad-coded events at or before {cutoff} — nothing to reason from"
        )

    as_of = max(str(row["event_time"]) for row in rows)

    # Focal dyads: most conflictual standing baselines IN THE REGION with
    # history to reason from. Deterministic order.
    by_dyad: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_dyad.setdefault(row["dyad_id"], []).append(row)
    regional = [
        (dyad_id, events)
        for dyad_id, events in by_dyad.items()
        if len(events) >= 2
        and any(e["region_pack"] == region_pack for e in events)
    ]
    regional.sort(key=lambda pair: (pair[1][-1]["baseline"] or 0.0, pair[0]))
    focal = regional[:_FOCAL_DYADS]

    continuations, episodes = _base_rate(
        rows, regime_anchor=as_of, horizon_years=horizon_years
    )
    rate = continuations / episodes if episodes else 0.5

    scenarios: list[dict[str, Any]] = []
    for dyad_id, events in focal:
        name = events[-1]["dyad_name"]
        latest = events[-1]
        counting = (
            f"{continuations} of {episodes} in-regime escalating EPISODES "
            f"(dyad-quarters with an escalating event, all dyads, monetary "
            f"order at {as_of}) saw another escalating episode on the same "
            f"dyad within {horizon_years}y"
        )
        scenarios.append({
            "scenario_name": f"further_escalation:{dyad_id}",
            "likelihood": round(rate, 4),
            "market_implication": (
                f"Renewed escalation on {name} prices as event risk in the "
                "region's equity indices and as a premium on the energy "
                "benchmarks — direction measured per event by the transmission "
                "engine, never asserted here."
            ),
            "rationale": (
                f"{name} carries baseline {latest['baseline']} with its latest "
                f"event {latest['event_id']} ({latest['event_time']}). Base rate: "
                f"{counting}. Likelihood IS that frequency — recount it from the "
                "archive."
            ),
            "analogue_ids": [],
        })
        scenarios.append({
            "scenario_name": f"reversion_to_baseline:{dyad_id}",
            "likelihood": round(1.0 - rate, 4),
            "market_implication": (
                f"A quiet horizon on {name} decays the standing risk premium; "
                "relative normalization of the most exposed markets."
            ),
            "rationale": (
                f"The complement of the escalation base rate for {name}: "
                f"{episodes - continuations} of {episodes} in-regime "
                f"dyad-quarter episodes were NOT followed within {horizon_years}y."
            ),
            "analogue_ids": [],
        })

    return {
        "mode": "near_term",
        "region_pack": region_pack,
        "question": question,
        "as_of": as_of,
        "horizon_years": horizon_years,
        "scenarios": scenarios,
        "frozen_inputs": {
            "episodes": episodes,
            "continuations": continuations,
            "focal_dyads": [dyad_id for dyad_id, _ in focal],
            "event_count": len(rows),
            "as_of": as_of,
        },
        "method": (
            "regime-gated base rates: escalating episodes within the current "
            "monetary order, continuation counted on the same dyad within "
            f"{horizon_years}y; likelihood = frequency, complement pairs sum to 1"
        ),
    }
