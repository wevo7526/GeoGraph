"""Burst-state features, and why they are all expanding-window.

The exploration finding this module exists to answer: pooled, a model on
"has this dyad escalated recently" scores AUC 0.92; conditioned on the dyad
it scores 0.35, worse than random. Almost all of the apparent skill was the
model learning WHICH dyads are dangerous — which needs no model — while
being blind to WHEN a given dyad moves.

So every feature here is a deviation from that dyad's own history. Not "has
escalated" but "how long has this run lasted against this dyad's typical
run"; not "intensity" but "intensity against this dyad's own trailing level".
A feature expressed that way cannot be spent on dyad identity, because by
construction it has none.

TWO RULES, both load-bearing:

1. EXPANDING WINDOW, NEVER FULL-SERIES. A dyad's "typical run length" must be
   computed from quarters at or before the row being built. Normalising by a
   statistic of the whole series leaks the future into every row and produces
   a walk-forward score that is fiction.
2. The one deliberately between-dyad feature (`base_level`) is kept, alone
   and named, because dyad character is real signal — it is simply not the
   signal that was missing. Keeping it isolated is what lets the ablation in
   the trainer measure its contribution instead of arguing about it.
"""

from __future__ import annotations

from typing import Any

import numpy as np

#: Feature order IS the artifact's contract — weights are stored positionally,
#: so appending is safe and reordering silently rescores every prediction.
FEATURE_NAMES: tuple[str, ...] = (
    "intercept",
    "level_now",        # this quarter's intensity, less the dyad's running mean
    "level_4q",         # last 4 quarters' mean, less the running mean
    "run_length",       # consecutive hot quarters, over the dyad's median run
    "gap_length",       # quarters since the last hot one, over the median gap
    "trend",            # 2q mean minus 8q mean, in the dyad's own units
    "tone_shift",       # recent tone minus running tone
    "volume_shift",     # log recent volume minus running log volume
    "base_level",       # the dyad's running mean intensity (between-dyad)
)
#: The single between-dyad feature, isolated so the trainer can ablate it.
BETWEEN_DYAD = ("base_level",)

#: WHAT THE SHIPPED MODEL ACTUALLY USES — and it is three of the nine.
#:
#: Measured, not chosen. Walk-forward, adding any of the other six DEGRADED
#: within-dyad ordering, several of them severely: level_4q took it from +0.43
#: to +0.10, gap_length to +0.03, and all nine together to −0.11. They lower
#: pooled squared error, which is what least squares optimises, by tracking
#: differences BETWEEN dyads — and they buy that at the cost of the ordering
#: within one, which is the only thing a forecast is asked for.
#:
#: The rest of the vector is kept and still computed: it is what the ablation
#: reads, and a feature that fails today is evidence rather than dead code.
SHIPPED_FEATURES = ("intercept", "level_now", "base_level")

#: Direct multi-horizon: a separate fit per h, rather than rolling one model
#: forward. Autoregressive rollout compounds its own error and there is no
#: reason to pay that when four fits cost nothing.
HORIZONS: tuple[int, ...] = (1, 2, 3, 4)


def shipped_columns() -> list[int]:
    return [FEATURE_NAMES.index(name) for name in SHIPPED_FEATURES]


def _running(values: list[float], index: int) -> float:
    """Mean of everything STRICTLY BEFORE `index`. Zero at the start — a dyad
    with no history yet has no deviation to report, and inventing one from
    the series' own future is the leak this module exists to avoid."""
    return float(np.mean(values[:index])) if index else 0.0


def _median_run_and_gap(hot: list[bool], index: int) -> tuple[float, float]:
    """(median hot-run length, median quiet-gap length) over the dyad's
    history before `index`. Falls back to 1.0 — a dyad with no completed run
    yet is assumed unremarkable rather than extreme."""
    runs: list[int] = []
    gaps: list[int] = []
    current = 0
    current_gap = 0
    for flag in hot[:index]:
        if flag:
            if current_gap:
                gaps.append(current_gap)
                current_gap = 0
            current += 1
        else:
            if current:
                runs.append(current)
                current = 0
            current_gap += 1
    return (
        float(np.median(runs)) if runs else 1.0,
        float(np.median(gaps)) if gaps else 1.0,
    )


def _trailing_run(hot: list[bool], index: int) -> int:
    """Length of the hot run ending AT `index` (0 if this quarter is quiet)."""
    length = 0
    while index - length >= 0 and hot[index - length]:
        length += 1
    return length


def _trailing_gap(hot: list[bool], index: int) -> int:
    """Quarters since the last hot one, counting back from `index`."""
    for back in range(index + 1):
        if hot[index - back]:
            return back
    return index + 1


def build_for_dyad(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Feature rows for one dyad's series, ascending by quarter.

    Every row carries its own features, its targets at each horizon, and the
    quarter it belongs to — the trainer needs the last of those to split by
    time rather than at random.
    """
    intensity = [float(r["intensity"]) for r in rows]
    tone = [float(r["tone"]) for r in rows]
    volume = [float(r["events"]) for r in rows]
    out: list[dict[str, Any]] = []

    for i, row in enumerate(rows):
        # "Hot" is relative to the dyad's OWN history to date, so the same
        # absolute departure can be routine for a rivalry and a rupture for a
        # quiet pair — the relational definition the classifier already uses.
        history = intensity[:i]
        hot_bar = float(np.median([v for v in history if v > 0.0])) if any(history) else 0.0
        hot = [v > hot_bar and v > 0.0 for v in intensity]

        base = _running(intensity, i)
        median_run, median_gap = _median_run_and_gap(hot, i)
        recent_2 = float(np.mean(intensity[max(0, i - 1): i + 1]))
        recent_4 = float(np.mean(intensity[max(0, i - 3): i + 1]))
        recent_8 = float(np.mean(intensity[max(0, i - 7): i + 1]))
        tone_recent = float(np.mean(tone[max(0, i - 3): i + 1]))
        volume_recent = float(np.log1p(sum(volume[max(0, i - 3): i + 1])))
        volume_base = float(np.log1p(_running(volume, i) * 4))

        features = [
            1.0,
            intensity[i] - base,
            recent_4 - base,
            _trailing_run(hot, i) / median_run,
            min(_trailing_gap(hot, i) / median_gap, 8.0),
            recent_2 - recent_8,
            tone_recent - _running(tone, i),
            volume_recent - volume_base,
            base,
        ]
        # TWO TARGETS, because they are two different questions.
        #
        # `point` is the intensity of quarter q+h exactly. It is an extreme
        # value — the largest departure in a single quarter — and extreme
        # values of a spiky series are close to unforecastable; persistence
        # cannot order them either.
        #
        # `level` is the MEAN intensity from q+1 through q+h: how hot this
        # dyad runs over the coming window. That is the quantity a reader
        # actually asks for, it is what the paper book would trade, and
        # averaging is what turns an extreme value back into a level.
        point = {
            h: intensity[i + h] if i + h < len(intensity) else None
            for h in HORIZONS
        }
        level = {
            h: (
                float(np.mean(intensity[i + 1: i + 1 + h]))
                if i + h < len(intensity) else None
            )
            for h in HORIZONS
        }
        # THE WITHIN TRANSFORMATION. Demeaning the features but not the target
        # is the error that sinks a panel model: the target still carries the
        # dyad's level, which is 30% of the variance and far larger than any
        # feature deviation, so least squares spends the deviations explaining
        # BETWEEN-dyad differences and lands on a coefficient that is the wrong
        # SIGN within a dyad. Subtracting the same running mean from the target
        # makes the fit a pure within-dyad regression, which is the only kind
        # whose coefficients answer "when does THIS dyad move".
        #
        # `base` is added back at prediction time to return an absolute level.
        deviation: dict[int, float | None] = {}
        for h in HORIZONS:
            value = level[h]
            deviation[h] = None if value is None else float(value) - base
        out.append({
            "dyad_id": row["dyad_id"],
            "dyad_name": row["dyad_name"],
            "q": row["q"],
            "date": row["date"],
            "x": features,
            "y": point,
            "y_level": level,
            "y_deviation": deviation,
            "base": base,
            "intensity": intensity[i],
        })
    return out


def build(panel: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Feature rows for the whole panel, grouped by dyad so each dyad's
    expanding statistics stay its own."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in panel:
        grouped.setdefault(row["dyad_id"], []).append(row)
    out: list[dict[str, Any]] = []
    for dyad in sorted(grouped):
        out.extend(build_for_dyad(sorted(grouped[dyad], key=lambda r: r["q"])))
    return out


#: Which target a fit is against.
#:   deviation — the coming window's level MINUS the dyad's running mean.
#:               The within-dyad question, and the default.
#:   level     — the coming window's mean intensity, absolutely.
#:   point     — the intensity of quarter q+h exactly (an extreme value).
#: All three are scored on every training run; shipping one without measuring
#: the others would make a choice look like a premise.
TARGETS = ("deviation", "level", "point")
_TARGET_KEYS = {"deviation": "y_deviation", "level": "y_level", "point": "y"}


def matrices(
    feature_rows: list[dict[str, Any]],
    horizon: int,
    *,
    columns: list[int] | None = None,
    target: str = "deviation",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(X, y, quarter, dyad) for one horizon, dropping rows whose target
    falls outside the observed series."""
    if target not in TARGETS:
        raise ValueError(f"target must be one of {TARGETS}, not {target!r}")
    key = _TARGET_KEYS[target]
    usable = [r for r in feature_rows if r[key][horizon] is not None]
    x = np.array([r["x"] for r in usable], dtype=float)
    if columns is not None:
        x = x[:, columns]
    return (
        x,
        np.array([r[key][horizon] for r in usable], dtype=float),
        np.array([r["q"] for r in usable], dtype=int),
        np.array([r["dyad_id"] for r in usable]),
    )


def bases(feature_rows: list[dict[str, Any]], horizon: int, *, target: str) -> np.ndarray:
    """The running mean subtracted from each usable row's target — what must
    be added back to turn a deviation prediction into a level."""
    key = _TARGET_KEYS[target]
    return np.array(
        [r["base"] for r in feature_rows if r[key][horizon] is not None], dtype=float
    )
