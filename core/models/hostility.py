"""IS THIS PAIR IN A MILITARISED DISPUTE? — the trained relationship classifier.

WHY THIS EXISTS. Whether a pair is an adversary, a rival or an ally decided
which game the solver plays for it, and it was decided by two hand-set
numbers: a coercive SHARE of 0.25 and a coercive COUNT of 300. Both were
calibrated against counts that turned out to be measuring something else (see
`classifier.coercion`), and neither survived contact with the archive once the
counting was fixed:

  * The United States and Russia came out `rival` — "a declared rivalry
    conducted in argument" — on a 9.5% coercive share, because the share's
    denominator is every coded interaction and the wire codes far more
    diplomacy than force. A pair can be in a shooting war and still spend most
    of its coverage on meetings about the shooting.
  * Russia and Ukraine came out the same way, at 4.5%, in the third year of an
    invasion.

A share cannot answer this question and a count cannot either, because both
scale with COVERAGE, which is not a property of the relationship. What can
answer it is a label: the Correlates of War project's Militarized Interstate
Disputes are a curated, scholarly record of exactly the thing the thresholds
were guessing at, and the archive has held them since Phase 3.

THE LABEL. MIDB 5.0 gives every dispute's participants with the side they took
(`sidea`) and the years they were in it. A dyad-year is positive when the two
states were in the same dispute on OPPOSITE sides. Over the three packs'
rosters that is 895 positive dyad-years across 180 pairs, 1979-2014, spread
evenly enough across the period to train on (79-174 per five-year block). The
top pairs are the ones a reader would name: North and South Korea, Iran and
Iraq, Israel and Syria, Armenia and Azerbaijan, the United States and Iran.

THE HONEST LIMITS, stated because they bound what this may be used for:

  * MID 5.0 ENDS IN 2014 and the wire runs to 2026. The model is fitted where
    the label exists and applied where it does not, which is the whole point —
    but it means the recent era is an extrapolation, not a measurement, and
    the artifact says so.
  * The label is DISPUTE PARTICIPATION, not hostility in general. A pair can
    be bitterly adversarial without a militarised dispute in a given year
    (sanctions, subversion, proxy war), and the model will score those low. It
    is a floor on adversariality, not a full ordering of it.
  * The gate is OUT OF SAMPLE IN TIME (fit to 1979-2004, scored on 2005-2014)
    and against three baselines that cost nothing: the shipped threshold rule,
    the declared standing alone, and the raw coercion count. A model that
    cannot beat all three is not written — `family` keeps its rules and says
    so, the same contract `models.dynamics` holds.
  * READ THE THREE BASELINE NUMBERS CORRECTLY, because two of them are
    BINARY. `shipped_rule` and `declared_rivalry` are indicators, so their
    "AUC" is arithmetically (sensitivity + specificity) / 2 and is not
    comparable to a continuous score's. 0.533 for the shipped rule means its
    operating point carries almost no discriminative power — a true finding,
    and the one that motivated this — but the HONEST margin to quote is
    against `coercion_count`, which is continuous: +0.0495 AUC.
  * The claim is a BETWEEN-PAIR ordering — which pairs are adversaries — and
    not a within-dyad one. `models.dynamics` and `models.intensity` are gated
    within dyad because they rank a pair's own quarters; this ranks pairs
    against each other, so a within-dyad gate would be the wrong test. An
    adversarial re-check of a within-dyad edge over the raw count found it
    not significant on a paired bootstrap, which is consistent with that: it
    is not what this model is for.
"""

from __future__ import annotations

import csv
import json
import math
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

#: Where COW's raw files live; the same directory `games.family.ally_windows`
#: reads its alliance windows from, and for the same reason — a fit that
#: cannot be reproduced from the commit is not a fit.
RAW_DIR = Path(
    os.getenv("GEOGRAPH_RAW_DIR")
    or Path(__file__).resolve().parents[2] / "data" / "raw"
)
MIDB = "MIDB 5.0.csv"

#: The committed artifact. Pooled across packs on purpose: a militarised
#: dispute is the same event whichever regional lens holds it, and the label
#: is thin enough per region that three fits would each be noise.
ARTIFACT = Path(__file__).resolve().parents[2] / "models" / "hostility.json"

#: The label's last year. Fitting stops before it; scoring runs after it.
LABEL_LAST_YEAR = 2014
FIT_LAST_YEAR = 2004

#: The features, in order. Every one is computable for a year the label does
#: not cover, which is what lets the model be applied to 2015-2026.
FEATURES = (
    "log_events",        # coverage — the confounder, given to the model explicitly
    "coercion_share",    # the old rule's numerator over its denominator
    "log_coercion",      # the count, on a scale where 3 and 30 differ more than 300 and 330
    "severe_share",      # share of events at Goldstein <= -7 (force, not argument)
    "max_departure",     # the year's largest escalation against the pair's own baseline
    "mean_goldstein",    # the tone, kept as a number and never as a verdict
    "fight_share",       # CAMEO root 19 as a share of coercion
    "declared_rivalry",  # the sourced standing, as a feature rather than an override
)

#: MEASURED AND DROPPED, recorded so the next reader does not re-add them.
#: Persistence is the strongest thing anyone knows about disputes, so a lagged
#: coercion count and a year-on-year trend were the obvious additions — and on
#: the held-out decade they moved the AUC by −0.0001 (0.8469 → 0.8468) and took
#: weights of +0.01. The volume and tone features already carry the state.
#: A separate fight COUNT (beside the share) is the same story: it substitutes
#: for the coercion count rather than adding to it.
MEASURED_AND_DROPPED = (
    "log_coercion_last_year", "coercion_trend", "log_fight",
)

#: A model that cannot beat every one of these is not worth loading.
BASELINES = ("shipped_rule", "declared_rivalry", "coercion_count")


# ── the label ──────────────────────────────────────────────────────────────


def mid_dyad_years(
    ccode_to_id: dict[int, str], *, first_year: int = 1979,
    last_year: int = LABEL_LAST_YEAR, midb: Path | None = None,
) -> set[tuple[str, int]]:
    """(dyad_id, year) for every year two roster states were in the same
    militarised dispute ON OPPOSITE SIDES.

    Opposite sides is the whole rule: MIDB lists participants with `sidea`,
    and two states on the SAME side of a dispute are allies in it, not
    adversaries — reading mere co-participation as hostility is the identical
    mistake the wire makes with joint operations.
    """
    from core.classifier import escalation

    path = midb or (RAW_DIR / MIDB)
    if not path.exists():
        return set()
    by_dispute: dict[str, list[tuple[int, int, int, int]]] = {}
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                by_dispute.setdefault(row["dispnum"].strip(), []).append((
                    int(row["ccode"]), int(row["sidea"]),
                    int(row["styear"]), int(row["endyear"]),
                ))
            except (KeyError, ValueError):
                continue
    out: set[tuple[str, int]] = set()
    for parts in by_dispute.values():
        side_a = [p for p in parts if p[1] == 1]
        side_b = [p for p in parts if p[1] == 0]
        for ccode_a, _, start_a, end_a in side_a:
            for ccode_b, _, start_b, end_b in side_b:
                actor_a, actor_b = ccode_to_id.get(ccode_a), ccode_to_id.get(ccode_b)
                if not actor_a or not actor_b or actor_a == actor_b:
                    continue
                dyad = escalation.dyad_id(actor_a, actor_b)
                lo = max(start_a, start_b, first_year)
                hi = min(end_a, end_b, last_year)
                for year in range(lo, hi + 1):
                    out.add((dyad, year))
    return out


# ── the features ───────────────────────────────────────────────────────────


def dyad_year_rows(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, int], dict[str, float]]:
    """Corpus rows → one feature row per (dyad, year).

    Reads the corpus, never a live store: an artifact committed to the repo
    must be reproducible from the commit alone.
    """
    cells: dict[tuple[str, int], dict[str, float]] = {}
    for row in rows:
        stamp = str(row.get("event_time") or "")
        if len(stamp) < 4 or not stamp[:4].isdigit():
            continue
        key = (str(row.get("dyad_id")), int(stamp[:4]))
        cell = cells.setdefault(key, {
            "events": 0.0, "coercion": 0.0, "severe": 0.0, "fight": 0.0,
            "goldstein_sum": 0.0, "goldstein_n": 0.0, "max_departure": 0.0,
        })
        cell["events"] += 1
        goldstein = row.get("goldstein")
        if goldstein is not None:
            cell["goldstein_sum"] += float(goldstein)
            cell["goldstein_n"] += 1
            if float(goldstein) <= -7.0:
                cell["severe"] += 1
        if row.get("coercion"):
            cell["coercion"] += 1
            if str(row.get("action_cameo_code") or "")[:2] == "19":
                cell["fight"] += 1
        magnitude = row.get("escalation_magnitude")
        if magnitude is not None and row.get("escalation_direction") == "escalating":
            cell["max_departure"] = max(cell["max_departure"], float(magnitude))
    return cells


def design_row(
    cell: dict[str, float], *, declared_rivalry: bool, previous: dict[str, float] | None = None
) -> list[float]:
    """`previous` is accepted and unused: see MEASURED_AND_DROPPED."""
    events = max(cell["events"], 1.0)
    coercion = cell["coercion"]
    return [
        math.log1p(events),
        coercion / events,
        math.log1p(coercion),
        cell["severe"] / events,
        cell["max_departure"],
        cell["goldstein_sum"] / cell["goldstein_n"] if cell["goldstein_n"] else 0.0,
        cell["fight"] / max(coercion, 1.0),
        1.0 if declared_rivalry else 0.0,
    ]


# ── the fit ────────────────────────────────────────────────────────────────


def _standardise(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = matrix.mean(axis=0)
    scale = matrix.std(axis=0)
    scale[scale < 1e-9] = 1.0
    return (matrix - mean) / scale, mean, scale


def fit_logistic(
    design: np.ndarray, labels: np.ndarray, *, l2: float = 1.0
) -> np.ndarray:
    """L2-penalised logistic regression by L-BFGS-B.

    scipy rather than a dependency: the repo already solves the correlated
    equilibrium's dual this way, and a model that needs an extra package to
    retrain is a model nobody retrains.
    """
    from scipy.optimize import minimize

    n_features = design.shape[1]
    padded = np.hstack([np.ones((design.shape[0], 1)), design])

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        z = padded @ theta
        # log(1 + exp(z)) without overflowing on the tail.
        log_likelihood = float(np.sum(labels * z - np.logaddexp(0.0, z)))
        penalty = l2 * float(theta[1:] @ theta[1:]) / 2.0
        probability = 1.0 / (1.0 + np.exp(-z))
        gradient = padded.T @ (labels - probability)
        gradient[1:] -= l2 * theta[1:]
        return -(log_likelihood - penalty), -gradient

    start = np.zeros(n_features + 1)
    result = minimize(objective, start, jac=True, method="L-BFGS-B")
    return np.asarray(result.x, dtype=float)


def predict_proba(theta: np.ndarray, design: np.ndarray) -> np.ndarray:
    padded = np.hstack([np.ones((design.shape[0], 1)), design])
    return np.asarray(1.0 / (1.0 + np.exp(-(padded @ theta))), dtype=float)


def auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Rank AUC, ties averaged. Returns 0.5 when a class is missing — an
    undefined score reported as "no information", never as a pass."""
    positive = labels == 1
    if positive.sum() == 0 or (~positive).sum() == 0:
        return 0.5
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1, dtype=float)
    # average ranks within ties
    unique, inverse, counts = np.unique(scores, return_inverse=True, return_counts=True)
    if len(unique) < len(scores):
        sums = np.zeros(len(unique))
        np.add.at(sums, inverse, ranks)
        ranks = (sums / counts)[inverse]
    n_pos = float(positive.sum())
    n_neg = float((~positive).sum())
    return float((ranks[positive].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


# ── loading, for the consumer ──────────────────────────────────────────────


def load(path: Path | None = None) -> dict[str, Any] | None:
    """The frozen model, or None when it does not exist or did not pass.

    A model whose gate failed is never loaded — the same contract
    `models.dynamics` holds, and for the same reason: the caller has a working
    rule to fall back to, and a failed model is worse than no model because it
    looks like evidence.
    """
    target = path or ARTIFACT
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not payload.get("gate_passed"):
        return None
    return dict(payload)


def identity() -> dict[str, Any] | None:
    """What the surface may say about the model that classified a pair —
    never a hash on the page, always available for the audit line."""
    payload = load()
    if payload is None:
        return None
    return {
        "name": payload.get("name", "hostility"),
        "held_out_auc": payload.get("held_out_auc"),
        "baselines": payload.get("baselines"),
        "label": payload.get("label"),
        "caveat": payload.get("caveat"),
        "label_span": payload.get("trained_at_label_span"),
    }


def score(
    payload: dict[str, Any], cell: dict[str, float], *,
    declared_rivalry: bool, previous: dict[str, float] | None = None,
) -> float:
    """P(this pair was in a militarised dispute this year), from a frozen fit."""
    design = np.asarray(
        [design_row(cell, declared_rivalry=declared_rivalry, previous=previous)],
        dtype=float,
    )
    mean = np.asarray(payload["standardise"]["mean"], dtype=float)
    scale = np.asarray(payload["standardise"]["scale"], dtype=float)
    theta = np.asarray(payload["theta"], dtype=float)
    return float(predict_proba(theta, (design - mean) / scale)[0])
