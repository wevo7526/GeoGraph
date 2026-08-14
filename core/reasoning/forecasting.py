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

#: WHICH ESCALATIONS COUNT. An episode is a dyad-quarter holding an event whose
#: departure from that dyad's OWN baseline sits in the top decile of all
#: in-regime departures.
#:
#: Counting every escalating event instead answered a question nobody asked. At
#: wire density a rivalry produces escalating events continuously, so "did
#: another escalating quarter follow within 3y?" resolved true 99% of the time
#: for any dyad active in GDELT — it measured whether the dyad was chronically
#: in the news, not whether it escalated. The magnitude is already relative to
#: the dyad's own EWMA baseline (a −6.0 is routine for a rivalry and a rupture
#: for an alliance), so a single cross-dyad threshold on it compares like with
#: like. The percentile is read off the archive at forecast time and frozen in
#: the payload, never a hardcoded score.
_SIGNIFICANCE_PERCENTILE = 0.90

#: In-regime episodes a dyad needs before it can be a FOCAL dyad. A dyad with
#: no episodes of its own contributes nothing but the pooled prior, and putting
#: it at the head of a forecast presents the pooled rate as a finding about a
#: dyad the archive has never seen escalate.
_MIN_FOCAL_EPISODES = 4


def dyad_event_rows(conn: Any) -> list[dict[str, Any]]:
    """Every dyad-coded event with its dyad's standing baseline — the input
    both the live freeze and the walk-forward backtest reason from."""
    return kuzu_store.query(
        conn,
        "MATCH (e:Event)-[:OF_DYAD]->(d:Dyad) "
        "RETURN d.node_id AS dyad_id, d.name AS dyad_name, "
        "d.ewma_baseline AS baseline, e.node_id AS event_id, "
        "e.event_time AS event_time, e.escalation_direction AS direction, "
        "e.escalation_magnitude AS magnitude, e.region_pack AS region_pack "
        "ORDER BY e.event_time, e.node_id",
    )


def all_dyad_event_rows(db_path: Any) -> list[dict[str, Any]]:
    """THE UNION OF BOTH STORES, BY EVENT ID — the read behind the freeze, the
    walk-forward backtest and the scorer, so the three can never disagree
    about what the archive holds.

    The graph's dyad-coded rows are the curated spine plus whatever wire a
    past deploy merged AND rescored — on a rebuilt volume that is 55 events,
    and base rates counted off 55 events wear the same typography as ones
    counted off a million. The wire ships as corpus artifacts in every image,
    so it is always available to union in; the id is the dedup key because
    the parser mints identical ids in both stores.
    """
    conn = kuzu_store.connect(db_path, read_only=True)
    try:
        rows = dyad_event_rows(conn)
    finally:
        kuzu_store.close(conn)

    from core.wire import corpus as wire_corpus

    if wire_corpus.installed():
        seen = {str(row["event_id"]) for row in rows}
        rows.extend(
            row for row in wire_corpus.forecast_rows() if row["event_id"] not in seen
        )
        rows.sort(key=lambda r: (str(r["event_time"]), str(r["event_id"])))
    return rows


def quarter(date: str) -> tuple[int, int]:
    """(year, quarter) of an ISO date at any archive resolution — shared by
    the base-rate counter here and the calibration scorer, so a forecast and
    its later scoring can never disagree about what a quarter is."""
    year = int(date[:4])
    month = int(date[5:7]) if len(date) >= 7 else 1
    return year, (month - 1) // 3 + 1


def _significance_threshold(
    rows: list[dict[str, Any]], *, regime_anchor: str
) -> float:
    """The magnitude an escalation must clear to count, read off the in-regime
    distribution rather than asserted. Zero when the archive holds no graded
    escalations, which degrades to counting them all."""
    magnitudes = sorted(
        float(row["magnitude"])
        for row in rows
        if row["direction"] == "escalating"
        and row.get("magnitude") is not None
        and regimes.comparable(regime_anchor, str(row["event_time"]))
    )
    if not magnitudes:
        return 0.0
    return magnitudes[min(len(magnitudes) - 1, int(len(magnitudes) * _SIGNIFICANCE_PERCENTILE))]


def _episode_counts(
    rows: list[dict[str, Any]], *, regime_anchor: str, horizon_years: int,
    threshold: float,
) -> dict[str, tuple[int, int]]:
    """(continuations, episodes) PER DYAD, counted on EPISODES — dyad-QUARTERS
    with at least one escalating event — not raw events.

    The distinction is load-bearing at GDELT density: a week of wire stories
    about one confrontation is dozens of escalating EVENTS but one episode,
    and counting events made the continuation rate measure how much the wire
    kept reporting (96%) rather than whether the dyad strategically
    re-escalated. An episode continues when the SAME dyad has another
    escalating episode in a LATER quarter within the horizon.

    Counted per dyad and kept per dyad. Collapsing these to one pooled
    numerator was the bug the shrinkage below exists to fix: pooled over 5,572
    episodes the rate measures "does ANY active dyad stay active" (93%), and
    every focal dyad was then handed that same 93% no matter what its own
    record said.
    """
    episode_quarters: dict[str, set[tuple[int, int]]] = {}
    for row in rows:
        if row["direction"] != "escalating":
            continue
        magnitude = row.get("magnitude")
        # A threshold of zero means the archive grades no escalations at all,
        # and the documented degradation is to count them all. Where grading
        # DOES exist, an ungraded event cannot clear the bar and is dropped.
        if threshold > 0.0 and (magnitude is None or float(magnitude) < threshold):
            continue
        date = str(row["event_time"])
        if not regimes.comparable(regime_anchor, date):
            continue
        episode_quarters.setdefault(row["dyad_id"], set()).add(quarter(date))

    horizon_quarters = horizon_years * 4
    counts: dict[str, tuple[int, int]] = {}
    for dyad_id, quarters in episode_quarters.items():
        ordered = sorted(quarters)
        indexed = [year * 4 + (quarter - 1) for year, quarter in ordered]
        episodes = 0
        continuations = 0
        for position, quarter_index in enumerate(indexed):
            episodes += 1
            if any(
                0 < later - quarter_index <= horizon_quarters
                for later in indexed[position + 1 :]
            ):
                continuations += 1
        counts[dyad_id] = (continuations, episodes)
    return counts


def _prior_strength(counts: dict[str, tuple[int, int]], pooled: float) -> float:
    """Prior pseudo-episodes for the partial-pooling estimator below, by
    method of moments on the beta-binomial.

    The question this answers is "how much do dyads actually differ?". If the
    spread of per-dyad rates is no wider than binomial noise around the pooled
    rate would explain, the dyads are indistinguishable on this evidence and
    the prior is effectively infinite — every dyad correctly gets the pooled
    number. The more the dyads genuinely differ, the weaker the prior and the
    more each dyad's own record speaks.

    Estimated, never tuned: nothing here is fitted to an outcome, so the
    result stays as recountable from the archive as the raw frequencies are.
    """
    usable = [(k, n) for k, n in counts.values() if n > 0]
    total = sum(n for _, n in usable)
    if len(usable) < 2 or total <= 0:
        return float("inf")
    # Episode-weighted spread of the observed per-dyad rates…
    spread = sum(n * (k / n - pooled) ** 2 for k, n in usable) / total
    # …minus the part binomial sampling alone would produce at these sizes.
    within = pooled * (1.0 - pooled) * (len(usable) - 1) / total
    between = spread - within
    if between <= 0.0 or pooled <= 0.0 or pooled >= 1.0:
        return float("inf")
    return max(0.0, pooled * (1.0 - pooled) / between - 1.0)


def _dyad_rate(
    counts: dict[str, tuple[int, int]], dyad_id: str, pooled: float, strength: float
) -> float:
    """One dyad's continuation rate, its own record shrunk toward the pooled
    rate in proportion to how thin that record is: (k + m·p) / (n + m).

    A dyad with two episodes barely moves off the pooled rate; a dyad with two
    hundred is mostly its own number. This is the stage-0 estimator every
    learned model in docs/ml-spec.md has to beat.
    """
    if strength == float("inf"):
        return pooled
    k, n = counts.get(dyad_id, (0, 0))
    return (k + strength * pooled) / (n + strength) if (n + strength) > 0 else pooled


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
    rows = all_dyad_event_rows(db_path)
    if not rows:
        raise ValueError("no dyad-coded events in either store — seed first")
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

    threshold = _significance_threshold(rows, regime_anchor=as_of)
    counts = _episode_counts(
        rows, regime_anchor=as_of, horizon_years=horizon_years, threshold=threshold
    )

    # WHEN the evidence is from, which is not the same as when the archive
    # ends. The wire tier runs 1979–2005 and the years after it hold only a
    # curated spine, so a likelihood stamped as-of 2025 can rest almost
    # entirely on evidence two decades older. Stated, not buried.
    contributing = [
        str(row["event_time"])
        for row in rows
        if row["direction"] == "escalating"
        and (
            threshold <= 0.0
            or (row.get("magnitude") is not None and float(row["magnitude"]) >= threshold)
        )
        and regimes.comparable(as_of, str(row["event_time"]))
    ]
    evidence_span = [min(contributing), max(contributing)] if contributing else None

    # Focal dyads: most conflictual standing baselines IN THE REGION that the
    # archive has actually WATCHED escalate. The evidence bar comes before the
    # ranking, not after it — sorting on baseline alone put a dyad with zero
    # in-regime episodes at the head of the forecast, where the pooled prior it
    # inherited read as a finding about that dyad.
    by_dyad: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_dyad.setdefault(row["dyad_id"], []).append(row)
    regional = [
        (dyad_id, events)
        for dyad_id, events in by_dyad.items()
        if any(e["region_pack"] == region_pack for e in events)
    ]
    evidenced = [
        pair for pair in regional if counts.get(pair[0], (0, 0))[1] >= _MIN_FOCAL_EPISODES
    ]
    # A thin archive is a reason to say less, not to lower the bar silently:
    # fall back to any dyad with an episode at all, and record which bar was met.
    focal_bar = _MIN_FOCAL_EPISODES
    if not evidenced:
        evidenced = [pair for pair in regional if counts.get(pair[0], (0, 0))[1] >= 1]
        focal_bar = 1
    evidenced.sort(key=lambda pair: (pair[1][-1]["baseline"] or 0.0, pair[0]))
    focal = evidenced[:_FOCAL_DYADS]
    continuations = sum(k for k, _ in counts.values())
    episodes = sum(n for _, n in counts.values())
    pooled = continuations / episodes if episodes else 0.5
    strength = _prior_strength(counts, pooled)

    scenarios: list[dict[str, Any]] = []
    for dyad_id, events in focal:
        name = events[-1]["dyad_name"]
        latest = events[-1]
        own_k, own_n = counts.get(dyad_id, (0, 0))
        rate = _dyad_rate(counts, dyad_id, pooled, strength)
        counting = (
            f"{name}'s own record is {own_k} of {own_n} in-regime escalating "
            f"EPISODES (dyad-quarters holding a departure of {threshold:.2f} or "
            f"more from the dyad's own baseline, monetary order "
            f"at {as_of}) followed by another within {horizon_years}y, shrunk "
            f"toward the all-dyad pooled rate {pooled:.4f} "
            f"({continuations} of {episodes}) with prior strength "
            f"{'infinite' if strength == float('inf') else format(strength, '.1f')}"
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
                f"The complement of the escalation rate for {name}: its own "
                f"{own_n - own_k} of {own_n} in-regime dyad-quarter episodes "
                f"were NOT followed within {horizon_years}y, against "
                f"{episodes - continuations} of {episodes} across all dyads."
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
            "pooled_rate": round(pooled, 6),
            "prior_strength": None if strength == float("inf") else round(strength, 4),
            "significance_threshold": round(threshold, 4),
            "significance_percentile": _SIGNIFICANCE_PERCENTILE,
            "focal_episode_bar": focal_bar,
            "evidence_span": evidence_span,
            # Per-dyad numerators and denominators, so every likelihood above
            # is recomputable from this payload without the graph.
            "dyad_counts": {
                dyad_id: list(counts.get(dyad_id, (0, 0))) for dyad_id, _ in focal
            },
            "focal_dyads": [dyad_id for dyad_id, _ in focal],
            "event_count": len(rows),
            "as_of": as_of,
        },
        "method": (
            "regime-gated base rates: an episode is a dyad-quarter holding an "
            f"escalation in the top {(1 - _SIGNIFICANCE_PERCENTILE) * 100:.0f}% "
            "of in-regime departures from the dyad's own baseline; continuation "
            f"counted on the same dyad within {horizon_years}y; per-dyad "
            "frequency partially pooled toward the all-dyad rate by "
            "beta-binomial method of moments; complement pairs sum to 1"
        ),
    }
