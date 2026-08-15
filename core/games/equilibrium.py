"""The correlated-equilibrium stage solution, and how far it stands from Nash.

`solve.py` solves each stage game by logit best response — a quantal-response
equilibrium, the concept the payoffs were FITTED under and the one the
indirect inference needs (unique, continuous). This module is the exact
counterpart, and the two are served side by side rather than one replacing
the other:

  * The set of CORRELATED equilibria of a finite game (Aumann 1974) is a
    polytope: a distribution over joint actions such that no side, told its
    recommended action, gains by deviating. Every Nash equilibrium is a
    correlated equilibrium, so the polytope is never empty.

  * A correlated equilibrium that FACTORS as the product of its marginals is
    a Nash equilibrium of the stage game; carried through the type-indexed
    backward induction with Bayes-updated beliefs, that is a Bayesian Nash
    solution of the finite-horizon game. `nash_gap` is the total-variation
    distance between the joint and the product of its own marginals: 0 means
    the stage sat ON a Nash point; anything above it says how much
    coordination the play needs that the archive does not model. That number
    is what "toward the BNE" means on the surface, stated rather than assumed.

WHY THE SELECTION IS ENTROPY-REGULARISED, NOT A BARE LP (2026-08-15, the
second fix of the day). Maximising welfare alone over the polytope is a linear
program, so its optimum is a VERTEX — in practice a single joint action
carrying all the mass. That is not a near-certainty the archive taught us; it
is what linear programming does to a polytope. Downstream it was fatal: with a
pure stage profile every period's mixture is degenerate, the path walk has one
action course to enumerate, and the region map reported "its most likely course
is mutual escalation at 100%" for pair after pair — false precision produced by
the selection rule, sitting on top of a kernel that is genuinely spread.

So the selection maximises welfare PLUS the joint's entropy at the same logit
temperature the quantal response uses (λ = 1/precision, passed in by
`solve.py`, fixed rather than fitted for the same reason). The feasible set is
unchanged — every constraint of the CE polytope still binds, so the answer is
still a correlated equilibrium — but the objective is now strictly concave, so
the solution is unique, interior where the constraints allow, and continuous
in the payoffs. Read plainly: among the correlated equilibria, the one that
does best on welfare while claiming no more certainty than the same
"approximately optimal" assumption the rest of the layer is built on.

It is solved in the DUAL, which is what makes it cheap enough for the
thousands of stage games a region map walks: with multipliers μ ≥ 0 on the
incentive constraints, the primal maximiser is a softmax,

    p(μ) = softmax((welfare − Gᵀμ) / λ),

and the dual λ·logsumexp((welfare − Gᵀμ)/λ) is smooth and convex in μ with
gradient −G·p(μ). L-BFGS-B over the multipliers converges in tens of
iterations and a couple of milliseconds. `ce_violation` reports the largest
incentive constraint the returned joint leaves violated; when the dual has not
cleared it, the exact welfare-maximal vertex from HiGHS is returned instead
and `status` says so, because an approximate correlated equilibrium served as
an exact one is the kind of quiet lie this repo does not keep.

The policy the recursion carries is each side's MARGINAL of the joint, so the
downstream path walk (`paths.py`) and the ML tilt (`bridge.py`) are shared by
both concepts unchanged. Nothing here reads a store: matrices in, mixtures out.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: A joint-action cell below this mass is treated as zero when the product
#: form is compared — the solvers return ~1e-12 noise on inactive cells.
_MASS_FLOOR = 1e-9

#: How far a returned joint may violate an incentive constraint and still be
#: called a correlated equilibrium, RELATIVE to the payoff scale of the stage
#: (a constraint is a payoff difference, so an absolute tolerance would mean
#: different things at stake 0.1 and stake 3.0).
_CE_TOLERANCE = 1e-6

#: Dual multipliers are bounded: a constraint that can only be met in the
#: limit would otherwise send L-BFGS-B off to infinity chasing a vertex the
#: exact LP can name in one call.
_MU_CEILING = 1e7
_DUAL_ITERATIONS = 400


@dataclass(frozen=True)
class StageSolution:
    """One stage game's correlated-equilibrium solution."""

    joint: np.ndarray        # (actions, actions) — P(a, b) over joint actions
    mix_a: np.ndarray        # A's marginal
    mix_b: np.ndarray        # B's marginal
    value_a: float           # A's expected payoff under the joint
    value_b: float
    nash_gap: float          # total variation between joint and mix_a ⊗ mix_b
    entropy: float           # of the joint, in nats — 0 is a pure profile
    ce_violation: float      # largest incentive constraint left unmet
    status: str              # "optimal" | "lp-vertex" | the solver's message


def incentive_constraints(payoff_a: np.ndarray, payoff_b: np.ndarray) -> np.ndarray:
    """The CE polytope's incentive rows, as `G` with `G p ≤ 0` on the flattened
    joint. A side told "play i" cannot do better with i':

        for A, all i ≠ i':   Σ_j p[i, j] · (A[i', j] − A[i, j]) ≤ 0
        for B, all j ≠ j':   Σ_i p[i, j] · (B[i, j'] − B[i, j]) ≤ 0
    """
    actions = int(payoff_a.shape[0])
    n = actions * actions
    rows: list[np.ndarray] = []
    for i in range(actions):
        for i_alt in range(actions):
            if i_alt == i:
                continue
            row = np.zeros((actions, actions))
            row[i, :] = payoff_a[i_alt, :] - payoff_a[i, :]
            rows.append(row.reshape(n))
    for j in range(actions):
        for j_alt in range(actions):
            if j_alt == j:
                continue
            row = np.zeros((actions, actions))
            row[:, j] = payoff_b[:, j_alt] - payoff_b[:, j]
            rows.append(row.reshape(n))
    return np.vstack(rows)


def _lp_vertex(
    welfare: np.ndarray, constraints: np.ndarray, shape: tuple[int, int]
) -> tuple[np.ndarray, str]:
    """The exact welfare-maximal vertex, by HiGHS — the fallback, and the
    reference the regularised solution is measured against."""
    from scipy.optimize import linprog

    n = constraints.shape[1]
    result = linprog(
        -welfare,
        A_ub=constraints, b_ub=np.zeros(constraints.shape[0]),
        A_eq=np.ones((1, n)), b_eq=np.array([1.0]),
        bounds=[(0.0, 1.0)] * n, method="highs",
    )
    if result.status == 0 and result.x is not None:
        joint = np.clip(np.asarray(result.x, dtype=float), 0.0, None)
        return joint.reshape(shape), "lp-vertex"
    joint = np.zeros(n)
    joint[int(np.argmax(welfare))] = 1.0
    return joint.reshape(shape), str(result.message)


def solve_stage_lp(
    payoff_a: np.ndarray, payoff_b: np.ndarray, *, temperature: float = 0.25
) -> StageSolution:
    """The entropy-regularised welfare-maximal correlated equilibrium.

    `payoff_a[i, j]` is A's payoff when A plays i and B plays j; `payoff_b`
    is indexed the same way (B's payoff at the same joint action). Maximises
    Σ p·(A + B) + `temperature`·H(p) over the CE polytope — see the module
    docstring for why the entropy term is there and why the temperature is the
    quantal response's own.
    """
    from scipy.optimize import minimize

    shape = (int(payoff_a.shape[0]), int(payoff_a.shape[1]))
    constraints = incentive_constraints(payoff_a, payoff_b)
    welfare = (payoff_a + payoff_b).reshape(-1)
    scale = max(float(np.abs(welfare).max()), 1.0)
    # THE TEMPERATURE IS READ ON THE STAGE'S OWN WELFARE SPREAD, not in raw
    # payoff units. A stage late in the recursion carries four discounted
    # continuation values inside every cell, so its welfare spans several
    # units; a flat λ = 1/precision against that is no regularisation at all
    # and the solution collapses back onto the vertex (measured: entropy 0.01
    # nats over 144 stage games, every mixture pure). Normalised, the
    # parameter means something a reader can check: the WORST joint action is
    # about e^(-precision) as likely as the best, which is the same
    # decisiveness the logit response carries at that precision.
    spread = float(welfare.max() - welfare.min())
    lam = max(float(temperature) * max(spread, 1e-9), 1e-9)

    def dual(mu: np.ndarray) -> tuple[float, np.ndarray]:
        tilted = (welfare - constraints.T @ mu) / lam
        top = float(tilted.max())
        weights = np.exp(tilted - top)
        total = float(weights.sum())
        p = weights / total
        return lam * (np.log(total) + top), -(constraints @ p)

    result = minimize(
        dual, np.zeros(constraints.shape[0]), jac=True, method="L-BFGS-B",
        bounds=[(0.0, _MU_CEILING)] * constraints.shape[0],
        options={"maxiter": _DUAL_ITERATIONS, "ftol": 1e-14, "gtol": 1e-12},
    )
    tilted = (welfare - constraints.T @ np.asarray(result.x, dtype=float)) / lam
    weights = np.exp(tilted - float(tilted.max()))
    joint = (weights / weights.sum()).reshape(shape)
    violation = float(np.max(constraints @ joint.reshape(-1)))
    status = "optimal"
    if not np.isfinite(violation) or violation > _CE_TOLERANCE * scale:
        # The regularised point does not clear the polytope: take the exact
        # vertex rather than serve an approximate equilibrium as an exact one.
        joint, status = _lp_vertex(welfare, constraints, shape)
        violation = float(np.max(constraints @ joint.reshape(-1)))

    joint = np.where(joint < _MASS_FLOOR, 0.0, joint)
    total = float(joint.sum())
    joint = joint / total if total > 0 else np.full(shape, 1.0 / (shape[0] * shape[1]))

    mix_a = joint.sum(axis=1)
    mix_b = joint.sum(axis=0)
    product = np.outer(mix_a, mix_b)
    positive = joint[joint > 0]
    return StageSolution(
        joint=joint,
        mix_a=mix_a,
        mix_b=mix_b,
        value_a=float((joint * payoff_a).sum()),
        value_b=float((joint * payoff_b).sum()),
        nash_gap=0.5 * float(np.abs(joint - product).sum()),
        entropy=float(-(positive * np.log(positive)).sum()),
        ce_violation=violation,
        status=status,
    )


def concept_line(horizon: int, temperature: float) -> str:
    return (
        f"correlated-equilibrium MPBE (welfare-maximal over the CE polytope, "
        f"entropy-regularised at the quantal response's own temperature "
        f"λ={temperature:.3g}; the unregularised LP optimum is a vertex — one "
        f"joint action at certainty — which is a property of linear "
        f"programming, not evidence), finite horizon H={horizon}, backward "
        "induction; nash_gap = total variation of the joint from the product "
        "of its marginals (0 = a Nash point)"
    )
