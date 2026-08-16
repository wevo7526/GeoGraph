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
    conn: Any = None,
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

    # `conn` lets this run inside the API process, which holds the write lock
    # and therefore cannot open a graph of its own (core/api/work.py).
    archive = (
        structural.PressureArchive.from_conn(conn, region_pack=region_pack)
        if conn is not None
        else structural.PressureArchive.load(db_path, region_pack=region_pack)
    )
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


# ── the calibration walk (2026-08-15) ───────────────────────────────────────


#: Cutoffs per year in the walk. Quarterly is the panel's own grain, and the
#: grain the base rate counts in.
_WALK_PER_YEAR = 4

#: region -> (archive size the walk was computed at, the walk). The walk is a
#: pure function of the rows, so it is cached for the process — and filled by
#: a background job (core/api/work.py) rather than by the first reader, who
#: would otherwise wait out the whole archive read.
CACHE: dict[str, tuple[int, dict[str, Any]]] = {}


def cached(region_pack: str) -> dict[str, Any] | None:
    entry = CACHE.get(region_pack)
    return entry[1] if entry else None


def remember(region_pack: str, size: int, walk_out: dict[str, Any]) -> None:
    CACHE[region_pack] = (size, walk_out)


def is_current(region_pack: str, size: int) -> bool:
    """The archive grows under the convergence loop, so a walk computed at a
    much smaller archive is stale. A few thousand new wire events do not move
    a Brier score, so the bar is proportional rather than exact."""
    entry = CACHE.get(region_pack)
    if entry is None:
        return False
    was = entry[0]
    return was > 0 and abs(size - was) / was < 0.05

#: Reliability bins. Ten would be honest and mostly empty at this sample size;
#: five keeps each bin's count large enough to read.
_RELIABILITY_BINS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

#: The recent era reported beside the whole walk. Density is coverage, not
#: history — see the aggregate's comment.
_RECENT_YEARS = 20


def episode_quarters(rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Per dyad, the sorted quarter indices holding an escalating event.

    Moved here from `scripts/score_forecasts.py` so the live scorer and the
    walk resolve outcomes through ONE implementation — two would drift, and
    the walk's whole claim is that it scores the estimator the freeze uses.
    """
    from core.reasoning import forecasting

    quarters: dict[str, set[int]] = {}
    for row in rows:
        if row["direction"] != "escalating":
            continue
        year, q = forecasting.quarter(str(row["event_time"]))
        quarters.setdefault(row["dyad_id"], set()).add(year * 4 + (q - 1))
    return {dyad: sorted(index) for dyad, index in quarters.items()}


def near_term_outcomes(
    scenarios: list[dict[str, Any]],
    episodes: dict[str, list[int]],
    *,
    as_of: str,
    horizon_quarters: int,
    independent_only: bool = True,
) -> dict[str, bool]:
    """Resolve each scenario against what the archive then recorded.

    ONE CALL PER DYAD BY DEFAULT. The near-term payload names two scenarios per
    focal dyad — `further_escalation` at p and `reversion_to_baseline` at 1 − p
    — which are the same claim stated twice. Scoring both counts every call
    twice, forces the sample's base rate to exactly 0.5 whatever the world did,
    and flatters the skill number by giving the estimator credit for arithmetic
    (measured 2026-08-15: an apparent skill of 0.76 over a 0.5 "base rate" that
    was an artifact of the pairing). The complement resolves as the complement;
    it carries no independent information, so it is not an independent call.
    """
    from core.reasoning import forecasting

    year, q = forecasting.quarter(as_of)
    cutoff_index = year * 4 + (q - 1)
    outcomes: dict[str, bool] = {}
    for scenario in scenarios:
        name = str(scenario["scenario_name"])
        dyad_id = name.split(":", 1)[1] if ":" in name else ""
        escalated = any(
            0 < index - cutoff_index <= horizon_quarters
            for index in episodes.get(dyad_id, [])
        )
        if name.startswith("further_escalation"):
            outcomes[name] = escalated
        elif not independent_only and name.startswith("reversion_to_baseline"):
            outcomes[name] = not escalated
    return outcomes


def walk(
    rows: list[dict[str, Any]],
    *,
    region_pack: str,
    horizon_years: int = 3,
    per_year: int = _WALK_PER_YEAR,
    min_cutoffs: int = 8,
) -> dict[str, Any]:
    """SCORE THE ESTIMATOR OVER HISTORY, at every cutoff whose horizon closed.

    The near-term forecast asks a three-year question, so a call frozen today
    cannot be scored until 2029 — and on 2026-08-15 all eighteen frozen
    forecasts carried `brier_score: null`. A platform whose central claim is
    "forecast, then be scored" had never once been scored, and on that
    schedule would not have been for three years.

    This closes it without touching the estimator or the horizon. The same
    body the live freeze calls (`forecasting.AsofArchive.forecast` — the
    LOCKED "never a backtest-only estimator" rule) is evaluated at each
    historical cutoff, its scenarios resolved against what the archive then
    recorded, and Brier-scored with the same function the live scorer uses.
    The archive at each cutoff is a strict prefix, so the result is genuinely
    out of sample, and it exists TODAY.

    A reliability table rides along, because one aggregate Brier hides the
    question a reader actually has: when it says 70%, does it happen 70% of
    the time?
    """
    from core.reasoning import forecasting

    archive = forecasting.AsofArchive.build(rows)
    episodes = episode_quarters(rows)
    horizon_quarters = horizon_years * 4
    times = sorted({str(r["event_time"])[:10] for r in rows})
    if not times:
        return {
            "region_pack": region_pack, "cutoffs": 0,
            "note": "no dyad-coded events for this region",
        }
    first_year = int(times[0][:4])
    last_year = int(times[-1][:4])
    # ONLY CLOSED HORIZONS. A cutoff whose window still runs past the archive's
    # edge would be scored against a future that has not happened — which is
    # how a walk-forward quietly becomes a lookahead.
    final_year = last_year - horizon_years
    months = [12 // per_year * (i + 1) for i in range(per_year)]

    scored: list[dict[str, Any]] = []
    for year in range(first_year, final_year + 1):
        for month in months:
            day = 30 if month in (6, 9) else 31
            cutoff = f"{year}-{month:02d}-{day:02d}"
            if cutoff > times[-1]:
                continue
            try:
                payload = archive.forecast(
                    "Which focal dyads escalate again within the horizon?",
                    region_pack=region_pack,
                    horizon_years=horizon_years,
                    cutoff=cutoff,
                )
            except Exception:  # noqa: BLE001 - a cutoff too thin to forecast is a skip
                continue
            scenarios = payload.get("scenarios") or []
            outcomes = near_term_outcomes(
                scenarios, episodes, as_of=cutoff, horizon_quarters=horizon_quarters,
            )
            pairs = [
                (float(s["likelihood"]), outcomes[str(s["scenario_name"])])
                for s in scenarios
                if s.get("likelihood") is not None
                and str(s["scenario_name"]) in outcomes
            ]
            if not pairs:
                continue
            scored.append({
                "cutoff": cutoff,
                "brier": round(brier_score(pairs), 4),
                "calls": len(pairs),
                "pairs": pairs,
            })

    if len(scored) < min_cutoffs:
        return {
            "region_pack": region_pack,
            "cutoffs": len(scored),
            "note": (
                f"only {len(scored)} closed-horizon cutoffs cleared the "
                f"estimator's own evidence bar (< {min_cutoffs}) — too few to "
                "report a score"
            ),
        }

    def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        pairs = [pair for row in rows for pair in row["pairs"]]
        if not pairs:
            return {"calls": 0}
        # The reference a Brier score is meaningless without: what predicting
        # the sample's own frequency, every time, would have earned.
        rate = sum(1 for _, outcome in pairs if outcome) / len(pairs)
        reference = brier_score([(rate, outcome) for _, outcome in pairs])
        measured = brier_score(pairs)
        bins = []
        for low, high in zip(_RELIABILITY_BINS[:-1], _RELIABILITY_BINS[1:], strict=True):
            bucket = [
                (p, o) for p, o in pairs
                if (low <= p < high) or (high == 1.0 and p == 1.0)
            ]
            if not bucket:
                continue
            bins.append({
                "band": [low, high],
                "calls": len(bucket),
                "mean_forecast": round(sum(p for p, _ in bucket) / len(bucket), 4),
                "observed_rate": round(
                    sum(1 for _, o in bucket if o) / len(bucket), 4
                ),
            })
        return {
            "cutoffs": len(rows),
            "calls": len(pairs),
            "span": [rows[0]["cutoff"], rows[-1]["cutoff"]],
            "brier": round(measured, 4),
            "base_rate_brier": round(reference, 4),
            "skill": round(1.0 - measured / reference, 4) if reference else None,
            "observed_rate": round(rate, 4),
            "reliability": bins,
        }

    overall = _aggregate(scored)
    # AND THE SAME NUMBERS OVER THE RECENT ERA, because coverage is not
    # history: the archive's density has moved twice (the 1979 wire, then the
    # 2026 modern harvest), and an aggregate that opens in 1920 is dominated
    # by cutoffs whose base rates rest on a handful of coded events. A reader
    # deciding whether to believe today's call wants the recent number.
    recent_cut = f"{int(scored[-1]['cutoff'][:4]) - _RECENT_YEARS}-01-01"
    recent = _aggregate([row for row in scored if row["cutoff"] >= recent_cut])

    return {
        "region_pack": region_pack,
        "horizon_years": horizon_years,
        **overall,
        "recent": {"years": _RECENT_YEARS, **recent},
        "question_difficulty": question_difficulty(
            rows, region_pack=region_pack, since=recent_cut[:4],
        ),
        "by_cutoff": [
            {k: v for k, v in row.items() if k != "pairs"} for row in scored
        ],
        "method": (
            "the live estimator (forecasting.AsofArchive.forecast) re-run at "
            "each quarter-end cutoff whose horizon has since closed, resolved "
            "against the archive's own later episodes and Brier-scored with the "
            "scorer's own function; skill is against predicting the sample's "
            "base rate, so 0 is no better than the frequency and negative is "
            "worse"
        ),
    }


# ── how hard the question actually is ───────────────────────────────────────


def question_difficulty(
    rows: list[dict[str, Any]],
    *,
    region_pack: str,
    since: str,
    horizons: tuple[int, ...] = (1, 2, 3),
) -> dict[str, Any]:
    """The base rate of the near-term question at each horizon, recent era.

    THE FINDING THIS EXISTS TO KEEP VISIBLE (2026-08-16). The scoreboard said
    the estimator has negative skill lately, which reads as "the model is bad".
    The measurement underneath says something more useful: at the shipped
    three-year horizon the question is nearly VACUOUS in the modern era — the
    base rate of "does a focal dyad escalate again" is 0.92 (mena), 0.97
    (china) and 0.92 (eurasia) since 2005, so predicting "yes, always" scores
    almost perfectly and nothing can beat it by much. Over the whole walk the
    same question sits near 0.44, which is why the all-era skill looks strong:
    it is carried by a sparse past where the answer varied.

    A question whose answer is always yes cannot be scored, and RECALIBRATION
    CANNOT FIX IT — fitting a map on old cutoffs and testing on new ones drives
    the fit to "predict 1" and the Brier to zero, which is the base rate
    wearing a model's clothes. What fixes it is a harder question: at one year
    the base rate falls to 0.84 / 0.87 / 0.71, where there is variance to
    predict. That is a change to what the frozen call MEANS
    (`_DEFAULT_HORIZON_YEARS` is documented as the base-rate continuation
    window), so it is reported here rather than made silently.

    The game layer already asks the harder version — `sharp_departure_
    probability` is "above this pair's OWN usual band", not "any escalation at
    all" — which is the shape the near-term mode would need.
    """
    from core.reasoning import forecasting

    archive = forecasting.AsofArchive.build(rows)
    episodes = episode_quarters(rows)
    times = sorted({str(r["event_time"])[:10] for r in rows})
    if not times:
        return {}
    out: list[dict[str, Any]] = []
    for horizon in horizons:
        outcomes: list[float] = []
        last_year = int(times[-1][:4]) - horizon
        for year in range(max(int(since), int(times[0][:4])), last_year + 1):
            for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
                cutoff = f"{year}-{month:02d}-{day:02d}"
                if cutoff > times[-1]:
                    continue
                try:
                    payload = archive.forecast(
                        "difficulty", region_pack=region_pack,
                        horizon_years=horizon, cutoff=cutoff,
                    )
                except Exception:  # noqa: BLE001 - a thin cutoff is a skip
                    continue
                resolved = near_term_outcomes(
                    payload.get("scenarios") or [], episodes,
                    as_of=cutoff, horizon_quarters=horizon * 4,
                )
                outcomes.extend(1.0 if v else 0.0 for v in resolved.values())
        if outcomes:
            out.append({
                "horizon_years": horizon,
                "calls": len(outcomes),
                "base_rate": round(sum(outcomes) / len(outcomes), 4),
            })
    return {
        "since": since,
        "by_horizon": out,
        "note": (
            "the base rate of the question itself: at a base rate near 1 the "
            "question is nearly vacuous and no estimator can show skill "
            "against it — a shorter horizon is the lever, not recalibration"
        ),
    }
