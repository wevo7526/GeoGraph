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
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from core.games import family as family_module
from core.games import state as state_module

#: Quantal-response precision. Higher is closer to exact best response; at 0
#: every action is equally likely. Chosen an order of magnitude above the
#: payoff scale so play is decisive but not knife-edge, and held FIXED rather
#: than fitted — a free precision parameter can absorb any misfit in the
#: payoffs it is supposed to be revealing.
PRECISION = 4.0

#: The most certain a filtered belief about the other side's type may get.
BELIEF_CEILING = 0.9

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


#: The ally game's starting parameters — used where no fitted ally artifact
#: ships, and as the fit's starting point. Read against `ally_stage_payoff`:
#: the shared good's marginal value to one partner is stake/2 (times the
#: capability scale), so a committed partner CARRIES only if its private cost
#: sits under that, and a reluctant one rides unless the abandonment cost of
#: withholding under strain outweighs the saving. Under Fearon's defaults
#: (cost_resolute 0.4 against a marginal value of ~0.33) even the committed
#: type would ride, and the default alliance would be a rift — which is a
#: statement about a default, not about alliances.
ALLY_DEFAULTS = Payoffs(
    discount=0.9, cost_resolute=0.15, cost_irresolute=0.9, stake=1.0, audience=0.5,
)


#: The rival game's starting parameters: a hardliner presses at calm (its cost
#: of pressing sits under the share a press wins) and stops when the
#: recklessness cost of pressing at high friction outweighs it; an
#: accommodating type holds. The fit (`fit_game.py --family rival`) decides.
RIVAL_DEFAULTS = Payoffs(
    discount=0.9, cost_resolute=0.1, cost_irresolute=1.0, stake=1.0, audience=0.5,
)


def defaults_for(space: family_module.ActionSpace) -> Payoffs:
    """The payoffs a family's game starts from when nothing is fitted."""
    if space.family == "ally":
        return ALLY_DEFAULTS
    if space.family == "rival":
        return RIVAL_DEFAULTS
    return Payoffs()


#: An ally's CONTRIBUTION to the shared good, by action index — commit
#: carries it, affirm keeps it going, withhold does not carry it. The
#: contribution scale is what `Payoffs.stake` (the value of the shared good)
#: and the type costs (the private cost of carrying it) are measured against.
_CONTRIBUTION: tuple[float, float, float] = (1.0, 0.5, 0.0)


def ally_stage_payoff(
    action: int,
    other: int,
    intensity: int,
    capability: int,
    type_index: int,
    payoffs: Payoffs,
) -> float:
    """One period's payoff to an ALLY, given both partners' actions and the
    state — Olson & Zeckhauser (1966), alliance burden-sharing.

    THE SAME FIVE PARAMETERS, RE-READ. A family earns a payoff here only if
    `scripts/fit_game.py` can fit it by the same indirect inference, so the
    ally game is written over the same `Payoffs` the adversary game uses, each
    field with an ally meaning:

      - `stake`            the value of the shared good (the alliance's
                           security), enjoyed by BOTH partners whoever carries
                           it — that is what makes it a public good;
      - `cost_resolute`    the private cost of carrying it for a COMMITTED
                           partner (the type that will bear it), and
      - `cost_irresolute`  for a RELUCTANT one — the ordering constraint the
                           fit already enforces;
      - `audience`         the cost of WITHHOLDING when the alliance is already
                           strained (intensity in its upper half): a partner
                           seen to abandon at the moment of strain pays a
                           reputational cost, the alliance-management analogue
                           of Fearon's audience cost;
      - `discount`         patience.

    Three terms:
      - the shared good, `stake × sqrt((own + other contribution) / 2)`,
        scaled by the pair's capability band exactly as Fearon's share is (the
        larger the pair's stake in the system, the more the good is worth) —
        public, so a partner enjoys the other's contribution at no cost, which
        is the free-riding incentive the model exists to state. CONCAVE, as in
        Olson-Zeckhauser: the first unit of the shared good is worth the most,
        so contributing SOMETHING is optimal for both types and the question is
        how much — which is what makes "affirm" (the routine assurance an
        alliance runs on) the resting action rather than a point the corners
        skip. A linear good made every partner a corner solution and the fit
        (2026-08-16) put 44% of baseline quarters on withhold against 4%
        observed;
      - the private cost, `cost_of(type) × own contribution` — a committed
        partner's is low, so it carries; a reluctant one's is high, so it
        stops at assurance or rides;
      - the abandonment cost for withholding under strain.

    So the game's own logic reproduces Olson-Zeckhauser: the committed partner
    over-provides, the reluctant one free-rides, and the bad end — a RIFT — is
    both withholding when friction is already high, not war between them.

    WHAT THE FIT SAYS ABOUT IT (2026-08-16, pooled over 31,770 in-window ally
    dyad-quarters, `models/game-ally-*.json`): converged, stake interior
    (0.49), costs 0.30 / 0.62, abandonment 0.61, discount at its ceiling. It
    reproduces the commit share and the high-friction mix and OVERSTATES
    withholding at baseline (0.42 simulated against 0.04 observed at band 0),
    because nothing in this payoff rewards routine assurance when the alliance
    is calm — the assurance good, the value of simply keeping the alliance
    warm, is the term the model lacks. It is written down here rather than
    invented as a sixth parameter the fit could not identify; the artifact
    carries the observed and simulated mixes so the gap is a number, not a
    remark.
    """
    bands = len(state_module.INTENSITY_EDGES)
    strength = (capability + 1) / len(state_module.CAPABILITY_EDGES)
    own = _CONTRIBUTION[action]
    theirs = _CONTRIBUTION[other]
    good = payoffs.stake * math.sqrt((own + theirs) / 2.0) * strength
    borne = payoffs.cost_of(type_index) * own
    # THE FRICTION LEVEL BITES ON WITHHOLDING, not on carrying: at high
    # friction the reluctant partner's temptation to withhold is exactly what
    # the abandonment cost has to answer, and carrying at high friction is the
    # repair the alliance runs on.
    abandonment = payoffs.audience if (action == 2 and intensity >= bands // 2) else 0.0
    return float(good - borne - abandonment)


def rival_stage_payoff(
    action: int,
    other: int,
    intensity: int,
    capability: int,
    type_index: int,
    payoffs: Payoffs,
) -> float:
    """One period's payoff to a RIVAL — repeated competition below the use of
    force, whose bad end is a coercive turn.

    THE SAME FIVE PARAMETERS, RE-READ ONCE MORE. A rivalry conducted in
    argument (US–China, US–Russia, UK–Russia — declared rivalries whose
    record is under the adversary cut) is not a crisis over a stake held under
    threat of force; it is a standing competition in which each side chooses
    to ease, hold or press, the prize is contested by pressing, and the thing
    to fear is not backing down but PRESSING AT HIGH FRICTION, which is how a
    competition crosses into coercion (family 1). So:

      - `stake`            the prize the competition is over (influence,
                           standing, the terms of the relationship), won in
                           share by pressing harder than the other side —
                           Fearon's own share term, on the Goldstein spacing;
      - `cost_resolute`    the cost of pressing for a HARDLINER type, and
      - `cost_irresolute`  for an ACCOMMODATING one;
      - `audience`         here the RECKLESSNESS cost — pressing when friction
                           is already high risks the coercive turn, and the
                           cost rises with the level (Fearon charges the
                           audience cost for BACKING DOWN from a high band;
                           this game charges it for pressing at one);
      - `discount`         patience.

    The bad end — the coercive turn — is both sides pressing at high friction,
    which this payoff makes expensive in proportion to how high.
    """
    bands = len(state_module.INTENSITY_EDGES)
    strength = (capability + 1) / len(state_module.CAPABILITY_EDGES)
    positions = _action_positions()
    press = (positions[action] - positions[other]) / 2.0
    share = payoffs.stake * (0.5 + 0.5 * press) * strength
    level = intensity / max(bands - 1, 1)
    pressure = max(0.0, float(positions[action]))
    borne = payoffs.cost_of(type_index) * pressure
    recklessness = payoffs.audience * level if action == 2 else 0.0
    return float(share - borne - recklessness)


def stage_payoff(
    action: int,
    other: int,
    intensity: int,
    capability: int,
    type_index: int,
    payoffs: Payoffs,
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> float:
    """One period's payoff to a side, given both actions and the state — in
    the FAMILY'S game. The adversary and rival spaces play Fearon's crisis
    bargaining below; the ally space plays `ally_stage_payoff`.

    Three terms, each tied to something the archive holds:
      - the share of the stake won, rising with capability and with pressing
        harder than the opponent (CINC via AttributeEstimate.clout);
      - the cost of the intensity being sustained, which is what SEPARATES
        the types and is therefore where the market's measured effect enters
        once AFFECTED is populated;
      - an audience cost for de-escalating from an escalated position, which
        is why rivalries get stuck (alliance structure via RELATES_TO).
    """
    if space.family == "ally":
        return ally_stage_payoff(action, other, intensity, capability, type_index, payoffs)
    if space.family == "rival":
        return rival_stage_payoff(action, other, intensity, capability, type_index, payoffs)
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


def posterior(
    prior: float, action: int, payoffs: Payoffs,
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> float:
    """P(type 1 | action), by Bayes — resolute for an adversary, committed for
    an ally.

    The likelihoods are ordered rather than free: a resolute type escalates
    more readily because escalation costs it less, so escalation is evidence
    of resolve and backing down is evidence against. Fixing the ORDER while
    leaving the strength to the payoffs is what keeps this from being a second
    set of parameters quietly doing the work.

    THE SIGNAL IS THE FAMILY'S. For an ally the type-1 partner is the
    COMMITTED one and its cheap action is COMMIT (index 0), so the order
    mirrors: `space.signal` names the index that is evidence of type 1, and
    the action is reflected onto the adversary's ordering before the
    likelihoods are read.
    """
    signal = action if space.signal == 2 else 2 - action
    ratio = payoffs.cost_irresolute / max(payoffs.cost_resolute, 1e-6)
    likelihood_resolute = (1.0, 1.0, min(ratio, 4.0))[signal]
    likelihood_irresolute = (min(ratio, 4.0), 1.0, 1.0)[signal]
    numerator = prior * likelihood_resolute
    denominator = numerator + (1.0 - prior) * likelihood_irresolute
    updated = float(numerator / denominator) if denominator > 0 else prior
    # NEVER CERTAIN. Twelve quarters of one action at a likelihood ratio of
    # four drove filtered beliefs to exactly 1.0 (2026-08-15: every busy pair
    # read "100% resolute"), and a belief at 1.0 collapses the expected
    # policy onto one type and every course onto one path. A type is a
    # private thing the archive infers, not observes; the filter keeps a
    # floor of doubt on both sides.
    return min(BELIEF_CEILING, max(1.0 - BELIEF_CEILING, updated))


SOLVERS = ("qre", "lp")


def solve(
    kernel: np.ndarray,
    payoffs: Payoffs,
    *,
    horizon: int = 4,
    precision: float = PRECISION,
    solver: str = "qre",
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> dict[str, Any]:
    """The equilibrium policy over (period, intensity, capability, type).

    Beliefs are carried on the grid but the value recursion runs over the
    payoff-relevant core — intensity, capability, own type — with the belief
    entering through the opponent's expected mixture. That keeps the table at
    a size thousands of solves can afford, which is the whole constraint.

    `solver` picks the stage concept: "qre" (the fitted logit response — the
    default and the estimator's concept) or "lp" (the welfare-maximal
    correlated equilibrium by linear programming, `equilibrium.py`, with the
    distance from a Nash point reported as `nash_gap`). The recursion, the
    policy table and everything downstream are shared.

    Returns policy[period][intensity][capability][type] → mixture over the
    space's actions, plus the value function it came from. `space` picks the
    family's game (`stage_payoff`); the recursion is the same for all.
    """
    if solver not in SOLVERS:
        raise ValueError(f"solver must be one of {SOLVERS}, got {solver!r}")
    from core.games import equilibrium as equilibrium_module

    bands = len(state_module.INTENSITY_EDGES)
    caps = len(state_module.CAPABILITY_EDGES)
    actions = len(space.actions)
    types = len(space.types)

    value = np.zeros((bands, caps, types))
    policy = np.zeros((horizon, bands, caps, types, actions))
    # The opponent's marginal at every solved cell — the LP's joint is not a
    # product, so B's side is kept rather than re-derived from A's.
    policy_b = np.zeros((horizon, bands, caps, types, actions))
    gaps: list[float] = []
    entropies: list[float] = []
    violations: list[float] = []
    lp_status_ok = True
    # The period-0 stage matrices (payoff + discounted continuation) at every
    # state — what a reader sees when asked "what game is being played at the
    # opening". 2 × (bands, caps, types, actions, actions); small.
    opening_a = np.zeros((bands, caps, types, actions, actions))
    opening_b = np.zeros((bands, caps, types, actions, actions))

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
                                stage_payoff(a, b, x, k, own, payoffs, space)
                                + payoffs.discount * future
                            )
                            matrix_b[a, b] = (
                                stage_payoff(b, a, x, k, own, payoffs, space)
                                + payoffs.discount * future
                            )
                    if period == 0:
                        opening_a[x, k, own] = matrix_a
                        opening_b[x, k, own] = matrix_b
                    if solver == "lp":
                        # ONE TEMPERATURE FOR BOTH CONCEPTS: the correlated
                        # selection is regularised at the same 1/precision the
                        # logit response uses, so the two stage concepts differ
                        # in the equilibrium notion and NOT in how decisively
                        # states are assumed to choose.
                        stage = equilibrium_module.solve_stage_lp(
                            matrix_a, matrix_b, temperature=1.0 / precision
                        )
                        policy[period, x, k, own] = stage.mix_a
                        policy_b[period, x, k, own] = stage.mix_b
                        # Under a correlated joint the value is Σ p·A, which
                        # collapses to mix_a @ A @ mix_b exactly when the
                        # joint is a product (nash_gap = 0).
                        value[x, k, own] = stage.value_a
                        gaps.append(stage.nash_gap)
                        entropies.append(stage.entropy)
                        violations.append(stage.ce_violation)
                        lp_status_ok = lp_status_ok and stage.status == "optimal"
                        continue
                    mix_a, mix_b = solve_stage(matrix_a, matrix_b, precision=precision)
                    policy[period, x, k, own] = mix_a
                    policy_b[period, x, k, own] = mix_b
                    # A's expected value is mix_a @ payoff_a @ mix_b — the
                    # OPPONENT'S equilibrium mixture, not A's own. Using mix_a
                    # on both sides is exact only for a symmetric stage game,
                    # and the counted kernel is not symmetrized, so it biased
                    # every continuation value.
                    value[x, k, own] = float(mix_a @ (matrix_a @ mix_b))
    out: dict[str, Any] = {
        "policy": policy,
        "policy_b": policy_b,
        "value": value,
        "opening_matrices": (opening_a, opening_b),
        "horizon": horizon,
        "precision": precision,
        "solver": solver,
        "family": space.family,
        "actions": list(space.actions),
        "types": list(space.types),
        "concept": (
            equilibrium_module.concept_line(horizon, 1.0 / precision)
            if solver == "lp"
            else (
                f"quantal-response MPBE, finite horizon H={horizon}, "
                f"precision {precision} held fixed"
            )
        ),
    }
    if solver == "lp":
        arr = np.asarray(gaps) if gaps else np.zeros(1)
        ent = np.asarray(entropies) if entropies else np.zeros(1)
        viol = np.asarray(violations) if violations else np.zeros(1)
        out["nash_gap"] = {
            "mean": round(float(arr.mean()), 4),
            "max": round(float(arr.max()), 4),
            # The share of stage games solved AT a Nash point.
            "share_product_form": round(float((arr < 1e-6).mean()), 4),
            "stage_games": int(len(gaps)),
            "all_optimal": bool(lp_status_ok),
            # THE DEGENERACY AUDIT. A pure joint has entropy 0, and that is
            # what the unregularised welfare LP returned at every stage — the
            # reason the region map used to name one course at 100%. Reported
            # so the claim "this is not a vertex" is checkable, alongside the
            # largest incentive constraint any stage left unmet.
            "entropy_mean": round(float(ent.mean()), 4),
            "entropy_min": round(float(ent.min()), 4),
            "ce_violation_max": float(f"{float(viol.max()):.3g}"),
        }
    return out
