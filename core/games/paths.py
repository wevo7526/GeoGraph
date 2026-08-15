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
#: Next-band branches below this share of a kernel row are not WALKED FORWARD
#: (the tree-size bound). It no longer drops mass from the fan: the marginal
#: accumulates every band's share, tail included, so a sub-floor rupture
#: transition is counted in the fan even though it is not branched — the floor
#: is about tree size, never about hiding the escalation tail.
BAND_BRANCH_FLOOR = 0.05
#: Live paths carried between periods. A cap, not a target — with band
#: branching the exact tree is (9 x 6)^H and a bound keeps the walk exact
#: over the mass that matters. Deterministic: pruned by probability with a
#: stable tie-break, so the same solve always keeps the same paths.
MAX_LIVE_PATHS = 2048


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


def _resolved_steps(
    steps: list[dict[str, Any]], band_mass: np.ndarray
) -> list[dict[str, Any]]:
    """A course's steps with each period's band read off the DISTRIBUTION the
    band branching put on it, rather than off whichever branch happened to
    create the group."""
    out = []
    for index, step in enumerate(steps):
        mass = band_mass[index]
        total = float(mass.sum())
        share = mass / total if total > 0 else mass
        band = int(np.argmax(share))
        above = np.nonzero(share > BAND_BRANCH_FLOOR)[0]
        out.append({
            **step,
            "intensity_band": band,
            "band_probability": round(float(share[band]), 4),
            "band_spread": [int(above[0]), int(above[-1])] if above.size else [band, band],
        })
    return out


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
    classify: Any = None,
) -> dict[str, Any]:
    """Every path through the horizon, ranked by probability.

    Beliefs move ALONG each path: a side that escalates at step one is judged
    more likely resolute at step two, which changes the mixture it is expected
    to play. Carrying the posterior forward is what makes this a sequence
    model rather than four independent one-period forecasts stapled together.

    THE KERNEL'S STOCHASTICITY IS KEPT. The walk used to take each kernel
    row's MODAL band — which discarded exactly the evidence the counted
    kernel exists to carry, froze every path onto the same few bands, and
    made the per-period marginal identical across periods (the fan was not a
    fan). Now each step branches over the next bands the row puts real mass
    on, and the marginal distribution is accumulated across ALL branches
    BEFORE the top-N cut, so the fan is the model's own distribution rather
    than a summary of eight survivors.
    """
    policy = equilibrium["policy"]
    horizon = equilibrium["horizon"]
    actions = state_module.ACTIONS
    bands = kernel.shape[-1]

    marginal_mass = np.zeros((horizon, bands))
    # (probability, intensity, belief_a, belief_b, steps)
    live: list[tuple[float, int, float, float, list[dict[str, Any]]]] = [
        (1.0, intensity, belief_a, belief_b, [])
    ]
    for period in range(horizon):
        nxt: list[tuple[float, int, float, float, list[dict[str, Any]]]] = []
        # NO ABSOLUTE FLOOR ON A BRANCH. The module's own normalisation
        # comment below records why: a path's raw joint probability through
        # several periods of mixtures is tiny by construction, and with the
        # band shares multiplied in it shrinks an order of magnitude faster —
        # an absolute cutoff emptied the whole tree by period four on the
        # real kernel while passing on a synthetic one. Pruning is RELATIVE:
        # the band floor bounds the branching factor, and the per-period cap
        # keeps the walk exact over the mass that matters.
        for probability, x, ba, bb, steps in live:
            mix_a = _expected_policy(policy, period, x, capability, bb)
            mix_b = _expected_policy(policy, period, x, capability, ba)
            for a, pa in enumerate(mix_a):
                for b, pb in enumerate(mix_b):
                    joint = probability * float(pa) * float(pb)
                    if joint <= 0.0:
                        continue
                    row = kernel[x, a, b]
                    posterior_a = posterior_of(ba, b, payoffs)
                    posterior_b = posterior_of(bb, a, payoffs)
                    spread = [
                        int(np.argmax(row > BAND_BRANCH_FLOOR)),
                        int(len(row) - 1 - np.argmax(row[::-1] > BAND_BRANCH_FLOOR)),
                    ]
                    for x_next in range(bands):
                        share = float(row[x_next])
                        if share <= 0.0:
                            continue
                        branch = joint * share
                        # THE FAN GETS THE FULL MASS, the tree does not. The
                        # marginal accumulates EVERY band's share — including the
                        # sub-floor tail — so escalation into the open top band
                        # (rupture is inherently a sub-5% transition, precisely
                        # the event this forecast exists to warn about) shows up
                        # in the fan instead of being silently pruned. The floor
                        # then bounds only the BRANCHING: a tail band is counted
                        # here but not walked forward, which keeps the tree exact
                        # over the mass that matters without hiding the tail.
                        marginal_mass[period, x_next] += branch
                        if share < BAND_BRANCH_FLOOR:
                            continue
                        nxt.append((
                            branch, x_next, posterior_a, posterior_b,
                            [*steps, {
                                "period": period + 1,
                                "action_a": actions[a],
                                "action_b": actions[b],
                                "quad": state_module.ACTION_QUAD[actions[max(a, b)]],
                                "intensity_band": x_next,
                                "band_probability": round(share, 4),
                                "band_spread": spread,
                                # The posteriors AFTER this step's actions —
                                # a function of the action course alone, so
                                # every band-variant of a course shares them
                                # and the aggregation below keeps them intact.
                                "belief_a": round(posterior_a, 4),
                                "belief_b": round(posterior_b, 4),
                            }],
                        ))
        # Deterministic cap: probability descending, then the path's own steps
        # as tie-break, so identical solves keep identical paths.
        nxt.sort(key=lambda item: (-item[0], str(item[4])))
        live = nxt[:MAX_LIVE_PATHS]

    # AGGREGATE BY ACTION COURSE. Band branching multiplies each action
    # sequence into up to bands^H band-variants, and ranking those raw
    # fragments made the "top path" hold half a percent of the mass — true
    # and unreadable. The course of play (who did what, each period) is the
    # sequence a reader acts on; the band at each step is a DISTRIBUTION over
    # that course, and the step carries its modal band with the share it
    # holds.
    groups: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
    for probability, _x, _ba, _bb, steps in live:
        key = tuple((s["action_a"], s["action_b"]) for s in steps)
        group = groups.setdefault(key, {
            "probability": 0.0,
            "band_mass": np.zeros((len(steps), bands)),
            "steps": steps,
        })
        group["probability"] += probability
        for index, step in enumerate(steps):
            group["band_mass"][index, step["intensity_band"]] += probability

    ordered = sorted(
        groups.values(), key=lambda g: (-g["probability"], str(g["steps"]))
    )
    total = sum(g["probability"] for g in ordered)
    # NORMALISE BEFORE THRESHOLDING. Pruning drops mass as the tree is walked,
    # and the raw joint probability of any single path through four periods of
    # three-way mixtures is tiny by construction — compared against an
    # absolute floor it would discard every path and report an empty forecast.
    # The threshold is about a course's share of the retained distribution.
    kept = [
        group for group in ordered[:top]
        if total and group["probability"] / total >= MIN_PATH_PROBABILITY
    ]
    # The fan, normalised per period over the mass the floors kept.
    marginal = []
    for period in range(horizon):
        mass = marginal_mass[period]
        mass_total = float(mass.sum())
        share = mass / mass_total if mass_total > 0 else mass
        marginal.append({
            "period": period + 1,
            "distribution": [round(float(v), 4) for v in share],
            "modal_band": int(np.argmax(share)) if mass_total > 0 else 0,
            "expected_band": round(float(share @ np.arange(bands)), 3),
        })
    paths_payload = []
    for group in kept:
        paths_payload.append({
            "probability": round(group["probability"] / total, 4) if total else 0.0,
            "steps": _resolved_steps(group["steps"], group["band_mass"]),
        })

    # KINDS OVER THE WHOLE WALK, not over the eight survivors. `top` is a
    # reading cut — with 1,645 enumerated courses the top eight hold 1.4% of
    # the mass, so a scenario built by pooling THEM answers "how much mass is
    # on the courses we chose to print". Pooling every enumerated course by
    # the name the classifier gives it answers the reader's actual question
    # ("how likely is a step-down at all"), and the shares then sum to one
    # across kinds instead of to the retained fraction. `classify` is injected
    # so this module keeps knowing nothing about how a course is NAMED
    # (core/games/scenarios.py owns that, and imports this one).
    kinds_payload: list[dict[str, Any]] = []
    if classify is not None and total:
        buckets: dict[str, dict[str, Any]] = {}
        for group in ordered:
            steps_resolved = _resolved_steps(group["steps"], group["band_mass"])
            bucket = buckets.setdefault(
                str(classify(steps_resolved)),
                {"probability": 0.0, "courses": 0, "lead": 0.0, "steps": steps_resolved},
            )
            bucket["probability"] += group["probability"]
            bucket["courses"] += 1
            if group["probability"] > bucket["lead"]:
                bucket["lead"] = group["probability"]
                bucket["steps"] = steps_resolved
        kinds_payload = [
            {
                "kind": kind,
                "probability": round(bucket["probability"] / total, 4),
                "lead_probability": round(bucket["lead"] / total, 4),
                "courses": bucket["courses"],
                "steps": bucket["steps"],
            }
            for kind, bucket in sorted(
                buckets.items(), key=lambda item: (-item[1]["probability"], item[0])
            )
        ]
    return {
        "paths": paths_payload,
        "kinds": kinds_payload,
        "paths_enumerated": len(groups),
        # What the top N leaves out, stated. A reader who sees eight paths
        # summing to 0.6 knows something different from one who sees 0.95.
        "retained_probability": (
            round(sum(g["probability"] for g in kept) / total, 4) if total else 0.0
        ),
        "marginal": marginal,
        "concept": equilibrium["concept"],
    }


def posterior_of(prior: float, action: int, payoffs: solve_module.Payoffs) -> float:
    """Belief update along a path — the same Bayes rule the solver uses."""
    return solve_module.posterior(prior, action, payoffs)


def marginal_intensity(paths: dict[str, Any], horizon: int) -> list[dict[str, Any]]:
    """Per-period distribution over intensity bands.

    Prefers the marginal `enumerate_paths` accumulated across EVERY branch —
    the model's own distribution — and only falls back to summarising the
    kept top paths for payloads frozen before the accumulator existed.
    """
    accumulated = paths.get("marginal")
    if accumulated:
        return list(accumulated)[:horizon]
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
