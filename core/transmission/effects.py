"""Where measured effects become graph structure.

The one-way door between the numeric store and the graph: EffectResults
computed by event_study.py land as AFFECTED edges through the validated write
path. Nothing else writes AFFECTED — that is what keeps every number on the
money edge deterministic and reproducible.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from typing import Any

import kuzu

from core.graph import kuzu_store
from core.transmission.event_study import EffectResult


def _finite(value: float | None) -> float | None:
    """NaN is not a measurement (a zero-variance estimation window yields
    t_stat = nan by construction) — it becomes None before the merge, the same
    rule pg_store applies, so the two stores agree and no JSON boundary 500s."""
    if value is None or not math.isfinite(value):
        return None
    return value


def write_effects(
    conn: kuzu.Connection,
    results: list[EffectResult],
    *,
    market_node_ids: dict[str, str],
    source_id: str,
) -> int:
    """Persist results as AFFECTED edges.

    `market_node_ids` maps ticker → Market node_id; `source_id` names the
    Source of the PRICE SERIES the effects were computed from (the provenance
    requirement on AFFECTED is about the data the number came from — the
    event's own provenance lives on DERIVED_FROM).
    """
    rows = [
        {
            "src": result.event_node_id,
            "dst": market_node_ids[result.market_ticker],
            "window": result.window,
            "resolution": result.resolution,
            "raw_return": _finite(result.raw_return),
            "expected_return": _finite(result.expected_return),
            "abnormal_return": _finite(result.abnormal_return),
            "t_stat": _finite(result.t_stat),
            "p_value": _finite(result.p_value),
            "first_mover": result.first_mover,
            "overlapping": result.overlapping,
            "method": result.method,
            "source_id": source_id,
        }
        for result in results
    ]
    return kuzu_store.merge_edges(conn, "AFFECTED", rows)


# ── reading effects back ─────────────────────────────────────────────────────
#
# `write_effects` remains the ONLY writer of AFFECTED. These are the READ side,
# extracted from core/api/routers/precedent.py so every consumer that needs a
# dyad's measured effects (precedent, and the Event Impact layer) goes through
# one tested helper instead of re-implementing the actor-edge reconstruction.


def dyad_actors(dyad_id: str) -> tuple[str, str]:
    """`dyad:cow-630--cow-666` → its two actor node ids, sorted — the exact
    inverse of `escalation.dyad_id`, whose '--' separator is safe because no
    actor's bare id contains one."""
    bare = dyad_id.split(":", 1)[-1]
    first, _, second = bare.partition("--")
    return f"actor:{first}", f"actor:{second or first}"


#: One dyad's measured effects, briefly memoised. THE SAME QUERY WAS RUN NINE
#: TIMES PER PAGE: `_expected_for_dyad` re-reads a dyad's whole effect set to
#: compute the base rate for EACH event, so a dynamic case study with eight
#: episodes paid for it eight times over, plus once for the timeline —
#: measured at ~2.4s per episode and 20-36s for the page as the archive passed
#: a million events. The rows are append-only measurements that the study job
#: adds to continuously, so a few seconds of staleness costs a reader nothing
#: and the request that would have scanned nine times now scans once.
_EFFECTS_TTL_SECONDS = 30.0
_EFFECTS_CACHE_SIZE = 8
_effects_cache: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()


def forget_dyad_effects() -> None:
    """Drop the memo — for tests, and for a caller that has just written."""
    _effects_cache.clear()


def effects_for_dyad(conn: kuzu.Connection, dyad_id: str) -> list[dict[str, Any]]:
    """Measured market effects for this dyad's events. Empty is a real and
    common answer — the transmission engine records a SKIP where a market did
    not exist at event time, and those never become an AFFECTED edge.

    MEMBERSHIP COMES FROM THE ACTOR EDGES, NOT OF_DYAD. The transmission
    engine measures the whole graph without consulting OF_DYAD, while the
    rescore that writes OF_DYAD is opt-in and unreachable inside a boot
    window — so production held 278k AFFECTED edges beside the spine's 55
    OF_DYAD edges, and a hard OF_DYAD match served "no measured market
    effects" for nearly every dyad while the measurements sat unreachable.
    Every event carries INITIATED_BY and DIRECTED_AT (that pair is the
    provenance invariant), and `escalation.dyad_id` IS the sorted actor pair,
    so anchoring on the two actors reconstructs membership exactly — the same
    stance as games/pricing.py, where requiring the dyad edge is documented
    as quietly shrinking the sample.
    """
    now = time.monotonic()
    cached = _effects_cache.get(dyad_id)
    if cached is not None and now - cached[0] < _EFFECTS_TTL_SECONDS:
        _effects_cache.move_to_end(dyad_id)
        return cached[1]

    actor_a, actor_b = dyad_actors(dyad_id)
    pattern = (
        "MATCH (x:Actor {node_id: $initiator})<-[:INITIATED_BY]-(e:Event)"
        "-[:DIRECTED_AT]->(y:Actor {node_id: $target}) "
        "MATCH (e)-[a:AFFECTED]->(m:Market) "
        "RETURN e.node_id AS event_id, e.event_time AS event_time, "
        "e.name AS event_name, e.goldstein AS goldstein, "
        "e.escalation_direction AS escalation_direction, "
        "e.escalation_magnitude AS escalation_magnitude, "
        "e.fidelity_tier AS fidelity_tier, e.region_pack AS region_pack, "
        "x.node_id AS initiator_id, y.node_id AS target_id, "
        "m.node_id AS market_id, m.name AS market_name, "
        "a.abnormal_return AS abnormal_return, a.window AS window, "
        "a.first_mover AS first_mover, a.p_value AS p_value, "
        "a.t_stat AS t_stat, a.resolution AS resolution"
    )
    rows = kuzu_store.query(conn, pattern, {"initiator": actor_a, "target": actor_b})
    if actor_a != actor_b:
        rows.extend(
            kuzu_store.query(conn, pattern, {"initiator": actor_b, "target": actor_a})
        )
    _effects_cache[dyad_id] = (now, rows)
    _effects_cache.move_to_end(dyad_id)
    while len(_effects_cache) > _EFFECTS_CACHE_SIZE:
        _effects_cache.popitem(last=False)
    return rows
