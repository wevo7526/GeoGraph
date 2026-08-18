"""Walk-forward skill of event → market cells, on stored measurements.

THE ARCHIVE ALREADY MEASURES. This module scores whether those measurements
predict the NEXT event's move. It never writes a panel row, never re-runs the
event study, and never originates a price — every expected value is a median
of strictly prior, admissible CARs (build-spec §17).

Three matchers, the same three consumers that disagree today:

- kind: Head B cut (`markets.kind_of`) — what the transmission map uses
- dyad: this pair, regime-gated — what `impact._expected_for_dyad` uses
- quad_band: quad class × intensity band, with thin fallback to quad-only —
  what `games.pricing.price_step` uses. This is the ORACLE-CLASS upper bound:
  the event's own class is known, and we ask whether past events of that class
  predicted its CAR.

A fourth matcher, `game_band`, substitutes a predicted (quad, band) for the
event's own — the GAME-CLASS bound. If oracle is good and game is not, the
bottleneck is sequencing, not transmission.

Nothing here is fitted to the score it produces. MIN_CELL, the p-gate and the
headline window are the production constants, not knobs of this walk.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from core.games import duration as duration_module
from core.games import state as state_module
from core.reasoning import markets as markets_module
from core.reasoning import regimes

HEADLINE_WINDOW = markets_module.HEADLINE_WINDOW
MIN_CELL = markets_module.MIN_CELL
CLEAN_P_GATE = markets_module.CLEAN_P_GATE
CONTROL_TICKER = "^GSPC"
SHARED_YIELD_TICKERS = ("DGS3MO", "DGS2", "DGS10")
SHARED_HAVEN_TICKERS = ("GC=F",)

Matcher = Literal["kind", "dyad", "quad_band", "game_band", "naive"]


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _ranks(values: list[float]) -> list[float]:
    """Average ranks for ties, 1-based."""
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg
        i = j + 1
    return ranks


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0.0 or var_y <= 0.0:
        return None
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    return cov / (var_x * var_y) ** 0.5


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    return _pearson(_ranks(xs), _ranks(ys))


def _weekend(date: Any) -> bool:
    try:
        return dt.date.fromisoformat(str(date)[:10]).weekday() >= 4
    except (TypeError, ValueError):
        return False


def _regime_id(date: Any) -> str | None:
    entry = regimes.regime_at(str(date)[:10], "monetary_order")
    return str(entry["id"]) if entry else None


def _overlapping(row: Mapping[str, Any]) -> bool:
    if row.get("overlapping") is True:
        return True
    return str(row.get("status") or "") == "overlapping"


def _clean_prior(row: Mapping[str, Any], *, p_gate: float | None) -> bool:
    if _overlapping(row):
        return False
    if p_gate is None:
        return True
    p_value = row.get("p_value")
    if p_value is None:
        return True
    try:
        return float(p_value) < p_gate
    except (TypeError, ValueError):
        return True


def happening_key(row: Mapping[str, Any]) -> tuple[str, str, float]:
    """A day and a measured move identify the happening, not the GDELT id."""
    return (
        str(row.get("date") or "")[:10],
        str(row.get("ticker") or ""),
        round(float(row["ar"]), 4),
    )


def is_gdelt(event_id: Any) -> bool:
    return str(event_id or "").startswith("event:gdelt-")


def observation_from_row(
    row: Mapping[str, Any], *, ticker: str, market_type: str | None = None,
    market_id: str | None = None, pack: str | None = None,
) -> dict[str, Any] | None:
    """Lift a `market_rows` / panel run into the scorer's observation shape."""
    ar = row.get("ar", row.get("abnormal_return"))
    if ar is None:
        return None
    date = str(row.get("date") or row.get("event_time") or "")
    if not date:
        return None
    direction = row.get("direction") or row.get("escalation_direction")
    magnitude = row.get("magnitude")
    if magnitude is not None:
        try:
            magnitude = float(magnitude)
        except (TypeError, ValueError):
            magnitude = None
    initiator = row.get("initiator_id")
    target = row.get("target_id")
    dyad = row.get("dyad_id")
    if not dyad and initiator and target:
        from core.classifier import escalation

        dyad = escalation.dyad_id(str(initiator), str(target))
    event_id = str(row.get("event_id") or row.get("event_node_id") or "")
    return {
        "event_id": event_id,
        "date": date,
        "ticker": ticker,
        "market_id": market_id or str(row.get("market_id") or f"market:{ticker}"),
        "market_type": market_type or str(row.get("market_type") or ""),
        "pack": pack or str(row.get("pack") or row.get("region_pack") or ""),
        "ar": float(ar),
        "window": str(row.get("window") or HEADLINE_WINDOW),
        "overlapping": _overlapping(row),
        "p_value": row.get("p_value"),
        "t_stat": row.get("t_stat"),
        "kind": markets_module.kind_of(direction, magnitude),
        "direction": direction,
        "magnitude": magnitude,
        "quad_class": row.get("quad_class"),
        "dyad_id": dyad,
        "first_mover": bool(row.get("first_mover")),
    }


def _metrics(pairs: list[tuple[float, float, float]]) -> dict[str, Any]:
    """pairs: (expected, realized, naive) for events that had an expected."""
    if not pairs:
        return {
            "n": 0, "coverage": 0.0, "sign_hit": None, "spearman": None,
            "mae": None, "mae_naive": None, "mae_zero": None, "beats_naive": None,
            "share_positive_reliability": None,
        }
    expected = [p[0] for p in pairs]
    realized = [p[1] for p in pairs]
    naive = [p[2] for p in pairs]
    sign_hits = [
        (e > 0) == (r > 0) for e, r in zip(expected, realized, strict=True)
        if e != 0.0 and r != 0.0
    ]
    mae = sum(abs(e - r) for e, r in zip(expected, realized, strict=True)) / len(pairs)
    mae_naive = sum(abs(n - r) for n, r in zip(naive, realized, strict=True)) / len(pairs)
    mae_zero = sum(abs(r) for r in realized) / len(pairs)
    predicted_pos = sum(1 for e in expected if e > 0) / len(expected)
    realized_pos = sum(1 for r in realized if r > 0) / len(realized)
    rho = spearman(expected, realized)
    return {
        "n": len(pairs),
        "coverage": None,  # filled by the caller, who knows the scored universe
        "sign_hit": round(sum(sign_hits) / len(sign_hits), 4) if sign_hits else None,
        "spearman": None if rho is None else round(float(rho), 4),
        "mae": round(mae, 6),
        "mae_naive": round(mae_naive, 6),
        "mae_zero": round(mae_zero, 6),
        "beats_naive": mae < mae_naive,
        "share_positive_reliability": {
            "predicted": round(predicted_pos, 4),
            "realized": round(realized_pos, 4),
        },
    }


def _fill_coverage(block: dict[str, Any], scored: int, universe: int) -> dict[str, Any]:
    block["coverage"] = round(scored / universe, 4) if universe else 0.0
    block["universe"] = universe
    return block


def walk(
    observations: list[dict[str, Any]],
    *,
    matchers: tuple[str, ...] = ("kind", "dyad", "quad_band"),
    clean: bool = True,
    p_gate: float | None = CLEAN_P_GATE,
    min_cell: int = MIN_CELL,
    window: str = HEADLINE_WINDOW,
    game_first_steps: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Time-ordered leave-one-out: each event is scored from strictly prior CARs.

    `game_first_steps` maps event_id → {quad, intensity_band} for the game-class
    matcher. Oracle class is `quad_band` (the event's own coding).
    """
    rows = [
        obs for obs in observations
        if obs.get("ar") is not None and str(obs.get("window") or window) == window
    ]
    rows.sort(key=lambda r: (str(r["date"]), str(r.get("event_id") or "")))

    histories: dict[tuple[str, ...], list[float]] = defaultdict(list)
    naive_hist: dict[tuple[str, str], list[float]] = defaultdict(list)
    dyad_mags: dict[str, list[float]] = defaultdict(list)
    seen_happenings: set[tuple[str, str, float]] = set()
    scored_happenings: set[tuple[str, str, float]] = set()

    pairs: dict[str, list[tuple[float, float, float]]] = {m: [] for m in matchers}
    strata: dict[str, dict[str, list[tuple[float, float, float]]]] = {
        m: defaultdict(list) for m in matchers
    }
    universe = 0
    skipped_dupes = 0

    def _append_history(obs: dict[str, Any], *, regime: str, band: int) -> None:
        ticker = str(obs["ticker"])
        kind = str(obs["kind"])
        dyad = str(obs.get("dyad_id") or "")
        quad = str(obs.get("quad_class") or "")
        histories[("kind", ticker, kind, regime)].append(float(obs["ar"]))
        if dyad:
            histories[("dyad", ticker, dyad, regime)].append(float(obs["ar"]))
        if quad:
            histories[("quad_band", ticker, quad, str(band), regime)].append(
                float(obs["ar"])
            )
            histories[("quad", ticker, quad, regime)].append(float(obs["ar"]))
        naive_hist[(ticker, regime)].append(float(obs["ar"]))

    def _lookup(key: tuple[str, ...], fallback: tuple[str, ...] | None) -> list[float]:
        values = histories.get(key) or []
        if len(values) >= min_cell:
            return values
        if fallback is not None:
            loose = histories.get(fallback) or []
            if len(loose) >= min_cell:
                return loose
        return []

    for obs in rows:
        happening = happening_key(obs)
        if is_gdelt(obs.get("event_id")) and happening in scored_happenings:
            skipped_dupes += 1
            continue
        scored_happenings.add(happening)
        universe += 1

        regime = _regime_id(obs["date"]) or ""
        ticker = str(obs["ticker"])
        dyad = str(obs.get("dyad_id") or "")
        mag = obs.get("magnitude")
        past_mags = list(dyad_mags.get(dyad, []))
        scale = state_module.dyad_scale(past_mags) if past_mags else 0.0
        band = state_module.intensity_band(float(mag or 0.0), scale)
        naive_values = naive_hist.get((ticker, regime)) or []
        naive = _median(naive_values) if naive_values else 0.0

        def _record(
            matcher: str, values: list[float], *, row: dict[str, Any], naive_value: float,
        ) -> None:
            if len(values) < min_cell:
                return
            expected = _median(values)
            pair = (expected, float(row["ar"]), naive_value)
            pairs[matcher].append(pair)
            tick = str(row["ticker"])
            strata[matcher][f"ticker:{tick}"].append(pair)
            market_type = str(row.get("market_type") or "unknown")
            strata[matcher][f"market_type:{market_type}"].append(pair)
            strata[matcher][f"kind:{row['kind']}"].append(pair)
            strata[matcher]["weekend" if _weekend(row["date"]) else "weekday"].append(pair)
            if _overlapping(row):
                strata[matcher]["overlapping"].append(pair)
            else:
                strata[matcher]["clean_target"].append(pair)
            pack = str(row.get("pack") or "")
            if pack:
                strata[matcher][f"pack:{pack}"].append(pair)
            if tick == CONTROL_TICKER:
                strata[matcher]["control_gspc"].append(pair)
            if tick in SHARED_YIELD_TICKERS:
                strata[matcher]["sovereign_yield_shared"].append(pair)

        if "kind" in matchers:
            _record(
                "kind",
                _lookup(("kind", ticker, str(obs["kind"]), regime), None),
                row=obs, naive_value=naive,
            )
        if "dyad" in matchers and dyad:
            _record(
                "dyad", _lookup(("dyad", ticker, dyad, regime), None),
                row=obs, naive_value=naive,
            )
        if "quad_band" in matchers and obs.get("quad_class"):
            _record(
                "quad_band",
                _lookup(
                    ("quad_band", ticker, str(obs["quad_class"]), str(band), regime),
                    ("quad", ticker, str(obs["quad_class"]), regime),
                ),
                row=obs, naive_value=naive,
            )
        if "game_band" in matchers and game_first_steps:
            step = game_first_steps.get(str(obs.get("event_id") or ""))
            if step:
                _record(
                    "game_band",
                    _lookup(
                        (
                            "quad_band", ticker, str(step["quad"]),
                            str(int(step["intensity_band"])), regime,
                        ),
                        ("quad", ticker, str(step["quad"]), regime),
                    ),
                    row=obs, naive_value=naive,
                )

        # Priors for later events: the event itself is never in its own cell.
        if mag is not None and dyad:
            dyad_mags[dyad].append(float(mag))
        if clean and not _clean_prior(obs, p_gate=p_gate):
            continue
        if is_gdelt(obs.get("event_id")) and happening in seen_happenings:
            continue
        seen_happenings.add(happening)
        _append_history(obs, regime=regime, band=band)

    by_matcher: dict[str, Any] = {}
    for matcher in matchers:
        block = _metrics(pairs[matcher])
        _fill_coverage(block, block["n"], universe)
        by_stratum = {}
        for name, stratum_pairs in strata[matcher].items():
            inner = _metrics(stratum_pairs)
            inner["coverage"] = (
                round(inner["n"] / universe, 4) if universe else 0.0
            )
            by_stratum[name] = inner
        by_matcher[matcher] = {**block, "strata": by_stratum}

    return {
        "window": window,
        "clean": clean,
        "p_gate": p_gate if clean else None,
        "min_cell": min_cell,
        "universe": universe,
        "gdelt_dupes_skipped": skipped_dupes,
        "matchers": by_matcher,
        "method": (
            "leave-one-out walk of stored CARs, strictly prior, regime-keyed; "
            "expected is the median of an admissible cell of at least "
            f"{min_cell}; absent cells are None, never 0.0; nothing is fitted"
        ),
    }


def compact_skill(report: dict[str, Any]) -> dict[str, Any]:
    """The block that rides on a markets story payload — small, by type."""
    kind = (report.get("matchers") or {}).get("kind") or {}
    by_type = {
        name.split(":", 1)[1]: {
            "n": row["n"],
            "coverage": row["coverage"],
            "sign_hit": row["sign_hit"],
            "mae": row["mae"],
            "mae_naive": row["mae_naive"],
            "beats_naive": row["beats_naive"],
        }
        for name, row in (kind.get("strata") or {}).items()
        if name.startswith("market_type:")
    }
    yields = (kind.get("strata") or {}).get("sovereign_yield_shared") or {}
    control = (kind.get("strata") or {}).get("control_gspc") or {}
    return {
        "window": report.get("window"),
        "clean": report.get("clean"),
        "universe": report.get("universe"),
        "kind": {
            "n": kind.get("n"),
            "coverage": kind.get("coverage"),
            "sign_hit": kind.get("sign_hit"),
            "mae": kind.get("mae"),
            "mae_naive": kind.get("mae_naive"),
            "beats_naive": kind.get("beats_naive"),
        },
        "by_market_type": by_type,
        "sovereign_yield": {
            "n": yields.get("n"),
            "sign_hit": yields.get("sign_hit"),
            "beats_naive": yields.get("beats_naive"),
        } if yields else None,
        "gspc_control": {
            "n": control.get("n"),
            "sign_hit": control.get("sign_hit"),
            "beats_naive": control.get("beats_naive"),
        } if control else None,
        "method": report.get("method"),
    }


def duration_ordering(
    implied: list[Mapping[str, Any]],
    simulated: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Rank correlation of market-implied persistence vs the game's run length.

    Validation only: the mapping from long-end share to quarters is not
    established, and this does not fit one.
    """
    sim = {
        str(row["dyad_id"]): float(row["simulated_persistence"])
        for row in simulated
        if row.get("dyad_id") is not None
        and row.get("simulated_persistence") is not None
    }
    xs: list[float] = []
    ys: list[float] = []
    paired: list[str] = []
    for row in implied:
        dyad = str(row.get("dyad_id") or "")
        if not dyad or row.get("implied_persistence") is None or dyad not in sim:
            continue
        xs.append(float(row["implied_persistence"]))
        ys.append(sim[dyad])
        paired.append(dyad)
    rho = spearman(xs, ys)
    return {
        "n": len(paired),
        "spearman": None if rho is None else round(float(rho), 4),
        "note": (
            "ordering across dyads, not a mapping from long-end share to "
            "quarters — that mapping is not established"
        ),
    }


def belly_adds_ordering(
    events: list[Mapping[str, Any]],
    simulated: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Whether using DGS2 as the long end (or the short) improves rank skill.

    `events` are per-event tenor readings with optional `belly`. Does not
    change `duration.implied_persistence`; it only reports the comparison.
    """
    sim = {
        str(row["dyad_id"]): float(row["simulated_persistence"])
        for row in simulated if row.get("simulated_persistence") is not None
    }

    def _rank(long_end: str, short: str) -> float | None:
        by_dyad: dict[str, list[float]] = defaultdict(list)
        for event in events:
            readings = {
                label: float(event[label])
                for label in ("front", "belly", "long")
                if event.get(label) is not None
            }
            if long_end not in readings or short not in readings:
                continue
            share = duration_module.implied_persistence(
                {short: readings[short], "long": readings[long_end]}
            )
            dyad = str(event.get("dyad_id") or "")
            if share is None or dyad not in sim:
                continue
            by_dyad[dyad].append(share)
        xs, ys = [], []
        for dyad, shares in by_dyad.items():
            xs.append(_median(shares))
            ys.append(sim[dyad])
        return spearman(xs, ys)

    front_long = _rank("long", "front")
    front_belly = _rank("belly", "front")
    belly_long = _rank("long", "belly")
    return {
        "front_vs_long": None if front_long is None else round(front_long, 4),
        "front_vs_belly": None if front_belly is None else round(front_belly, 4),
        "belly_vs_long": None if belly_long is None else round(belly_long, 4),
        "belly_improves_front_long": (
            front_belly is not None and front_long is not None
            and abs(front_belly) > abs(front_long)
        ) or (
            belly_long is not None and front_long is not None
            and abs(belly_long) > abs(front_long)
        ),
        "note": "comparison only; the production statistic stays front vs long",
    }


def remeasure_justified(report: dict[str, Any]) -> dict[str, Any]:
    """The Slice 7 gate: do not re-run the archive study unless clean cells
    already beat the naive AND still lose badly to a zero forecast — that is
    the signature of an estimator bottleneck, not a hygiene one.

    Default is no. A live panel has to present that pattern; fixtures cannot
    authorise a full-archive re-measure.
    """
    kind = (report.get("matchers") or {}).get("kind") or {}
    beats = bool(kind.get("beats_naive"))
    mae = kind.get("mae")
    mae_zero = kind.get("mae_zero")
    coverage = float(kind.get("coverage") or 0.0)
    # Hygiene is still the lever if clean cells do not beat the naive.
    if not beats:
        return {
            "justified": False,
            "reason": (
                "clean kind-matched cells do not beat the unconditional median; "
                "re-measurement is not the next lever"
            ),
        }
    if mae is None or mae_zero is None:
        return {"justified": False, "reason": "no MAE to compare"}
    if coverage < 0.3:
        return {
            "justified": False,
            "reason": "coverage is too thin to blame the estimator",
        }
    if mae < mae_zero:
        return {
            "justified": False,
            "reason": (
                "clean cells beat both the naive median and a zero forecast; "
                "the estimator is not the bottleneck"
            ),
        }
    return {
        "justified": False,
        "reason": (
            "even when MAE loses to a zero forecast, a full-archive re-measure "
            "is still refused from this gate — run a spine-only estimation-"
            "window study first, by hand"
        ),
        "next": "spine-only ESTIMATION_PERIODS['day'] 60 vs 120, not the wire",
    }


def observations_from_panel(
    conn: Any, panel: Any, pack_name: str, *, window: str = HEADLINE_WINDOW,
) -> Iterable[dict[str, Any]]:
    """Stream one pack's headline CARs, ticker by ticker — never the whole table."""
    from core import packs
    from core.panel import pg_store

    pack = packs.load(pack_name)
    coding = markets_module.coding_for(conn, pack_name)
    for market in pack.markets:
        ticker = str(market["ticker"])
        market_type = str(market.get("market_type") or "")
        market_id = str(market.get("id") or f"market:{ticker}")
        for run in pg_store.computed_runs(panel, ticker=ticker, window=window):
            meta = coding.get(str(run["event_node_id"]))
            if meta is None or run.get("abnormal_return") is None:
                continue
            region = meta.get("region_pack") or ""
            if region not in (pack_name, ""):
                continue
            lifted = observation_from_row(
                {
                    **run,
                    "event_id": run["event_node_id"],
                    "date": meta.get("date"),
                    "direction": meta.get("direction"),
                    "magnitude": meta.get("magnitude"),
                    "quad_class": meta.get("quad_class"),
                    "initiator_id": meta.get("initiator_id"),
                    "target_id": meta.get("target_id"),
                    "ar": run["abnormal_return"],
                    "overlapping": str(run.get("status") or "") == "overlapping",
                },
                ticker=ticker, market_type=market_type, market_id=market_id,
                pack=pack_name,
            )
            if lifted is not None:
                yield lifted
