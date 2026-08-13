"""P(x' | x, a₁, a₂) — counted from the archive, never assumed.

The one part of the game that is measured rather than solved. Everything else
in this package is arithmetic over parameters; this is the empirical fact the
parameters have to explain.

WHY COUNTED AND NOT LEARNED. The panel is 28,282 dyad-quarters. A learned
kernel over six intensity bands and nine joint actions has 324 cells to fill
from that, and would memorise long before it generalised. Counting with a
floor is the honest estimator at this sample size, and it fails visibly: a
cell nobody observed says so instead of interpolating.

THE JOINT ACTION IS PARTLY LATENT, and the spec should not pretend otherwise.
The archive records events, not decisions. What each side DID in a quarter is
read off the quad classes of the events it initiated — an actor whose events
were material_conflict escalated, one whose events were cooperative
de-escalated — which is an observable proxy for the action, not the action
itself. The gap is real and is exactly what the private-type layer above is
for: two sides can play the same observable action for different reasons, and
the belief state is where that ambiguity lives.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from core.games import state as state_module
from core.graph import kuzu_store

#: Observations a (x, a₁, a₂) cell needs before its own counts are trusted.
#: Below it the cell falls back to the pooled kernel and is FLAGGED — a
#: transition estimated from two observations is not a probability, it is an
#: anecdote with three decimal places.
MIN_CELL_OBSERVATIONS = 12

#: Laplace smoothing. Small: enough that no reachable transition has exactly
#: zero probability (a zero would make the solver treat a rare path as
#: impossible), not so much that it flattens a well-observed cell.
ALPHA = 0.5


def action_from_quads(quad_counts: dict[str, int]) -> str:
    """The action a side's coded events imply for a quarter.

    Material conflict outranks everything: a quarter holding one shooting
    incident and forty statements is an escalation, and a majority vote over
    event counts would call it cooperation because talk is cheap and frequent.
    """
    if quad_counts.get("material_conflict"):
        return "escalate"
    if quad_counts.get("verbal_conflict"):
        return "hold"
    if quad_counts.get("material_cooperation") or quad_counts.get("verbal_cooperation"):
        return "de-escalate"
    return "hold"


def event_rows(conn: Any) -> list[dict[str, Any]]:
    """Dyad-coded events WITH their initiator and the dyad's two sides.

    Separate from `panel.dyad_event_rows` because it asks a different
    question. The panel aggregates a dyad-quarter into one intensity; a game
    needs to know which SIDE did what, so the initiator and the dyad's actor
    pair have to come back too.
    """
    return kuzu_store.query(
        conn,
        "MATCH (e:Event)-[:OF_DYAD]->(d:Dyad) "
        "MATCH (e)-[:INITIATED_BY]->(a:Actor) "
        "RETURN d.node_id AS dyad_id, d.actor_a_id AS actor_a, "
        "d.actor_b_id AS actor_b, a.node_id AS initiator, "
        "e.event_time AS event_time, e.quad_class AS quad_class, "
        "e.region_pack AS region_pack "
        "ORDER BY e.event_time, e.node_id",
    )


def joint_actions(
    rows: list[dict[str, Any]], *, quarter_of: Any
) -> dict[tuple[str, int], tuple[str, str]]:
    """(dyad, quarter) → the joint action each side's coded events imply.

    THE PROXY IS NAMED HERE AND NOWHERE ELSE. The archive records events, not
    decisions: what a side "did" in a quarter is read off the quad classes of
    the events it INITIATED. That is an observable stand-in for an action, not
    the action, and the gap is exactly what the private-type layer is for —
    two sides can play the same observable move for different reasons, and the
    belief state is where that ambiguity lives.

    A side that initiated nothing that quarter is recorded as holding. Absence
    of initiative is not the same as restraint, but it is the only reading the
    record supports, and inventing a de-escalation from silence would put a
    decision in the archive that nobody observed.
    """
    per_side: dict[tuple[str, int], dict[str, dict[str, int]]] = defaultdict(
        lambda: {"a": defaultdict(int), "b": defaultdict(int)}
    )
    for row in rows:
        quad = row.get("quad_class")
        if not quad:
            continue
        key = (str(row["dyad_id"]), quarter_of(str(row["event_time"])))
        side = "a" if row["initiator"] == row["actor_a"] else "b"
        per_side[key][side][str(quad)] += 1

    return {
        key: (action_from_quads(sides["a"]), action_from_quads(sides["b"]))
        for key, sides in per_side.items()
    }


def count(
    panel_rows: list[dict[str, Any]],
    actions_by_cell: dict[tuple[str, int], tuple[str, str]],
) -> dict[tuple[int, int, int], np.ndarray]:
    """Raw transition counts, keyed (intensity band, action A, action B).

    `actions_by_cell` maps (dyad, quarter) → the joint action read off that
    quarter's events. Kept as a parameter rather than derived here so the
    caller owns the proxy in one place and it can be swapped without touching
    the counting.
    """
    by_dyad: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in panel_rows:
        by_dyad[row["dyad_id"]].append(row)

    bands = len(state_module.INTENSITY_EDGES)
    counts: dict[tuple[int, int, int], np.ndarray] = {}
    for dyad, rows in by_dyad.items():
        rows = sorted(rows, key=lambda r: r["q"])
        levels = state_module.classify(rows)
        for i in range(len(rows) - 1):
            joint = actions_by_cell.get((dyad, rows[i]["q"]))
            if joint is None:
                continue
            a1 = state_module.ACTIONS.index(joint[0])
            a2 = state_module.ACTIONS.index(joint[1])
            key = (levels[i], a1, a2)
            if key not in counts:
                counts[key] = np.zeros(bands)
            counts[key][levels[i + 1]] += 1.0
    return counts


def kernel(
    counts: dict[tuple[int, int, int], np.ndarray],
    *,
    min_observations: int = MIN_CELL_OBSERVATIONS,
    alpha: float = ALPHA,
) -> tuple[np.ndarray, np.ndarray]:
    """(kernel, observed) — probabilities and the count behind each cell.

    Shape is (intensity, action A, action B, next intensity). Cells under the
    floor take the POOLED distribution for their intensity band, so the
    solver always has a proper distribution to work with and the caller can
    still see, from `observed`, which cells were never really measured.
    """
    bands = len(state_module.INTENSITY_EDGES)
    actions = len(state_module.ACTIONS)
    kern = np.zeros((bands, actions, actions, bands))
    observed = np.zeros((bands, actions, actions))

    # Pooled over joint actions, per origin band — the fallback.
    pooled = np.zeros((bands, bands))
    for (x, _a1, _a2), row in counts.items():
        pooled[x] += row
    for x in range(bands):
        total = pooled[x].sum()
        pooled[x] = (
            (pooled[x] + alpha) / (total + alpha * bands) if total
            # A band the archive never left has no evidence at all; a uniform
            # row says exactly that rather than inventing a direction.
            else np.full(bands, 1.0 / bands)
        )

    for x in range(bands):
        for a1 in range(actions):
            for a2 in range(actions):
                cell: np.ndarray | None = counts.get((x, a1, a2))
                total = float(cell.sum()) if cell is not None else 0.0
                observed[x, a1, a2] = total
                if cell is None or total < min_observations:
                    kern[x, a1, a2] = pooled[x]
                else:
                    kern[x, a1, a2] = (cell + alpha) / (total + alpha * bands)
    return kern, observed


def coverage(
    observed: np.ndarray, *, min_observations: int = MIN_CELL_OBSERVATIONS
) -> dict[str, Any]:
    """How much of the kernel is real. Travels with any forecast built on it —
    a game solved over a kernel that is four-fifths fallback is a game about
    the fallback."""
    total = int(observed.size)
    measured = int((observed >= min_observations).sum())
    return {
        "cells": total,
        "measured": measured,
        "fallback": total - measured,
        "share_measured": round(measured / total, 4) if total else 0.0,
        "observations": int(observed.sum()),
        "min_cell_observations": min_observations,
    }
