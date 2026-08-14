"""The ML → game bridge: the learned trajectory conditions the dyad's kernel.

THE LINK THE PIPELINE WAS MISSING. The intensity model (core/models) and the
game were two fully disjoint pipelines — the model's frozen trajectories fed
nothing, and the game's dynamics were the same pooled kernel for every dyad.
This module is the bridge, built on the platform's own provenance rule: the
game consumes the model's output FROM ITS FROZEN FORECAST (mode='model'),
so every number that tilts a kernel was already gated (walk-forward), frozen,
and traceable to an artifact hash before the game ever saw it.

The mechanism is an exponential tilt. The model predicts each dyad's
deviation from its own baseline over the coming quarters; the mean predicted
drift, scaled by the model's own held-out residual spread and capped, tilts
every kernel row toward (positive drift) or away from (negative drift) the
higher bands:

    P'(x' | x, a, b)  ∝  P(x' | x, a, b) · exp(η · (x' − x) / (bands − 1))

with η = TILT_SCALE · mean_h clip(deviation_h / residual_sd_h, ±1). The
counted kernel stays the evidence — a tilt bounded by ±TILT_SCALE cannot
overturn a well-measured row, only lean it — and η = 0 (no model, gate
failed, dyad uncovered) reproduces the untilted kernel exactly.

Every tilted solve carries an audit block naming the model artifact and the
η it applied. A solve without the block was not tilted. Section 17 holds:
the model's number conditions the DYNAMICS; it never lands in a measured
effect or a counted base rate.
"""

from __future__ import annotations

from typing import Any

import numpy as np

#: The cap on how hard a learned drift may lean the counted kernel. At 0.5
#: a maximal drift multiplies the far band's odds by e^0.5 ≈ 1.65 relative
#: to the origin band — a lean, never an overrule.
TILT_SCALE = 0.5


def eta_from_trajectory(path: list[dict[str, Any]]) -> float:
    """The tilt strength for one dyad, from its frozen model trajectory.

    Each step's predicted deviation is normalised by the model's own
    held-out residual spread at that horizon ((hi − lo) / 2), clipped to
    ±1 — a prediction inside its own noise band tilts weakly — then averaged
    across the horizon and scaled.
    """
    ratios = []
    for step in path:
        deviation = float(step.get("deviation", 0.0))
        lo, hi = step.get("lo"), step.get("hi")
        spread = (float(hi) - float(lo)) / 2.0 if lo is not None and hi is not None else 0.0
        if spread <= 0:
            continue
        ratios.append(float(np.clip(deviation / spread, -1.0, 1.0)))
    if not ratios:
        return 0.0
    return TILT_SCALE * float(np.mean(ratios))


def tilted_kernel(kernel: np.ndarray, eta: float) -> np.ndarray:
    """The kernel with every row leaned by η. η=0 returns the kernel as is."""
    if eta == 0.0:
        return kernel
    bands = kernel.shape[-1]
    origins = np.arange(bands, dtype=float)
    # weight[x, x'] = exp(eta * (x' - x) / (bands - 1))
    offsets = (origins[None, :] - origins[:, None]) / max(bands - 1, 1)
    weights = np.exp(eta * offsets)
    tilted = kernel * weights[:, None, None, :]
    totals = tilted.sum(axis=-1, keepdims=True)
    return np.asarray(tilted / np.where(totals > 0, totals, 1.0))


def audit(eta: float, model: dict[str, Any] | None) -> dict[str, Any] | None:
    """The provenance block a tilted solve must carry. None when untilted."""
    if eta == 0.0 or model is None:
        return None
    return {
        "eta": round(eta, 4),
        "scale": TILT_SCALE,
        "model": f"{model.get('name')}@{model.get('hash')}",
        "method": (
            "kernel rows tilted by exp(eta * band offset), eta = "
            f"{TILT_SCALE} * mean(clip(predicted deviation / residual spread, "
            "±1)) over the model's frozen trajectory for this dyad; the "
            "counted kernel remains the evidence, the tilt is bounded"
        ),
    }
