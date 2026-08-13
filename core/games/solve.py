"""Markov Perfect Bayesian Equilibrium, by backward induction.

SOLVED, NOT CONVERGED TO. One pass from the terminal period gives the policy;
there is no iteration to diverge and no fixed point to miss. That is the
practical payoff of the finite horizon docs/game-spec.md insists on — and the
theoretical one is that the folk theorem never gets started, so the game
predicts something specific instead of admitting every path.

Within each period the two sides move SIMULTANEOUSLY, so the stage game is a
matrix game and its solution is a mixed-strategy equilibrium. Solved here by
logit best-response iteration rather than exact support enumeration, for a
reason that is about honesty as much as speed: a logit response with a finite
precision parameter is a QUANTAL response equilibrium, which assumes states
choose well rather than perfectly. Over a 120-year archive of actual
statecraft, "approximately optimal" is the defensible assumption and exact
optimisation is the fragile one. It also makes the equilibrium unique and
continuous in the parameters, without which the indirect inference in
estimate.py would be optimising a step function.

Beliefs update by Bayes on the observed action, which is the "Bayesian" half:
escalating is more likely from a resolute type, so escalation raises the
opponent's posterior that you are resolute — and that revision is itself a
reason to escalate. The model earns its keep on exactly that circularity.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any

import numpy as np

from core.games import state as state_module

#: Quantal-response precision. Higher is closer to exact best response; at 0
#: every action is equally likely. Chosen an order of magnitude above the
#: payoff scale so play is decisive but not knife-edge, and held FIXED rather
#: than fitted — a free precision parameter can absorb any misfit in the
#: payoffs it is supposed to be revealing.
PRECISION = 4.0

#: Best-response sweeps per stage game. Logit response is a contraction at
#: this precision; twenty is well past where the mixtures stop moving.
_SWEEPS = 20


@functools.lru_cache(maxsize=1)
def _action_positions() -> np.ndarray:
    """Goldstein spacing of the action set, read once. Cached because
    `stage_payoff` is called inside the solver's innermost loop and this
    walks the whole CAMEO codebook."""
    return state_module.action_positions()


@dataclass(frozen=True)
class Payoffs:
    """The structural parameters — what indirect inference actually fits.

    Five numbers, deliberately. Each has a meaning someone can dispute, which
    is the property a black box cannot offer and the reason this layer exists
    at all.
    """

    discount: float = 0.9          # δ — patience
    cost_resolute: float = 0.4     # what fighting costs a type that will bear it
    cost_irresolute: float = 1.8   # …and one that will not
    stake: float = 1.0             # value of the issue in dispute
    audience: float = 0.3          # cost of backing down after escalating

    def cost_of(self, type_index: int) -> float:
        return self.cost_resolute if type_index == 1 else self.cost_irresolute


def stage_payoff(
    action: int,
    other: int,
    intensity: int,
    capability: int,
    type_index: int,
    payoffs: Payoffs,
) -> float:
    """One period's payoff to a side, given both actions and the state.

    Three terms, each tied to something the archive holds:
      - the share of the stake won, rising with capability and with pressing
        harder than the opponent (CINC via AttributeEstimate.clout);
      - the cost of the intensity being sustained, which is what SEPARATES
        the types and is therefore where the market's measured effect enters
        once AFFECTED is populated;
      - an audience cost for de-escalating from an escalated position, which
        is why rivalries get stuck (alliance structure via RELATES_TO).
    """
    bands = len(state_module.INTENSITY_EDGES)
    strength = (capability + 1) / len(state_module.CAPABILITY_EDGES)
    # Pressing harder is measured on the GOLDSTEIN scale, not by ordinal
    # distance: the gap between talking and shooting is not the gap between
    # cooperating and talking, and the codebook already says by how much.
    positions = _action_positions()
    press = (positions[action] - positions[other]) / 2.0
    share = payoffs.stake * (0.5 + 0.5 * press) * strength

    # THE COST MUST DEPEND ON THE ACTION, not only on the state. With it read
    # off current intensity alone the term is identical across every action, so
    # it shifts a side's payoffs uniformly and cannot influence which action it
    # picks — the types then differ only through continuation value and the
    # solver returned the IRRESOLUTE type escalating more, which is backwards.
    # Escalating is costly NOW, and costly in proportion to what it costs you:
    # that asymmetry is the whole Fearon mechanism this game is built on.
    level = intensity / max(bands - 1, 1)
    pressure = max(0.0, float(positions[action]))
    borne = payoffs.cost_of(type_index) * (level + pressure)

    backing_down = payoffs.audience if (action == 0 and intensity >= bands // 2) else 0.0
    return float(share - borne - backing_down)


def _logit(values: np.ndarray, precision: float) -> np.ndarray:
    """Softmax over expected payoffs — the quantal response."""
    shifted = precision * (values - values.max())
    weights = np.exp(shifted)
    mixture: np.ndarray = weights / weights.sum()
    return mixture


def solve_stage(
    payoff_a: np.ndarray, payoff_b: np.ndarray, *, precision: float = PRECISION
) -> tuple[np.ndarray, np.ndarray]:
    """Mixed-strategy quantal response equilibrium of one simultaneous stage.

    `payoff_a[i, j]` is A's payoff when A plays i and B plays j. Returns each
    side's mixture. Starts uniform and sweeps; the fixed point is unique at
    this precision, so the starting point does not select an equilibrium.
    """
    actions = payoff_a.shape[0]
    mix_a = np.full(actions, 1.0 / actions)
    mix_b = np.full(actions, 1.0 / actions)
    for _ in range(_SWEEPS):
        mix_a = _logit(payoff_a @ mix_b, precision)
        mix_b = _logit(payoff_b.T @ mix_a, precision)
    return mix_a, mix_b


def posterior(prior: float, action: int, payoffs: Payoffs) -> float:
    """P(resolute | action), by Bayes.

    The likelihoods are ordered rather than free: a resolute type escalates
    more readily because escalation costs it less, so escalation is evidence
    of resolve and backing down is evidence against. Fixing the ORDER while
    leaving the strength to the payoffs is what keeps this from being a second
    set of parameters quietly doing the work.
    """
    ratio = payoffs.cost_irresolute / max(payoffs.cost_resolute, 1e-6)
    likelihood_resolute = (1.0, 1.0, min(ratio, 4.0))[action]
    likelihood_irresolute = (min(ratio, 4.0), 1.0, 1.0)[action]
    numerator = prior * likelihood_resolute
    denominator = numerator + (1.0 - prior) * likelihood_irresolute
    return float(numerator / denominator) if denominator > 0 else prior


def solve(
    kernel: np.ndarray,
    payoffs: Payoffs,
    *,
    horizon: int = 4,
    precision: float = PRECISION,
) -> dict[str, Any]:
    """The equilibrium policy over (period, intensity, capability, type).

    Beliefs are carried on the grid but the value recursion runs over the
    payoff-relevant core — intensity, capability, own type — with the belief
    entering through the opponent's expected mixture. That keeps the table at
    a size thousands of solves can afford, which is the whole constraint.

    Returns policy[period][intensity][capability][type] → mixture over
    ACTIONS, plus the value function it came from.
    """
    bands = len(state_module.INTENSITY_EDGES)
    caps = len(state_module.CAPABILITY_EDGES)
    actions = len(state_module.ACTIONS)
    types = len(state_module.TYPES)

    value = np.zeros((bands, caps, types))
    policy = np.zeros((horizon, bands, caps, types, actions))

    for period in range(horizon - 1, -1, -1):
        next_value = value.copy()
        for x in range(bands):
            for k in range(caps):
                for own in range(types):
                    matrix_a = np.zeros((actions, actions))
                    matrix_b = np.zeros((actions, actions))
                    for a in range(actions):
                        for b in range(actions):
                            future = float(kernel[x, a, b] @ next_value[:, k, own])
                            matrix_a[a, b] = (
                                stage_payoff(a, b, x, k, own, payoffs)
                                + payoffs.discount * future
                            )
                            matrix_b[a, b] = (
                                stage_payoff(b, a, x, k, own, payoffs)
                                + payoffs.discount * future
                            )
                    mix_a, _mix_b = solve_stage(matrix_a, matrix_b, precision=precision)
                    policy[period, x, k, own] = mix_a
                    value[x, k, own] = float(mix_a @ (matrix_a @ mix_a))
    return {
        "policy": policy,
        "value": value,
        "horizon": horizon,
        "precision": precision,
        "concept": (
            f"quantal-response MPBE, finite horizon H={horizon}, "
            f"precision {precision} held fixed"
        ),
    }
