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
    "signed_level",     # this quarter's SIGNED departure, less its running mean
)
#: The single between-dyad feature, isolated so the trainer can ablate it.
BETWEEN_DYAD = ("base_level",)

#: WHAT THE SHIPPED MODEL ACTUALLY USES — and it is TWO of the nine.
#:
#: Measured, not chosen, and re-measured when the data changed. Walk-forward,
#: adding any of the other seven DEGRADES within-dyad ordering, several of them
#: severely: level_4q took it from +0.46 to +0.10, gap_length to +0.03, all
#: nine together to −0.11. They lower pooled squared error, which is what least
#: squares optimises, by tracking differences BETWEEN dyads — and they buy that
#: at the cost of the ordering within one, which is the only thing a forecast
#: is asked for.
#:
#: `base_level` WAS shipped, on the pre-backfill panel, where it cost almost
#: nothing (+0.4236 against persistence's +0.4253) and improved error. The
#: 2006–2026 backfill made it poison: with the panel spanning two coverage
#: regimes, a dyad's running mean intensity stopped describing the dyad and
#: started describing the calendar. It now correlates +0.52 with the quarter
#: and its mean runs 0.215 pre-1990, 1.051 through 2005, 1.815 after — an
#: eightfold drift that is corpus growth, not statecraft. In a walk-forward
#: test the test window always follows the train window, so a feature encoding
#: "later is higher" earns a positive coefficient and then mis-ranks every
#: held-out quarter. It took the shipped model from +0.42 to −0.04.
#:
#: The rest of the vector is kept and still computed: it is what the ablation
#: reads, and a feature that fails today is evidence rather than dead code.
SHIPPED_FEATURES = ("intercept", "level_now")

#: Direct multi-horizon: a separate fit per h, rather than rolling one model
#: forward. Autoregressive rollout compounds its own error and there is no
#: reason to pay that when four fits cost nothing.
HORIZONS: tuple[int, ...] = (1, 2, 3, 4)


def shipped_columns() -> list[int]:
    return [FEATURE_NAMES.index(name) for name in SHIPPED_FEATURES]


def _median_run_and_gap(hot: dict[int, bool], quarter: int) -> tuple[float, float]:
    """(median hot-run length, median quiet-gap length) over the dyad's
    history BEFORE `quarter`.

    Keyed by quarter, so a run does not survive a hole in the series: two hot
    quarters either side of a gap the archive could not cover are not a
    four-quarter run, and counting them as one would invent the persistence
    the model is trying to measure. Falls back to 1.0 — a dyad with no
    completed run yet is unremarkable rather than extreme.
    """
    past = sorted(q for q in hot if q < quarter)
    runs: list[int] = []
    gaps: list[int] = []
    current = current_gap = 0
    previous: int | None = None
    for q in past:
        if previous is not None and q != previous + 1:
            # A discontinuity closes whatever was open; neither a run nor a
            # gap can be measured across quarters that are not there.
            if current:
                runs.append(current)
            if current_gap:
                gaps.append(current_gap)
            current = current_gap = 0
        if hot[q]:
            if current_gap:
                gaps.append(current_gap)
                current_gap = 0
            current += 1
        else:
            if current:
                runs.append(current)
                current = 0
            current_gap += 1
        previous = q
    return (
        float(np.median(runs)) if runs else 1.0,
        float(np.median(gaps)) if gaps else 1.0,
    )


def _trailing_run(hot: dict[int, bool], quarter: int) -> int:
    """Length of the hot run ending AT `quarter`, stopping at any hole."""
    length = 0
    while hot.get(quarter - length, False):
        length += 1
    return length


def _trailing_gap(hot: dict[int, bool], quarter: int) -> int:
    """Quarters since the last hot one, stopping at the first hole — an
    unobserved stretch is not evidence of quiet."""
    back = 0
    while (quarter - back) in hot:
        if hot[quarter - back]:
            return back
        back += 1
    return back


def build_for_dyad(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Feature rows for one dyad's series, ascending by quarter.

    INDEXED BY QUARTER, NOT BY ROW POSITION, and the difference is not
    cosmetic. The panel's coverage floor drops quarters the archive barely
    watched, which puts HOLES in a dyad's series — 51 of 135 MENA dyads, 1,314
    discontinuities. While this function indexed with `intensity[i + h]`, the
    target at "horizon h" was h OBSERVATIONS later, which across a hole can be
    years. That silently scrambled every target on the gapped dyads and the
    walk-forward gate caught it: within-dyad ordering held at the 1990 and
    1994 cuts and flipped to −0.33 at 1998 and −0.26 at 2002, while
    persistence stayed healthy at +0.36 to +0.66. A model does not fail that
    way on hard data; it fails that way on a wrong index.

    So every lag and every target is a lookup on q ± k. A quarter that is not
    in the panel is ABSENT rather than zero: it was dropped for lack of
    coverage, and reading it as a quiet quarter is the exact lie the floor
    exists to prevent.
    """
    ordered = sorted(rows, key=lambda r: int(r["q"]))
    by_quarter = {int(r["q"]): r for r in ordered}
    out: list[dict[str, Any]] = []

    def value_at(quarter: int, field: str) -> float | None:
        row = by_quarter.get(quarter)
        return float(row[field]) if row is not None else None

    def window(quarter: int, span: int, field: str = "intensity") -> list[float]:
        """The present quarters in [q-span+1, q] — absent ones contribute
        nothing rather than a zero."""
        return [
            v for v in (value_at(quarter - k, field) for k in range(span))
            if v is not None
        ]

    for row in ordered:
        q = int(row["q"])
        history = [float(r["intensity"]) for r in ordered if int(r["q"]) < q]
        tone_history = [float(r["tone"]) for r in ordered if int(r["q"]) < q]
        volume_history = [float(r["events"]) for r in ordered if int(r["q"]) < q]
        # Signed departure history — `.get` because a synthetic panel (the game
        # fit's) carries no signed column, and its default zero makes the
        # feature inert there rather than a KeyError.
        signed_history = [
            float(r.get("signed_intensity", 0.0)) for r in ordered if int(r["q"]) < q
        ]

        # "Hot" is relative to the dyad's OWN history to date, so the same
        # absolute departure can be routine for a rivalry and a rupture for a
        # quiet pair — the relational definition the classifier already uses.
        hot_bar = float(np.median([v for v in history if v > 0.0])) if any(history) else 0.0
        hot = {
            int(r["q"]): float(r["intensity"]) > hot_bar and float(r["intensity"]) > 0.0
            for r in ordered
        }

        base = float(np.mean(history)) if history else 0.0
        signed_base = float(np.mean(signed_history)) if signed_history else 0.0
        median_run, median_gap = _median_run_and_gap(hot, q)
        intensity_now = float(row["intensity"])
        signed_now = float(row.get("signed_intensity", 0.0))
        recent_2 = float(np.mean(window(q, 2)))
        recent_4 = float(np.mean(window(q, 4)))
        recent_8 = float(np.mean(window(q, 8)))
        tone_recent = float(np.mean(window(q, 4, "tone")))
        volume_recent = float(np.log1p(sum(window(q, 4, "events"))))
        volume_base = float(
            np.log1p((float(np.mean(volume_history)) if volume_history else 0.0) * 4)
        )
        tone_base = float(np.mean(tone_history)) if tone_history else 0.0

        features = [
            1.0,
            intensity_now - base,
            recent_4 - base,
            _trailing_run(hot, q) / median_run,
            min(_trailing_gap(hot, q) / median_gap, 8.0),
            recent_2 - recent_8,
            tone_recent - tone_base,
            volume_recent - volume_base,
            base,
            # SIGNED level, demeaned within the dyad exactly as level_now is, so
            # it too carries no dyad identity — only which way, and how far, the
            # dyad is departing from its OWN signed history. Kept for the
            # ablation to read; it does not enter SHIPPED_FEATURES until a
            # walk-forward pass on the real panel shows it improves within-dyad
            # ordering, the same bar every other feature here had to clear.
            signed_now - signed_base,
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
        # Both are lookups on q + h. A horizon whose quarter is missing from
        # the panel yields None and the row is dropped for that horizon —
        # never quietly filled from whatever observation happens to sit h
        # positions along.
        point = {h: value_at(q + h, "intensity") for h in HORIZONS}
        level: dict[int, float | None] = {}
        for h in HORIZONS:
            ahead = [value_at(q + k, "intensity") for k in range(1, h + 1)]
            level[h] = (
                float(np.mean([v for v in ahead if v is not None]))
                if all(v is not None for v in ahead) else None
            )
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
            "intensity": intensity_now,
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
