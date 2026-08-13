"""A solved policy → a DISTRIBUTION over event sequences.

The deliverable this whole package exists for: a sequenced event in the
forecast. Two rules shape it and neither is negotiable.

NEVER ONE PATH. A single sequence presented as "the forecast" would be the
most misleading object this repo could produce — it reads as a claim about
what will happen when it is one draw from a wide distribution. Paths are
enumerated with their probabilities and reported together.

ENUMERATED, NOT SAMPLED. With three actions over four periods the whole tree
is 81 joint-action paths per type pair, which is small enough to walk
exactly. Exact enumeration means the probabilities are the model's own and
not a Monte Carlo artifact, and it makes the output reproducible without a
seed — which matters because these get frozen into the graph and scored
later.

Prices are NOT attached here. This module emits events; the market half is a
lookup over measured AFFECTED edges downstream, so nothing on any surface
carries a price that originated in a model (build-spec section 17).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from core.games import solve as solve_module
from core.games import state as state_module

#: Paths kept, by probability. The tail is long and flat; reporting all 81
#: would bury the shape in noise, and reporting one would be a lie. The
#: retained mass is always stated alongside.
TOP_PATHS = 8
#: Paths below this are not worth a reader's attention even inside the top N.
MIN_PATH_PROBABILITY = 0.005


def _expected_policy(
    policy: np.ndarray, period: int, x: int, k: int, belief: float
) -> np.ndarray:
    """The action mixture an OBSERVER expects, marginalising over type.

    The forecast does not know which type a side is — that is the whole point
    of the private information — so what it can predict is the mixture
    weighted by the current belief. `belief` is P(resolute).
    """
    mixture: np.ndarray = (
        (1.0 - belief) * policy[period, x, k, 0] + belief * policy[period, x, k, 1]
    )
    return mixture


def enumerate_paths(
    equilibrium: dict[str, Any],
    kernel: np.ndarray,
    *,
    intensity: int,
    capability: int,
    belief_a: float,
    belief_b: float,
    payoffs: solve_module.Payoffs,
    top: int = TOP_PATHS,
) -> dict[str, Any]:
    """Every path through the horizon, ranked by probability.

    Beliefs move ALONG each path: a side that escalates at step one is judged
    more likely resolute at step two, which changes the mixture it is expected
    to play. Carrying the posterior forward is what makes this a sequence
    model rather than four independent one-period forecasts stapled together.
    """
    policy = equilibrium["policy"]
    horizon = equilibrium["horizon"]
    actions = state_module.ACTIONS

    # (probability, intensity, belief_a, belief_b, steps)
    live: list[tuple[float, int, float, float, list[dict[str, Any]]]] = [
        (1.0, intensity, belief_a, belief_b, [])
    ]
    for period in range(horizon):
        nxt: list[tuple[float, int, float, float, list[dict[str, Any]]]] = []
        for probability, x, ba, bb, steps in live:
            mix_a = _expected_policy(policy, period, x, capability, bb)
            mix_b = _expected_policy(policy, period, x, capability, ba)
            for a, pa in enumerate(mix_a):
                for b, pb in enumerate(mix_b):
                    joint = probability * float(pa) * float(pb)
                    if joint < MIN_PATH_PROBABILITY / 10:
                        continue
                    # The next intensity is itself a distribution; the path
                    # takes its modal band and carries the spread as the band.
                    row = kernel[x, a, b]
                    x_next = int(np.argmax(row))
                    nxt.append((
                        joint, x_next,
                        posterior_of(ba, b, payoffs),
                        posterior_of(bb, a, payoffs),
                        [*steps, {
                            "period": period + 1,
                            "action_a": actions[a],
                            "action_b": actions[b],
                            "quad": state_module.ACTION_QUAD[actions[max(a, b)]],
                            "intensity_band": x_next,
                            "band_spread": [
                                int(np.argmax(row > 0.05)),
                                int(len(row) - 1 - np.argmax(row[::-1] > 0.05)),
                            ],
                        }],
                    ))
        live = nxt

    live.sort(key=lambda item: -item[0])
    total = sum(item[0] for item in live)
    # NORMALISE BEFORE THRESHOLDING. Pruning drops mass as the tree is walked,
    # and the raw joint probability of any single path through four periods of
    # three-way mixtures is order 1e-4 — compared against an absolute floor it
    # would discard every path and report an empty forecast. The threshold is
    # about a path's share of the retained distribution, not its raw weight.
    kept = [
        item for item in live[:top]
        if total and item[0] / total >= MIN_PATH_PROBABILITY
    ]
    return {
        "paths": [
            {"probability": round(p / total, 4) if total else 0.0, "steps": steps}
            for p, _x, _ba, _bb, steps in kept
        ],
        "paths_enumerated": len(live),
        # What the top N leaves out, stated. A reader who sees eight paths
        # summing to 0.6 knows something different from one who sees 0.95.
        "retained_probability": round(sum(p for p, *_ in kept) / total, 4) if total else 0.0,
        "concept": equilibrium["concept"],
    }


def posterior_of(prior: float, action: int, payoffs: solve_module.Payoffs) -> float:
    """Belief update along a path — the same Bayes rule the solver uses."""
    return solve_module.posterior(prior, action, payoffs)


def marginal_intensity(paths: dict[str, Any], horizon: int) -> list[dict[str, Any]]:
    """Per-period distribution over intensity bands, across the kept paths.

    The fan a chart draws: at each step, where the probability mass sits. A
    reader gets the shape of the forecast without reading eight sequences.
    """
    bands = len(state_module.INTENSITY_EDGES)
    out = []
    for period in range(1, horizon + 1):
        mass = np.zeros(bands)
        for path in paths["paths"]:
            step = next((s for s in path["steps"] if s["period"] == period), None)
            if step is not None:
                mass[step["intensity_band"]] += path["probability"]
        total = mass.sum()
        if total > 0:
            mass = mass / total
        out.append({
            "period": period,
            "distribution": [round(float(v), 4) for v in mass],
            "modal_band": int(np.argmax(mass)),
            "expected_band": round(float(mass @ np.arange(bands)), 3),
        })
    return out
