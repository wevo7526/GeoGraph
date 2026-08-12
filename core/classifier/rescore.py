"""Head B over the WHOLE archive — the rescore both loaders share.

Escalation folds per dyad across 120 years in time order, so scoring happens
ONCE, after every loader that adds events (COW deep tier, GDELT backfill,
pack seeds): deep-tier baselines change how modern events read, and a
partial rescore would be wrong by construction.
"""

from __future__ import annotations

from typing import Any

from core.classifier import escalation
from core.graph import kuzu_store

#: The Event slots the rescore reads and writes back. Escalation properties
#: ride along and are REPLACED by Head B's result.
_EVENT_PROPS = (
    "name", "event_time", "action_cameo_code", "goldstein", "quad_class",
    "region_pack", "fidelity_tier", "temporal_resolution", "source_scale",
)


def rescore_escalation(conn: Any) -> dict[str, int]:
    """Head B over the whole archive: every event with actors, in time order.

    Deep-tier baselines change how modern spine events read (a US–Iran event
    in 2025 departs from a baseline the 1980s built), so a partial rescore
    would be WRONG by construction — this always folds everything.
    """
    names = {
        row["node_id"]: row["name"]
        for row in kuzu_store.query(
            conn, "MATCH (a:Actor) RETURN a.node_id AS node_id, a.name AS name"
        )
    }
    columns = ", ".join(f"e.{p} AS {p}" for p in _EVENT_PROPS)
    rows = kuzu_store.query(
        conn,
        f"MATCH (e:Event) RETURN e.node_id AS node_id, {columns} "
        "ORDER BY e.event_time, e.node_id",
    )
    by_id = {row["node_id"]: row for row in rows}

    endpoints = {
        row["node_id"]: row
        for row in kuzu_store.query(
            conn,
            "MATCH (e:Event) "
            "OPTIONAL MATCH (e)-[:INITIATED_BY]->(i:Actor) "
            "OPTIONAL MATCH (e)-[:DIRECTED_AT]->(t:Actor) "
            "RETURN e.node_id AS node_id, i.node_id AS initiator, t.node_id AS target",
        )
    }
    stream: list[dict[str, Any]] = []
    for row in rows:
        ends = endpoints.get(row["node_id"], {})
        actor_a = ends.get("initiator") or ends.get("target")
        if not actor_a or row["goldstein"] is None:
            continue
        stream.append({
            "node_id": row["node_id"], "event_time": row["event_time"],
            "goldstein": row["goldstein"],
            "actor_a": actor_a, "actor_b": ends.get("target") or actor_a,
        })

    coding = escalation.code_events(stream, names=names)
    updates: list[dict[str, Any]] = []
    of_dyad: list[dict[str, Any]] = []
    for coded in coding.events:
        base = dict(by_id[coded["node_id"]])
        for key in ("name", "region_pack", "fidelity_tier",
                    "temporal_resolution", "source_scale", "quad_class"):
            base[key] = base.get(key) or ""
        base.update({k: v for k, v in coded.items() if k.startswith("escalation_")})
        updates.append(base)
        of_dyad.append({"src": coded["node_id"], "dst": coded["dyad_id"]})

    kuzu_store.merge_nodes(conn, "Dyad", coding.dyads)
    kuzu_store.merge_nodes(conn, "Event", updates)
    kuzu_store.merge_edges(conn, "OF_DYAD", of_dyad)
    return {"events_rescored": len(updates), "dyads": len(coding.dyads)}
