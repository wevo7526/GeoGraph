"""The LP stage solution: a correlated equilibrium by linear programming, and
how far it stands from a Nash (product-form) equilibrium.

`solve.py` solves each stage game by logit best response — a quantal-response
equilibrium, the concept the payoffs were FITTED under and the one the
indirect inference needs (unique, continuous). This module is the exact
counterpart, and the two are served side by side rather than one replacing
the other:

  * The set of CORRELATED equilibria of a finite game (Aumann 1974) is a
    polytope: a distribution over joint actions such that no side, told its
    recommended action, gains by deviating. Choosing the welfare-maximal one
    is a LINEAR PROGRAM in the nine joint-action probabilities, solved here
    with HiGHS through `scipy.optimize.linprog`. Every Nash equilibrium is a
    correlated equilibrium, so the polytope is never empty and the LP always
    has a solution — there is no iteration to fail to converge.

  * A correlated equilibrium that FACTORS as the product of its marginals is
    a Nash equilibrium of the stage game; carried through the type-indexed
    backward induction with Bayes-updated beliefs, that is a Bayesian Nash
    solution of the finite-horizon game. `nash_gap` is the total-variation
    distance between the LP's joint distribution and the product of its own
    marginals: 0 means the LP landed ON a Nash point; anything above it says
    how much coordination the welfare-maximal play needs that the archive
    does not model. That number is what "toward the BNE" means on the surface,
    stated rather than assumed.

The policy the recursion carries is each side's MARGINAL of the joint, so the
downstream path walk (`paths.py`) and the ML tilt (`bridge.py`) are shared by
both concepts unchanged. Nothing here reads a store: matrices in, mixtures out.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: A joint-action cell below this mass is treated as zero when the product
#: form is compared — HiGHS returns 1e-12 noise on inactive vertices.
_MASS_FLOOR = 1e-9


@dataclass(frozen=True)
class StageSolution:
    """One stage game's LP solution."""

    joint: np.ndarray        # (actions, actions) — P(a, b) over joint actions
    mix_a: np.ndarray        # A's marginal
    mix_b: np.ndarray        # B's marginal
    value_a: float           # A's expected payoff under the joint
    value_b: float
    nash_gap: float          # total variation between joint and mix_a ⊗ mix_b
    status: str              # "optimal" | the solver's message when it was not


def solve_stage_lp(payoff_a: np.ndarray, payoff_b: np.ndarray) -> StageSolution:
    """The welfare-maximal correlated equilibrium of one simultaneous stage.

    `payoff_a[i, j]` is A's payoff when A plays i and B plays j; `payoff_b`
    is indexed the same way (B's payoff at the same joint action). Variables
    are the nine (actions²) joint probabilities p[i, j]; the incentive
    constraints say a side told "play i" cannot do better with i':

        for A, all i ≠ i':   Σ_j p[i, j] · (A[i, j] − A[i', j]) ≥ 0
        for B, all j ≠ j':   Σ_i p[i, j] · (B[i, j] − B[i, j']) ≥ 0

    with p ≥ 0 and Σ p = 1; the objective maximises Σ p · (A + B). Falls back
    to the uniform-on-best-cell distribution only if HiGHS reports anything
    but optimal — which the theory says cannot happen, and the status field
    records if it ever does.
    """
    from scipy.optimize import linprog

    actions = int(payoff_a.shape[0])
    n = actions * actions

    def idx(i: int, j: int) -> int:
        return i * actions + j

    rows: list[np.ndarray] = []
    for i in range(actions):
        for i_alt in range(actions):
            if i_alt == i:
                continue
            row = np.zeros(n)
            for j in range(actions):
                # linprog wants A_ub x ≤ b_ub, so the ≥ constraint is negated.
                row[idx(i, j)] = payoff_a[i_alt, j] - payoff_a[i, j]
            rows.append(row)
    for j in range(actions):
        for j_alt in range(actions):
            if j_alt == j:
                continue
            row = np.zeros(n)
            for i in range(actions):
                row[idx(i, j)] = payoff_b[i, j_alt] - payoff_b[i, j]
            rows.append(row)
    a_ub = np.vstack(rows)
    b_ub = np.zeros(len(rows))
    a_eq = np.ones((1, n))
    b_eq = np.array([1.0])
    objective = -(payoff_a + payoff_b).reshape(n)

    result = linprog(
        objective, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq,
        bounds=[(0.0, 1.0)] * n, method="highs",
    )
    if result.status == 0 and result.x is not None:
        joint = np.clip(np.asarray(result.x, dtype=float), 0.0, None).reshape(
            actions, actions
        )
        status = "optimal"
    else:  # pragma: no cover - the CE polytope is never empty
        joint = np.zeros((actions, actions))
        best = int(np.argmax(payoff_a + payoff_b))
        joint.reshape(-1)[best] = 1.0
        status = str(result.message)
    joint[joint < _MASS_FLOOR] = 0.0
    total = float(joint.sum())
    joint = joint / total if total > 0 else np.full((actions, actions), 1.0 / n)

    mix_a = joint.sum(axis=1)
    mix_b = joint.sum(axis=0)
    product = np.outer(mix_a, mix_b)
    nash_gap = 0.5 * float(np.abs(joint - product).sum())
    return StageSolution(
        joint=joint,
        mix_a=mix_a,
        mix_b=mix_b,
        value_a=float((joint * payoff_a).sum()),
        value_b=float((joint * payoff_b).sum()),
        nash_gap=nash_gap,
        status=status,
    )


def concept_line(horizon: int) -> str:
    return (
        f"correlated-equilibrium LP (welfare-maximal, HiGHS) MPBE, finite "
        f"horizon H={horizon}, backward induction; nash_gap = total variation "
        "of the joint from the product of its marginals (0 = a Nash point)"
    )
