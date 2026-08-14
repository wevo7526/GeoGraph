"""The dyad's OPENING STATE, read from data instead of assumed.

The game's opening conditions were hardcoded — capability "1 — balanced",
beliefs 0.5/0.5 — for every dyad, in the frozen sequence forecast and on
every interactive solve. The graph holds CINC capability estimates and the
archive holds each side's observed actions, so both were assumptions wearing
a default where a measurement existed:

- CAPABILITY comes off the two actors' latest `clout` AttributeEstimates
  (the deep tier's CINC), banded on the challenger/leader ratio exactly as
  `state.CAPABILITY_EDGES` defines the axis.
- BELIEFS come from the game's OWN Bayes rule run over the dyad's observed
  recent joint actions: a side that has spent three years escalating walks in
  believed resolute, one that has folded walks in believed irresolute. The
  update rule is `solve.posterior` — the same likelihoods the equilibrium
  uses along a path — so the opening belief and the in-game belief dynamics
  are one mechanism, not two.

Everything returns its provenance: a reader must be able to see whether an
opening state was measured or defaulted, because the two deserve different
trust. Deterministic; no model output enters here (the ML bridge is
`core/games/bridge.py`, and it is labelled).
"""

from __future__ import annotations

from typing import Any

from core.games import solve as solve_module
from core.games import state as state_module
from core.graph import kuzu_store

#: Quarters of observed actions the belief filter consumes. Long enough to
#: hold a posture, short enough that a détente is believed within a few
#: years of it starting.
BELIEF_QUARTERS = 12


def dyad_actors(dyad_id: str) -> tuple[str, str]:
    """`dyad:cow-630--cow-666` → the two actor node ids, sorted — the exact
    inverse of `escalation.dyad_id`."""
    bare = dyad_id.split(":", 1)[-1]
    first, _, second = bare.partition("--")
    return f"actor:{first}", f"actor:{second or first}"


def _latest_clout(conn: Any, actor_id: str) -> float | None:
    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor {node_id: $id})-[:HAS_ESTIMATE]->(s:AttributeEstimate) "
        "WHERE s.attribute = 'clout' "
        "RETURN s.value_mean AS value, s.as_of AS as_of "
        "ORDER BY s.as_of DESC LIMIT 1",
        {"id": actor_id},
    )
    if not rows or rows[0]["value"] is None:
        return None
    return float(rows[0]["value"])


def capability_state(conn: Any | None, dyad_id: str) -> dict[str, Any]:
    """The capability band, measured from CINC where the graph holds it.

    Falls back to the balanced band with `source: "default"` — visibly, so a
    defaulted band never wears a measurement's face.
    """
    if conn is not None:
        actor_a, actor_b = dyad_actors(dyad_id)
        clout_a = _latest_clout(conn, actor_a)
        clout_b = _latest_clout(conn, actor_b)
        if clout_a is not None and clout_b is not None and max(clout_a, clout_b) > 0:
            ratio = min(clout_a, clout_b) / max(clout_a, clout_b)
            return {
                "band": state_module.capability_band(ratio),
                "ratio": round(ratio, 4),
                "source": "cinc",
            }
    return {"band": 1, "ratio": None, "source": "default"}


def filtered_beliefs(
    joint: dict[tuple[str, int], tuple[str, str]],
    dyad_id: str,
    payoffs: solve_module.Payoffs,
    *,
    quarters: int = BELIEF_QUARTERS,
) -> dict[str, Any]:
    """P(resolute) per side, filtered from the dyad's observed recent actions.

    `belief_a` is A's belief that B IS RESOLUTE, so it updates on B's
    observed actions (and vice versa) — the same orientation the solver uses.
    Starts from the uninformed 0.5 prior and folds the last `quarters`
    quarters in order.
    """
    observed = sorted(
        (quarter, actions)
        for (dyad, quarter), actions in joint.items()
        if dyad == dyad_id
    )[-quarters:]
    belief_a = 0.5  # A's belief about B
    belief_b = 0.5  # B's belief about A
    for _, (action_a, action_b) in observed:
        belief_a = solve_module.posterior(
            belief_a, state_module.ACTIONS.index(action_b), payoffs
        )
        belief_b = solve_module.posterior(
            belief_b, state_module.ACTIONS.index(action_a), payoffs
        )
    return {
        "a": round(belief_a, 4),
        "b": round(belief_b, 4),
        "quarters_observed": len(observed),
        "source": "bayes_filter" if observed else "default",
    }
