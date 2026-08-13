"""The state space, and why every axis of it is coarse.

The game is solved EXACTLY by backward induction, and indirect inference
(docs/game-spec.md section 2.1) needs thousands of those solves to fit five
structural parameters. That budget is what sets every resolution here:

    6 intensity x 3 capability x 5 x 5 belief  =  450 states
    x 9 joint actions x 4 type pairs x 4 periods

which solves in well under a second. A finer grid buys precision the panel
cannot support anyway — 28,282 dyad-quarters over 186 dyads is not enough to
estimate transitions between forty intensity levels — and costs the ability
to fit the game at all. The coarseness is a deliberate trade and it should be
stated wherever the output is shown.

INTENSITY IS RELATIVE, ALWAYS. The axis is the dyad's departure from its OWN
EWMA baseline, which is what the escalation classifier already computes: a
−6.0 is routine for a rivalry and a rupture for an alliance. A shared
absolute grid across dyads would make the game's states mean different things
for different pairs, which is the same error that made a pooled model score
0.92 while ranking each dyad's own quarters backwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

#: Intensity bands, as multiples of the dyad's own historical scale. The top
#: band is open — a rupture has no ceiling and binning one would cap the
#: quantity the forecast exists to see.
INTENSITY_EDGES: tuple[float, ...] = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0)
#: Capability bands on the challenger/leader CINC ratio. Three, because the
#: Organski window is about approach to parity, not a fine gradient.
CAPABILITY_EDGES: tuple[float, ...] = (0.0, 0.5, 0.85)
#: Belief grid over P(opponent is resolute), per side.
BELIEF_LEVELS: int = 5

#: The two types. Private, drawn once per episode from a prior tied to the
#: actor's AttributeEstimate.resolve — the ontology already holds it.
TYPES: tuple[str, ...] = ("irresolute", "resolute")

#: The action set IS the quad-class partition the archive already codes every
#: event into, collapsed to its escalation direction. Nothing invented: a
#: game whose actions did not correspond to coded events could never be
#: estimated from the record or checked against it.
ACTIONS: tuple[str, ...] = ("de-escalate", "hold", "escalate")

#: Which quad class an action produces, for turning a solved path back into
#: events the transmission layer can price.
ACTION_QUAD: dict[str, str] = {
    "escalate": "material_conflict",
    "hold": "verbal_conflict",
    "de-escalate": "verbal_cooperation",
}


def action_positions() -> np.ndarray:
    """Where the actions sit ON THE GOLDSTEIN SCALE, normalised to [-1, 1].

    WHY THIS IS NOT (−1, 0, +1). Treating de-escalate / hold / escalate as
    equally spaced ordinals asserts that the step from talking to shooting is
    the same size as the step from cooperating to talking. Goldstein says
    otherwise, and Goldstein is already the scale every event in this archive
    is coded on — so the spacing is read off the codebook rather than assumed.

    The mean Goldstein of each quad class is computed from the CAMEO codebook
    itself, so a codebook correction moves the game's action space with it.

    NOTE WHAT THIS IS AND IS NOT. This puts Goldstein in the STATE and in the
    geometry of the action space. It does NOT make Goldstein a payoff.
    Goldstein measures how conflictual an ACTION is, as coded; it says nothing
    about how much a given state MINDS. Treating a coder's severity scale as a
    utility function is the error this separation exists to avoid — what an
    escalation costs each side is precisely the unknown that
    `Payoffs.cost_resolute` / `cost_irresolute` are fitted to recover.
    """
    from core.classifier import typing as event_typing

    by_quad: dict[str, list[float]] = {}
    for entry in event_typing.codebook_entries():
        by_quad.setdefault(str(entry["quad_class"]), []).append(float(entry["goldstein"]))
    means = [
        float(np.mean(by_quad.get(ACTION_QUAD[action], [0.0]))) or 0.0
        for action in ACTIONS
    ]
    scale = max(abs(v) for v in means) or 1.0
    # SIGN FLIPPED. Goldstein runs negative-for-conflictual, so the raw means
    # order escalation LOWEST; used directly in a "who pressed harder" term
    # they would reward backing down. The axis this returns is escalation
    # PRESSURE — increasing toward conflict — which is what the payoff reads.
    return np.array([-v / scale for v in means], dtype=float)


def intensity_band(value: float, scale: float) -> int:
    """Which band a departure of `value` falls in, given the dyad's own scale.

    `scale` is the dyad's historical typical departure — its own yardstick.
    A dyad with no history yet has scale 0 and lands in band 0, which is the
    honest reading: nothing to be a departure FROM.
    """
    if scale <= 0.0:
        return 0
    ratio = value / scale
    band = 0
    for index, edge in enumerate(INTENSITY_EDGES):
        if ratio >= edge:
            band = index
    return band


def capability_band(ratio: float) -> int:
    band = 0
    for index, edge in enumerate(CAPABILITY_EDGES):
        if ratio >= edge:
            band = index
    return band


def belief_grid() -> np.ndarray:
    """The discretized belief axis: P(opponent resolute), endpoints included.

    Endpoints are included deliberately. A belief of 0 or 1 is a side that
    considers the question settled, and excluding those would make certainty
    unrepresentable — which is exactly the state a long rivalry converges to.
    """
    return np.linspace(0.0, 1.0, BELIEF_LEVELS)


@dataclass(frozen=True)
class State:
    """A payoff-relevant state. Markov: strategies depend on this and nothing
    else, which is what makes backward induction exact and the policy
    storable as a table."""

    intensity: int      # index into INTENSITY_EDGES
    capability: int     # index into CAPABILITY_EDGES
    belief_a: int       # A's belief that B is resolute, index into belief_grid
    belief_b: int       # B's belief that A is resolute

    def index(self) -> int:
        return (
            ((self.intensity * len(CAPABILITY_EDGES) + self.capability) * BELIEF_LEVELS
             + self.belief_a) * BELIEF_LEVELS
            + self.belief_b
        )


def all_states() -> list[State]:
    """Every state, in index order — the solver's table layout."""
    return [
        State(x, k, ba, bb)
        for x in range(len(INTENSITY_EDGES))
        for k in range(len(CAPABILITY_EDGES))
        for ba in range(BELIEF_LEVELS)
        for bb in range(BELIEF_LEVELS)
    ]


def state_count() -> int:
    return len(INTENSITY_EDGES) * len(CAPABILITY_EDGES) * BELIEF_LEVELS**2


def dyad_scale(intensities: list[float]) -> float:
    """A dyad's own yardstick: the median of its NON-ZERO departures.

    Median rather than mean because a single war would otherwise set the
    scale for a decade of ordinary friction, and non-zero because the quiet
    quarters are the majority of any dyad's history and would drag the
    yardstick to nothing.
    """
    active = [v for v in intensities if v > 0.0]
    if not active:
        return 0.0
    return float(np.median(active))


def classify(rows: list[dict[str, Any]], *, capability_ratio: float = 0.5) -> list[int]:
    """One dyad's panel rows → its intensity band per quarter.

    The scale is EXPANDING, computed from each row's own past, for the same
    reason every feature in core/models is: a yardstick that saw the future
    would make the transition counts below a description of hindsight.
    """
    intensities = [float(r["intensity"]) for r in rows]
    bands = []
    for i, value in enumerate(intensities):
        bands.append(intensity_band(value, dyad_scale(intensities[:i])))
    return bands
