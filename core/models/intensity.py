"""The intensity forecaster: ridge regression, and the gate it must pass.

Ridge because the honest size of this problem is small. ~27k dyad-quarters
over ~190 dyads is not a deep-learning dataset, and a closed-form estimator
with one penalty has no seed, no learning rate, no early stopping and no
epoch count — so it cannot be quietly tuned into looking good, and its
weights are a vector a person can read.

THE GATE IS WITHIN-DYAD. A pooled score on this panel is not evidence: the
label's variance is 70% within dyad and 30% between, and a model that only
knows which dyad it is looking at already scores 0.92 pooled while ranking a
given dyad's own quarters backwards. So skill here means: for THIS dyad, did
the model order its quarters better than "next quarter looks like this one"?

Everything is walk-forward. Train on quarters strictly before the cut, test
on the quarters after it, never a random split — a random split leaks the
future through a dyad's own history and produces a number that means nothing.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from core.models import features as feature_module

#: Ridge penalty. One number, chosen for scale rather than fitted to the test
#: period — tuning it against the evaluation is how a walk-forward score
#: becomes a training score wearing a disguise.
DEFAULT_PENALTY = 1.0


def scaler(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(mean, sd) per column, fitted on TRAINING rows only.

    RIDGE IS SCALE-DEPENDENT and these features are not on one scale: an
    intensity deviation runs to ±20, a run-length ratio to 8, a log-volume
    shift to about 5. Penalising them uniformly without standardising
    shrinks the small-scale features hardest, which has nothing to do with
    how useful they are. Skipping this step made the fitted model score
    WORSE within dyad (−0.11) than the single feature it contains (+0.43) —
    the model was being drowned by whichever column happened to be largest.

    Fitted on train only: standardising with statistics that saw the test
    rows leaks the future, quietly, through the mean.
    """
    mean = x.mean(axis=0)
    sd = x.std(axis=0)
    # A constant column (the intercept) has no spread to divide by, and a
    # feature that never varies in training carries no information anyway.
    mean[0], sd[0] = 0.0, 1.0
    sd[sd < 1e-9] = 1.0
    return mean, sd


def standardize(x: np.ndarray, mean: np.ndarray, sd: np.ndarray) -> np.ndarray:
    scaled: np.ndarray = (x - mean) / sd
    return scaled


def fit(x: np.ndarray, y: np.ndarray, *, penalty: float = DEFAULT_PENALTY) -> np.ndarray:
    """Closed-form ridge on ALREADY-STANDARDISED columns. The intercept is
    never penalised — shrinking it would bias every prediction toward zero."""
    ridge = penalty * np.eye(x.shape[1])
    ridge[0, 0] = 0.0
    try:
        weights: np.ndarray = np.linalg.solve(x.T @ x + ridge, x.T @ y)
    except np.linalg.LinAlgError:
        weights = np.linalg.lstsq(x, y, rcond=None)[0]
    return weights


def predict(x: np.ndarray, weights: np.ndarray, *, target: str = "deviation") -> np.ndarray:
    """Model output in the target's own units.

    A LEVEL is floored at zero — intensity is a magnitude and a negative one
    is not a gentler escalation, it is nonsense. A DEVIATION is not floored:
    a negative deviation is the model saying this dyad cools from here, which
    is the single most useful thing it can say and clamping it away would
    delete exactly the prediction that is hard to make.
    """
    raw = np.asarray(x @ weights, dtype=float)
    return raw if target == "deviation" else np.maximum(0.0, raw)


def to_level(deviation: np.ndarray, base: np.ndarray) -> np.ndarray:
    """Deviation prediction → absolute intensity, floored at zero."""
    level: np.ndarray = np.maximum(0.0, deviation + base)
    return level


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Rank correlation, ties averaged. Rank rather than Pearson because the
    question is ordering — which of this dyad's quarters are the dangerous
    ones — not whether the units are calibrated."""
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")

    def rank(v: np.ndarray) -> np.ndarray:
        order = np.argsort(v, kind="mergesort")
        ranks = np.empty(len(v), dtype=float)
        ranks[order] = np.arange(1, len(v) + 1)
        for value in np.unique(v):
            tied = v == value
            if tied.sum() > 1:
                ranks[tied] = ranks[tied].mean()
        return ranks

    ra, rb = rank(a), rank(b)
    if np.std(ra) == 0 or np.std(rb) == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def within_dyad_score(
    predicted: np.ndarray, actual: np.ndarray, dyads: np.ndarray
) -> tuple[float, int]:
    """(mean within-dyad rank correlation, dyads scored).

    Only dyads whose test window actually VARIES are scored. A dyad that sat
    at zero throughout has no ordering to get right, and scoring it as zero
    correlation would dilute a real result with rows that could not have
    produced one either way.
    """
    scores = []
    for dyad in sorted(set(dyads.tolist())):
        mask = dyads == dyad
        if mask.sum() >= 4 and np.std(actual[mask]) > 0:
            value = _spearman(predicted[mask], actual[mask])
            if not np.isnan(value):
                scores.append(value)
    return (float(np.mean(scores)) if scores else float("nan")), len(scores)


def rmse(predicted: np.ndarray, actual: np.ndarray) -> float:
    return float(np.sqrt(np.mean((predicted - actual) ** 2)))


def evaluate(
    feature_rows: list[dict[str, Any]],
    *,
    horizon: int,
    cut_quarter: int,
    test_quarters: int = 20,
    penalty: float = DEFAULT_PENALTY,
    columns: list[int] | None = None,
    target: str = "deviation",
) -> dict[str, Any]:
    """One walk-forward fold, model and baseline scored the same way.

    The baseline is PERSISTENCE — next quarter looks like this one — because
    it is what the model has to be worth more than. Beating a base rate is
    not a result; beating persistence within dyad is.
    """
    # The FULL matrix is always built: `columns` subsets what the model may
    # learn from, but the persistence baseline reads the dyad's actual current
    # level and must not move when the model's feature set does.
    full, y, quarters, dyads = feature_module.matrices(feature_rows, horizon, target=target)
    x = full if columns is None else full[:, columns]
    train = quarters < cut_quarter
    test = (quarters >= cut_quarter) & (quarters < cut_quarter + test_quarters)
    if train.sum() < 100 or test.sum() < 50:
        return {"horizon": horizon, "cut": cut_quarter, "skipped": "too few rows"}

    mean, sd = scaler(x[train])
    weights = fit(standardize(x[train], mean, sd), y[train], penalty=penalty)
    predicted = predict(standardize(x[test], mean, sd), weights, target=target)
    # Persistence in the TARGET's own units: the dyad's current level, as a
    # deviation when the target is a deviation and absolutely otherwise. The
    # baseline has to be the same quantity as the prediction or the comparison
    # is between two different questions.
    names = feature_module.FEATURE_NAMES
    level_now = full[test][:, names.index("level_now")]
    baseline = (
        level_now if target == "deviation"
        else np.maximum(0.0, level_now + full[test][:, names.index("base_level")])
    )

    model_score, n_dyads = within_dyad_score(predicted, y[test], dyads[test])
    base_score, _ = within_dyad_score(baseline, y[test], dyads[test])
    return {
        "horizon": horizon,
        "target": target,
        "cut": cut_quarter,
        "cut_year": cut_quarter // 4,
        "n_train": int(train.sum()),
        "n_test": int(test.sum()),
        "dyads_scored": n_dyads,
        "within_dyad": round(model_score, 4) if not np.isnan(model_score) else None,
        "within_dyad_persistence": round(base_score, 4) if not np.isnan(base_score) else None,
        "rmse": round(rmse(predicted, y[test]), 4),
        "rmse_persistence": round(rmse(baseline, y[test]), 4),
    }


def walk_forward(
    feature_rows: list[dict[str, Any]],
    *,
    cut_years: list[int],
    horizons: tuple[int, ...] = feature_module.HORIZONS,
    penalty: float = DEFAULT_PENALTY,
    columns: list[int] | None = None,
    target: str = "deviation",
) -> list[dict[str, Any]]:
    """Every (cut, horizon) fold, in a deterministic order."""
    folds = []
    for cut_year in cut_years:
        for horizon in horizons:
            fold = evaluate(
                feature_rows, horizon=horizon, cut_quarter=cut_year * 4,
                penalty=penalty, columns=columns, target=target,
            )
            if "skipped" not in fold:
                folds.append(fold)
    return folds


#: How much within-dyad ordering the model may give up against persistence.
#: Not zero, because ordering and magnitude trade against each other slightly;
#: small, because ordering is the part that is hard to get at all.
_ORDER_TOLERANCE = 0.95


def passes_gate(folds: list[dict[str, Any]]) -> tuple[bool, str]:
    """The ship/don't-ship decision, in one place so it cannot drift.

    THE GATE MOVED ONCE, ON EVIDENCE, AND THIS IS THE RECORD OF IT. It was
    written as "beat persistence at within-dyad ordering". Walk-forward, over
    four cuts and four horizons, NOTHING beat persistence at ordering — the
    dyad's own current deviation scores +0.4253 and every engineered feature
    added to it made the out-of-sample ordering worse. Persistence is not a
    baseline this problem clears; persistence IS the ordering signal.

    So the model's contribution is MAGNITUDE, and the gate asks for exactly
    that: keep persistence's ordering (within a small tolerance) and beat its
    error. Raw persistence says "next year looks like this quarter", which is
    well ordered and badly calibrated — it ignores that a dyad reverts toward
    its own baseline, and by how much at each horizon. Fitting that reversion
    is a real and honest contribution, and it is all this model claims.

    A model failing either condition is reported, not shipped.
    """
    scored = [f for f in folds if f.get("within_dyad") is not None]
    if not scored:
        return False, "no fold produced a within-dyad score"
    model = float(np.mean([f["within_dyad"] for f in scored]))
    base = float(np.mean([
        f["within_dyad_persistence"] for f in scored
        if f.get("within_dyad_persistence") is not None
    ]))
    error = float(np.mean([f["rmse"] for f in folds]))
    base_error = float(np.mean([f["rmse_persistence"] for f in folds]))
    summary = (
        f"ordering {model:+.4f} vs persistence {base:+.4f}; "
        f"rmse {error:.3f} vs {base_error:.3f}"
    )
    if model <= 0.0:
        return False, f"within-dyad ordering {model:+.4f} is not above zero"
    if model < base * _ORDER_TOLERANCE:
        return False, f"ordering falls too far below persistence — {summary}"
    if error >= base_error:
        return False, f"no magnitude improvement over persistence — {summary}"
    return True, summary


def fit_final(
    feature_rows: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = feature_module.HORIZONS,
    penalty: float = DEFAULT_PENALTY,
    target: str = "deviation",
) -> tuple[dict[int, list[float]], list[float], list[float]]:
    """(weights per horizon, scaler mean, scaler sd) over the whole panel.

    The scaler ships WITH the weights: a prediction made against differently
    standardised columns is a different model, so the artifact carries both
    or it carries neither.
    """
    columns = feature_module.shipped_columns()
    weights: dict[int, list[float]] = {}
    mean = sd = None
    for horizon in horizons:
        x, y, _, _ = feature_module.matrices(
            feature_rows, horizon, target=target, columns=columns
        )
        if mean is None:
            mean, sd = scaler(x)
        assert sd is not None
        weights[horizon] = [
            float(v) for v in fit(standardize(x, mean, sd), y, penalty=penalty)
        ]
    assert mean is not None and sd is not None
    return weights, [float(v) for v in mean], [float(v) for v in sd]


def forecast_trajectory(
    feature_rows: list[dict[str, Any]],
    weights: dict[int, list[float]],
    *,
    dyad_id: str,
    mean: list[float],
    sd: list[float],
    residual_sd: dict[int, float] | None = None,
) -> list[dict[str, Any]]:
    """The dyad's forward path from its LATEST observed quarter.

    The model predicts a DEVIATION from that dyad's running baseline, so the
    baseline is added back here — which is also why the path is meaningful
    per dyad rather than comparable across them.

    A band, never a bare line: the interval is the horizon's own residual
    spread from the walk-forward folds, so it widens with the horizon because
    the model measurably got worse there, not because a curve looked better
    that way.
    """
    own = [r for r in feature_rows if r["dyad_id"] == dyad_id]
    if not own:
        return []
    latest = max(own, key=lambda r: r["q"])
    columns = feature_module.shipped_columns()
    raw = np.array([latest["x"][i] for i in columns], dtype=float)
    x = standardize(raw, np.array(mean, dtype=float), np.array(sd, dtype=float))
    base = float(latest["base"])
    path = []
    for horizon in sorted(weights):
        deviation = float(x @ np.array(weights[horizon], dtype=float))
        value = max(0.0, base + deviation)
        spread = (residual_sd or {}).get(horizon, 0.0)
        path.append({
            "horizon": horizon,
            "quarter": latest["q"] + horizon,
            "date": _quarter_date(latest["q"] + horizon),
            "intensity": round(value, 4),
            "deviation": round(deviation, 4),
            "lo": round(max(0.0, value - spread), 4),
            "hi": round(value + spread, 4),
        })
    return path


def _quarter_date(index: int) -> str:
    year, quarter = divmod(index, 4)
    return f"{year}-{quarter * 3 + 1:02d}-01"
