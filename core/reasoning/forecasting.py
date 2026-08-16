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

import numpy as np
import numpy.typing as npt

from core.classifier import escalation
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
    """Every dyad-coded event with its dyad's baseline AS OF that event — the
    input both the live freeze and the walk-forward backtest reason from.

    `baseline` is the dyad's standing EWMA immediately AFTER folding the event
    in, reconstructed from the slots Head B left on the Event (the baseline it
    was measured against, plus its own score) — never the Dyad node's
    present-day scalar. The distinction is the as-of honesty of the whole
    walk: a dyad's latest row at or before a cutoff carries its standing
    baseline AT that cutoff, and at the archive's end the number equals
    `d.ewma_baseline` exactly (same EWMA, same fold). Reading the node's
    standing scalar here was leak point 1 in docs/oos-spec.md — every
    historical cutoff saw today's baseline.
    """
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event)-[:OF_DYAD]->(d:Dyad) "
        "RETURN d.node_id AS dyad_id, d.name AS dyad_name, "
        "e.escalation_baseline AS measured_against, e.goldstein AS goldstein, "
        "e.node_id AS event_id, "
        "e.event_time AS event_time, e.escalation_direction AS direction, "
        "e.escalation_magnitude AS magnitude, e.region_pack AS region_pack "
        "ORDER BY e.event_time, e.node_id",
    )
    for row in rows:
        measured_against = row.pop("measured_against")
        score = row.pop("goldstein")
        row["baseline"] = (
            escalation.update_baseline(float(measured_against), float(score))
            if measured_against is not None and score is not None
            else None
        )
    return rows


def rows_from_conn(conn: Any) -> list[dict[str, Any]]:
    """The same union as `all_dyad_event_rows`, on a connection the caller
    already holds — so the freeze can run inside the API process, which is the
    one process allowed to hold the write lock (core/api/jobs.py)."""
    rows = dyad_event_rows(conn)

    from core.wire import corpus as wire_corpus

    if wire_corpus.installed():
        seen = {str(row["event_id"]) for row in rows}
        rows.extend(
            row for row in wire_corpus.forecast_rows() if row["event_id"] not in seen
        )
        rows.sort(key=lambda r: (str(r["event_time"]), str(r["event_id"])))
    return rows


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
        return rows_from_conn(conn)
    finally:
        kuzu_store.close(conn)


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
    escalations, which degrades to counting them all.

    REFERENCE IMPLEMENTATION: `AsofArchive` computes the same number from its
    arrays, and tests/test_reasoning.py holds the two equal. Change both or
    neither."""
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

    REFERENCE IMPLEMENTATION: `AsofArchive` computes the same counts from its
    arrays, and tests/test_reasoning.py holds the two equal. Change both or
    neither.
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


class AsofArchive:
    """The dyad-event archive as columnar arrays: built ONCE, evaluated at any
    cutoff in milliseconds.

    This is what makes the walk-forward backtest feasible at corpus scale
    while keeping the locked rule intact. The rule ("never a special
    backtest-only estimator") locks the CODE PATH, not statelessness:
    `forecast_from_rows` builds one of these and evaluates it, and the
    backtest builds one of these and evaluates it ~425 times — the same
    `forecast()` body computes every live freeze and every historical quarter.
    Before this, each cutoff re-scanned and re-sorted all 1.31M rows five ways
    (a full boot budget per region); now the scan is paid once at build.

    Semantics are pinned to the reference implementations above
    (`_significance_threshold`, `_episode_counts`) — the threshold is still a
    percentile of the as-of in-regime distribution, so past events are still
    re-classified as the cutoff advances. Nothing is frozen across cutoffs;
    the arrays just make the re-classification a mask instead of a pass.
    """

    #: Key stride packing (dyad_code, quarter_index) into one int64 for a
    #: single np.unique — must exceed any quarter index (year 9999 → 39999).
    _QKEY = 1 << 17

    def __init__(
        self,
        *,
        times: npt.NDArray[np.bytes_],
        dyad_codes: npt.NDArray[np.int64],
        dyad_ids: list[str],
        esc: npt.NDArray[np.bool_],
        mag: npt.NDArray[np.float64],
        qidx: npt.NDArray[np.int64],
        regime_codes: npt.NDArray[np.int64],
        regime_ids: dict[str, int],
        baselines: npt.NDArray[np.float64],
        region_codes: npt.NDArray[np.int64],
        region_names: list[str],
        event_ids: list[str],
        dyad_names: list[str],
    ) -> None:
        self.times = times
        self.dyad_codes = dyad_codes
        self.dyad_ids = dyad_ids
        self.esc = esc
        self.mag = mag
        self.qidx = qidx
        self.regime_codes = regime_codes
        self.regime_ids = regime_ids
        self.baselines = baselines
        self.region_codes = region_codes
        self.region_names = region_names
        self.event_ids = event_ids
        self.dyad_names = dyad_names

    @classmethod
    def build(cls, rows: list[dict[str, Any]]) -> AsofArchive:
        """Columnar form of the row contract, sorted by event_time (stable, so
        ties keep their input order — same 'latest row' as the dict path)."""
        raw_times = [str(row["event_time"]) for row in rows]
        times_arr = np.array(raw_times, dtype="S32") if rows else np.empty(0, dtype="S32")
        order = np.argsort(times_arr, kind="stable")
        times_arr = times_arr[order]
        ordered = [rows[int(i)] for i in order]

        count = len(ordered)
        dyad_list = [str(row["dyad_id"]) for row in ordered]
        if count:
            unique_dyads, dyad_codes = np.unique(np.array(dyad_list), return_inverse=True)
            dyad_ids = [str(d) for d in unique_dyads.tolist()]
        else:
            dyad_codes = np.empty(0, dtype=np.int64)
            dyad_ids = []

        esc = np.fromiter(
            (row["direction"] == "escalating" for row in ordered), dtype=bool, count=count
        )
        mag = np.fromiter(
            (
                float(row["magnitude"]) if row.get("magnitude") is not None else np.nan
                for row in ordered
            ),
            dtype=np.float64,
            count=count,
        )
        baselines = np.fromiter(
            (
                float(row["baseline"]) if row.get("baseline") is not None else np.nan
                for row in ordered
            ),
            dtype=np.float64,
            count=count,
        )
        sorted_times = [str(row["event_time"]) for row in ordered]
        qidx = np.fromiter(
            (
                year * 4 + (quarter_number - 1)
                for year, quarter_number in (quarter(t) for t in sorted_times)
            ),
            dtype=np.int64,
            count=count,
        )

        # Regime membership per event, via the unique dates only — the wire is
        # dense, so 1.3M rows hold ~17k distinct dates.
        regime_ids: dict[str, int] = {}
        if count:
            unique_dates, inverse = np.unique(times_arr, return_inverse=True)
            per_unique = np.empty(len(unique_dates), dtype=np.int64)
            for position, stamp in enumerate(unique_dates.tolist()):
                entry = regimes.regime_at(stamp.decode(), "monetary_order")
                if entry is None:
                    per_unique[position] = -1
                else:
                    per_unique[position] = regime_ids.setdefault(
                        str(entry["id"]), len(regime_ids)
                    )
            regime_codes = per_unique[inverse]
        else:
            regime_codes = np.empty(0, dtype=np.int64)

        region_list = [str(row["region_pack"]) for row in ordered]
        if count:
            unique_regions, region_codes_arr = np.unique(
                np.array(region_list), return_inverse=True
            )
            region_names = [str(r) for r in unique_regions.tolist()]
        else:
            region_codes_arr = np.empty(0, dtype=np.int64)
            region_names = []

        return cls(
            times=times_arr,
            dyad_codes=dyad_codes.astype(np.int64),
            dyad_ids=dyad_ids,
            esc=esc,
            mag=mag,
            qidx=qidx,
            regime_codes=regime_codes,
            regime_ids=regime_ids,
            baselines=baselines,
            region_codes=region_codes_arr.astype(np.int64),
            region_names=region_names,
            event_ids=[str(row["event_id"]) for row in ordered],
            dyad_names=[str(row["dyad_name"]) for row in ordered],
        )

    def _prefix(self, cutoff: str | None) -> int:
        if cutoff is None:
            return len(self.times)
        return int(np.searchsorted(self.times, cutoff.encode("ascii"), side="right"))

    def forecast(
        self,
        question: str,
        *,
        region_pack: str,
        horizon_years: int = _DEFAULT_HORIZON_YEARS,
        cutoff: str | None = None,
    ) -> dict[str, Any]:
        """One near-term payload at one cutoff — the single estimator body
        behind live freezes and every backtest quarter."""
        prefix = self._prefix(cutoff)
        if prefix == 0:
            raise ValueError(
                f"no dyad-coded events at or before {cutoff} — nothing to reason from"
            )

        as_of = self.times[prefix - 1].decode()
        anchor = regimes.regime_at(as_of, "monetary_order")
        anchor_code = -2 if anchor is None else self.regime_ids.get(str(anchor["id"]), -2)

        esc_n = self.esc[:prefix]
        mag_n = self.mag[:prefix]
        in_regime = self.regime_codes[:prefix] == anchor_code
        esc_in_regime = esc_n & in_regime
        graded = esc_in_regime & ~np.isnan(mag_n)

        # The same percentile the reference reads off the sorted list, via a
        # partial sort: sorted(vals)[k] == partition(vals, k)[k].
        vals = mag_n[graded]
        if vals.size:
            k = min(vals.size - 1, int(vals.size * _SIGNIFICANCE_PERCENTILE))
            threshold = float(np.partition(vals, k)[k])
        else:
            threshold = 0.0

        contributing = graded & (mag_n >= threshold) if threshold > 0.0 else esc_in_regime
        contributing_idx = np.nonzero(contributing)[0]
        evidence_span = (
            [self.times[contributing_idx[0]].decode(), self.times[contributing_idx[-1]].decode()]
            if contributing_idx.size
            else None
        )

        # Episodes: unique (dyad, quarter) pairs; a pair continues when the
        # SAME dyad's next episode quarter is within the horizon (the nearest
        # later quarter is within the horizon iff any later one is).
        dyad_count = len(self.dyad_ids)
        episodes_by_dyad = np.zeros(dyad_count, dtype=np.int64)
        continuations_by_dyad = np.zeros(dyad_count, dtype=np.int64)
        if contributing_idx.size:
            keys = (
                self.dyad_codes[contributing_idx] * self._QKEY + self.qidx[contributing_idx]
            )
            unique_keys = np.unique(keys)
            dyad_of = unique_keys // self._QKEY
            quarter_of = unique_keys % self._QKEY
            continued = np.zeros(len(unique_keys), dtype=bool)
            if len(unique_keys) > 1:
                continued[:-1] = (dyad_of[1:] == dyad_of[:-1]) & (
                    quarter_of[1:] - quarter_of[:-1] <= horizon_years * 4
                )
            episodes_by_dyad = np.bincount(dyad_of, minlength=dyad_count)
            continuations_by_dyad = np.bincount(
                dyad_of[continued], minlength=dyad_count
            )
        counts: dict[str, tuple[int, int]] = {
            self.dyad_ids[int(code)]: (
                int(continuations_by_dyad[int(code)]),
                int(episodes_by_dyad[int(code)]),
            )
            for code in np.nonzero(episodes_by_dyad)[0]
        }

        # Latest row per dyad inside the prefix (np.maximum.at is defined for
        # repeated indices, unlike plain fancy assignment), and which dyads the
        # region has touched.
        last_row = np.full(dyad_count, -1, dtype=np.int64)
        np.maximum.at(last_row, self.dyad_codes[:prefix], np.arange(prefix, dtype=np.int64))
        regional_flag = np.zeros(dyad_count, dtype=bool)
        try:
            region_code = self.region_names.index(region_pack)
        except ValueError:
            region_code = -1
        if region_code >= 0:
            in_region = self.region_codes[:prefix] == region_code
            regional_flag[self.dyad_codes[:prefix][in_region]] = True

        regional_codes = np.nonzero(regional_flag)[0]
        evidenced = [
            int(code)
            for code in regional_codes
            if episodes_by_dyad[int(code)] >= _MIN_FOCAL_EPISODES
        ]
        focal_bar = _MIN_FOCAL_EPISODES
        if not evidenced:
            evidenced = [
                int(code) for code in regional_codes if episodes_by_dyad[int(code)] >= 1
            ]
            focal_bar = 1

        def _sort_key(code: int) -> tuple[float, str]:
            value = self.baselines[last_row[code]]
            return (
                0.0 if np.isnan(value) else (float(value) or 0.0),
                self.dyad_ids[code],
            )

        evidenced.sort(key=_sort_key)
        focal = evidenced[:_FOCAL_DYADS]

        continuations = int(continuations_by_dyad.sum())
        episodes = int(episodes_by_dyad.sum())
        pooled = continuations / episodes if episodes else 0.5
        strength = _prior_strength(counts, pooled)

        scenarios: list[dict[str, Any]] = []
        for code in focal:
            latest = int(last_row[code])
            dyad_id = self.dyad_ids[code]
            name = self.dyad_names[latest]
            baseline_value = self.baselines[latest]
            baseline_shown: float | None = (
                None if np.isnan(baseline_value) else float(baseline_value)
            )
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
                    f"{name} carries baseline {baseline_shown} with its latest "
                    f"event {self.event_ids[latest]} "
                    f"({self.times[latest].decode()}). Base rate: "
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
                    self.dyad_ids[code]: list(counts.get(self.dyad_ids[code], (0, 0)))
                    for code in focal
                },
                "focal_dyads": [self.dyad_ids[code] for code in focal],
                # Names travel with the ids: focal dyads are ranked by
                # conflictuality, not roster popularity, so a reader's dyad
                # list may not contain them — without names here the surface
                # printed raw ids as dead links.
                "dyad_names": {
                    self.dyad_ids[code]: self.dyad_names[int(last_row[code])]
                    for code in focal
                },
                "event_count": prefix,
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


def forecast(
    db_path: Path,
    question: str,
    *,
    region_pack: str,
    horizon_years: int = _DEFAULT_HORIZON_YEARS,
    conn: Any = None,
) -> dict[str, Any]:
    """A near-term Forecast payload: mode='near_term', scenario pairs with
    base-rate likelihoods, frozen inputs. The caller stamps generated_at and
    persists — nothing here reads a clock."""
    rows = rows_from_conn(conn) if conn is not None else all_dyad_event_rows(db_path)
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
    (The path IS `AsofArchive.forecast`; a caller holding many cutoffs builds
    the archive once and evaluates it per cutoff, which is the whole backtest.)
    """
    return AsofArchive.build(rows).forecast(
        question, region_pack=region_pack, horizon_years=horizon_years, cutoff=cutoff
    )
