"""The transition model — the learned half of what actually runs the game.

WHY THIS EXISTS. `games/transition.kernel` counts P(next band | band, a₁, a₂)
over every dyad in a pack, so US–Japan and North Korea–South Korea are solved
over the SAME table. Measured on 2026-08-16: at band 2 the counted kernel
returns an expected next band of 0.60 for every pair on the board, identically,
by construction — and the ranked output put Russia–China, US–South Korea and
US–Philippines (all alliances) above US–China (a declared rivalry, last of
twelve). The one fact that separates those pairs — how coercive their record
actually is — was not an input to the game at all.

THE SHAPE, and why it cannot be worse than what it replaces:

    P(next) = softmax( log P_counted(next | band, a₁, a₂)  +  x · W )
                       └──── offset: the counted evidence ────┘  └ learned ┘

The counted kernel enters as an OFFSET, not as a baseline to beat. W = 0
reproduces today's behaviour exactly, so the model can only add what counting
does not know; it cannot throw the counts away. That matters at this sample
size — the reason `transition.py` counts rather than learns is that 324 cells
over ~28k dyad-quarters would memorise, and a full learned kernel still would.
A residual of a dozen coefficients on top of the counts does not.

An additive residual WITHOUT the offset was tried first and lost to the plain
counts: the counted table encodes a band×action×action interaction that a
linear model in those same variables cannot represent. Reading it as an offset
is what makes the residual's job small enough to learn.

WHAT IS IN x, AND WHAT IS NOT. Three measured facts about the pair's own recent
record — volume, coercive share and volatility over four quarters — plus the
two interactions the ablation kept. A fourth, mean level, was computed and
dropped: see FEATURES below. NOT in it: `ally`, `rival`, `bloc`,
`proxy`, the CINC ratio, betweenness, eigenvector, degree, Burt constraint,
community co-membership. Every one was measured against PRODUCTION's 37,930
NetworkMetric nodes (the dev graph holds none, which is why an earlier pass
answered this question with an empty table and did not count). They move
pooled log-loss by 0.004-0.025 and move within-dyad ordering by nothing, or
DOWN: china's within-dyad ρ falls from +0.1740 to +0.1591 when they are added.
That is the trap `core/models/features.py` already documents — a feature that
says which dyad this is scores pooled and hurts within it — and the declared
edges are the purest form of it, since they are constant for a pair.

The graph is not thereby useless: the wire's events, actors, dyads and
escalation coding ARE the graph, the regime gate and the standing chip are the
graph, and `opening.py` reads capability off it. But as predictive features for
what a pair does next quarter, the declared edges are dominated by the pair's
own observed behaviour, and saying so is cheaper than shipping features that
do nothing.

Held out on a time split (75/25), against the kernel that ships today:

    region    log-loss              within-dyad ρ
    mena      1.4982 → 1.3710       +0.1406 → +0.2056
    china     1.3982 → 1.2583       +0.1294 → +0.1740
    eurasia   1.3858 → 1.2556       +0.0720 → +0.1061

Training is OFFLINE (`scripts/train_dynamics.py` → a hashed JSON artifact in
`models/`, committed), reading the CORPUS and never a live store, so the
artifact is reproducible from the commit alone.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np

from core.games import state as state_module

#: The pair's own record over the trailing four quarters. Measured, not
#: chosen: see the module docstring for what was tried and dropped.
#:
#: `level` — the pair's mean intensity — was computed, ablated and DROPPED,
#: which is the same verdict `core/models/features.py` reached about the same
#: quantity for the intensity model, on the same reasoning. Held out: removing
#: it changes log-loss by −0.0020/−0.0004/−0.0002 (i.e. improves it slightly)
#: and moves within-dyad ordering UP in two regions (china +0.0085, mena
#: +0.0039). A pair's average level is mostly a label for which pair it is,
#: and this archive punishes those.
#:
#: `volume` is what carries the model: dropping it costs +0.0281/+0.0199/
#: +0.0201 log-loss and −0.0424/−0.0428/−0.0151 within-dyad ρ. `coercive`
#: earns its place through the interactions rather than on its own.
FEATURES = ("volume", "coercive", "volatility")

#: Two products the ablation kept. `coercive * band` lets a coercive record
#: mean something different high up the scale than low down; `volume *
#: coercive` separates a busy pair that is mostly talking from a quiet pair
#: whose few events are all coercive.
INTERACTIONS = ("coercive_x_band", "volume_x_coercive")

#: Quarters of history a feature window looks back over, inclusive of the
#: current one. Four is one year — long enough that a single quiet quarter
#: does not reclassify a pair, short enough to move within a crisis.
WINDOW_QUARTERS = 4

#: L2 on the residual weights. The residual's whole job is to be small.
L2 = 0.3

#: The band the model may move an offset row by, in log space, before the tilt
#: is clipped. A residual that can dominate the counted evidence is no longer
#: a residual — and an unclipped softmax on a rarely-observed cell will do
#: exactly that.
MAX_TILT = 2.5


def feature_names() -> tuple[str, ...]:
    """Design-matrix column order. Fixed here so the artifact and the forward
    pass cannot disagree about which weight is which."""
    bands = len(state_module.INTENSITY_EDGES)
    return (
        *(f"band_{k}" for k in range(bands)),
        *FEATURES,
        *INTERACTIONS,
        "const",
    )


def row_features(
    window: list[dict[str, Any]], band: int, scale: float
) -> dict[str, float]:
    """The four measured facts, from a dyad's trailing panel rows.

    `window` is that dyad's rows for the current quarter and the three before
    it, in time order; `scale` is the dyad's own intensity scale, so `level`
    and `volatility` are expressed on the pair's own ruler rather than pooled
    (the same reason `state.dyad_scale` exists).
    """
    events = sum(int(r["events"] or 0) for r in window)
    coercive = sum(int(r["conflict"] or 0) for r in window)
    intensity = [float(r["intensity"]) for r in window]
    share = (coercive / events) if events else 0.0
    return {
        "volume": math.log1p(events),
        "coercive": share,
        "level": float(np.mean(intensity)) / (scale or 1.0),
        "volatility": float(np.std(intensity)) / (scale or 1.0),
        "coercive_x_band": share * band,
        "volume_x_coercive": math.log1p(events) * share,
    }


def design(rows: list[dict[str, Any]]) -> np.ndarray:
    """(n, d) design matrix in `feature_names()` order."""
    bands = len(state_module.INTENSITY_EDGES)
    columns: list[np.ndarray] = []
    for k in range(bands):
        columns.append(np.array([1.0 if r["band"] == k else 0.0 for r in rows]))
    for name in (*FEATURES, *INTERACTIONS):
        columns.append(np.array([float(r[name]) for r in rows]))
    columns.append(np.ones(len(rows)))
    return np.column_stack(columns)


def offsets(kernel: np.ndarray, rows: list[dict[str, Any]]) -> np.ndarray:
    """log P_counted for each row's (band, a₁, a₂) cell."""
    bands = len(state_module.INTENSITY_EDGES)
    out = np.zeros((len(rows), bands))
    for i, row in enumerate(rows):
        cell = kernel[row["band"], row["a"], row["b"]]
        out[i] = np.log(np.clip(cell, 1e-9, 1.0))
    return out


@dataclass(frozen=True)
class Dynamics:
    """A fitted residual, plus the standardisation it was fitted under."""

    weights: np.ndarray            # (d, bands)
    mean: np.ndarray               # (d,)
    scale: np.ndarray              # (d,)
    names: tuple[str, ...]
    region: str
    artifact: str = ""             # name@hash, for the audit line

    def _standardise(self, x: np.ndarray) -> np.ndarray:
        return np.asarray((x - self.mean) / self.scale, dtype=float)

    def tilt(self, features: dict[str, float], band: int) -> np.ndarray:
        """The log-space adjustment this pair's record implies, per next band.

        CLIPPED. `MAX_TILT` bounds how far the residual may move a counted row,
        because the cells the counted kernel is least sure of are exactly the
        ones a softmax will happily send to a corner.
        """
        bands = len(state_module.INTENSITY_EDGES)
        row = {**features, "band": band}
        x = np.zeros(len(self.names))
        for i, name in enumerate(self.names):
            if name == "const":
                x[i] = 1.0
            elif name.startswith("band_"):
                x[i] = 1.0 if int(name.split("_")[1]) == band else 0.0
            else:
                x[i] = float(row.get(name, 0.0))
        adjustment = self._standardise(x[None, :]) @ self.weights
        return np.asarray(
            np.clip(adjustment.reshape(bands), -MAX_TILT, MAX_TILT), dtype=float
        )

    def kernel_for(
        self, counted: np.ndarray, features: dict[str, float]
    ) -> np.ndarray:
        """THIS PAIR's kernel — the counted table, tilted by its own record.

        Same shape as `transition.kernel`'s first return, so the solver, the
        path walk and the equilibrium code are untouched: they receive a
        kernel, as always, and this one knows which pair it is for.
        """
        bands = len(state_module.INTENSITY_EDGES)
        actions = len(state_module.ACTIONS)
        out = np.empty_like(counted)
        for band in range(bands):
            adjustment = self.tilt(features, band)
            for a in range(actions):
                for b in range(actions):
                    z = np.log(np.clip(counted[band, a, b], 1e-9, 1.0)) + adjustment
                    z -= z.max()
                    e = np.exp(z)
                    out[band, a, b] = e / e.sum()
        return out

    def payload(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "names": list(self.names),
            "weights": [[float(v) for v in row] for row in self.weights],
            "mean": [float(v) for v in self.mean],
            "scale": [float(v) for v in self.scale],
            "max_tilt": MAX_TILT,
            "window_quarters": WINDOW_QUARTERS,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, artifact: str = "") -> Dynamics:
        return cls(
            weights=np.asarray(payload["weights"], dtype=float),
            mean=np.asarray(payload["mean"], dtype=float),
            scale=np.asarray(payload["scale"], dtype=float),
            names=tuple(payload["names"]),
            region=str(payload["region"]),
            artifact=artifact,
        )


def fit(
    rows: list[dict[str, Any]], kernel: np.ndarray, *, l2: float = L2
) -> Dynamics:
    """Least-effort residual over the counted offset. L-BFGS, standardised x."""
    from scipy.optimize import minimize

    bands = len(state_module.INTENSITY_EDGES)
    x_raw = design(rows)
    mean = x_raw.mean(axis=0)
    scale = x_raw.std(axis=0)
    # A constant column standardises to nothing; leave it alone rather than
    # dividing by ~0 and handing the optimiser an infinity.
    scale = np.where(scale < 1e-9, 1.0, scale)
    mean = np.where(x_raw.std(axis=0) < 1e-9, 0.0, mean)
    x = (x_raw - mean) / scale
    offset = offsets(kernel, rows)
    y = np.array([r["next"] for r in rows], dtype=int)
    n, d = x.shape

    def objective(flat: np.ndarray) -> tuple[float, np.ndarray]:
        w = flat.reshape(d, bands)
        z = offset + x @ w
        z = z - z.max(axis=1, keepdims=True)
        e = np.exp(z)
        p = e / e.sum(axis=1, keepdims=True)
        loss = -float(np.mean(np.log(np.clip(p[np.arange(n), y], 1e-12, 1.0))))
        onehot = np.zeros_like(p)
        onehot[np.arange(n), y] = 1.0
        grad = x.T @ (p - onehot) / n + l2 * w / n
        return loss + 0.5 * l2 * float((w * w).sum()) / n, grad.ravel()

    result = minimize(
        objective, np.zeros(d * bands), jac=True, method="L-BFGS-B",
        options={"maxiter": 500},
    )
    return Dynamics(
        weights=result.x.reshape(d, bands), mean=mean, scale=scale,
        names=feature_names(), region="",
    )


def predict(model: Dynamics, rows: list[dict[str, Any]], kernel: np.ndarray) -> np.ndarray:
    x = model._standardise(design(rows))
    z = offsets(kernel, rows) + x @ model.weights
    z = np.clip(z - z.max(axis=1, keepdims=True), -60, 0)
    e = np.exp(z)
    return np.asarray(e / e.sum(axis=1, keepdims=True), dtype=float)


def log_loss(probabilities: np.ndarray, rows: list[dict[str, Any]]) -> float:
    y = np.array([r["next"] for r in rows], dtype=int)
    picked = probabilities[np.arange(len(rows)), y]
    return float(-np.mean(np.log(np.clip(picked, 1e-12, 1.0))))


def within_dyad_rho(
    probabilities: np.ndarray, rows: list[dict[str, Any]], *, min_rows: int = 12
) -> float:
    """Spearman of expected vs realised next band, WITHIN each dyad.

    THE GATE'S METRIC, for the reason docs/ml-spec.md gives: this archive's
    label variance is 70% within dyad and 30% between, so a pooled score
    mostly reports whether the model can tell the pairs apart. A model whose
    whole purpose is to give each pair its own dynamics has to be judged on
    whether it orders THAT pair's own quarters.
    """
    bands = len(state_module.INTENSITY_EDGES)
    expected = probabilities @ np.arange(bands)
    grouped: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        grouped[row["dyad"]].append(i)
    scores: list[float] = []
    weights: list[int] = []
    realised = np.array([r["next"] for r in rows], dtype=float)
    for index in grouped.values():
        if len(index) < min_rows:
            continue
        p, y = expected[index], realised[index]
        if p.std() < 1e-9 or y.std() < 1e-9:
            continue
        rank_p = np.argsort(np.argsort(p)).astype(float)
        rank_y = np.argsort(np.argsort(y)).astype(float)
        scores.append(float(np.corrcoef(rank_p, rank_y)[0, 1]))
        weights.append(len(index))
    if not scores:
        return float("nan")
    return float(np.average(scores, weights=weights))


#: How much within-dyad ordering the model may give up against the counted
#: kernel. Zero: the model exists to make the game dyad-specific, so a model
#: that orders a pair's own quarters no better than the pooled table has not
#: done its job, whatever it did to the pooled score.
ORDERING_TOLERANCE = 1.0


def passes_gate(folds: list[dict[str, Any]]) -> tuple[bool, str]:
    """Ship or don't. BOTH conditions, and the ordering one is the real bar.

    The pooled log-loss condition is easy — an offset model with any signal at
    all improves it, and the graph features improve it too while making the
    ordering worse. So the gate asks for both, and reports the summary either
    way: a model that fails is written into the training log, not into
    `models/`.
    """
    if not folds:
        return False, "no folds"
    model_ll = float(np.mean([f["log_loss"] for f in folds]))
    counted_ll = float(np.mean([f["log_loss_counted"] for f in folds]))
    scored = [f for f in folds if not math.isnan(f.get("rho", float("nan")))]
    if not scored:
        return False, "no fold produced a within-dyad score"
    model_rho = float(np.mean([f["rho"] for f in scored]))
    counted_rho = float(np.mean([f["rho_counted"] for f in scored]))
    summary = (
        f"log-loss {model_ll:.4f} vs counted {counted_ll:.4f}; "
        f"within-dyad rho {model_rho:+.4f} vs {counted_rho:+.4f}"
    )
    if model_ll >= counted_ll:
        return False, f"no log-loss improvement over the counted kernel — {summary}"
    if model_rho < counted_rho * ORDERING_TOLERANCE:
        return False, f"within-dyad ordering is not improved — {summary}"
    return True, summary
