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
    if updates:
        kuzu_store.merge_nodes(conn, "Event", updates)
        kuzu_store.merge_edges(conn, "OF_DYAD", of_dyad)
    return {"events_rescored": len(updates), "dyads": len(coding.dyads)}


# ── the same fold, one dyad at a time (2026-08-16) ──────────────────────────


def _actor_endpoints(conn: Any, event_ids: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """event → its initiator and target. The provenance invariant guarantees
    both edges exist, which is why the dyad can always be reconstructed."""
    return {
        row["node_id"]: row
        for row in kuzu_store.query(
            conn,
            "MATCH (e:Event) "
            "OPTIONAL MATCH (e)-[:INITIATED_BY]->(i:Actor) "
            "OPTIONAL MATCH (e)-[:DIRECTED_AT]->(t:Actor) "
            "RETURN e.node_id AS node_id, i.node_id AS initiator, t.node_id AS target",
        )
        if event_ids is None or row["node_id"] in set(event_ids)
    }


def unscored_dyads(conn: Any, *, limit: int = 40) -> list[str]:
    """Dyads holding at least one event Head B has never scored.

    The wire loader writes events without escalation fields — the fold is a
    separate pass — so after a background load the graph holds events whose
    direction, magnitude and baseline are empty. Everything reading escalation
    off the GRAPH (dyad timelines, structural pressure, the graph half of the
    forecast union) sees nulls until this runs.
    """
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event) "
        "WHERE e.escalation_direction IS NULL OR e.escalation_direction = '' "
        "OPTIONAL MATCH (e)-[:INITIATED_BY]->(i:Actor) "
        "OPTIONAL MATCH (e)-[:DIRECTED_AT]->(t:Actor) "
        "RETURN DISTINCT i.node_id AS initiator, t.node_id AS target",
    )
    seen: list[str] = []
    for row in rows:
        actor_a = row["initiator"] or row["target"]
        if not actor_a:
            continue
        did = escalation.dyad_id(str(actor_a), str(row["target"] or actor_a))
        if did not in seen:
            seen.append(did)
        if len(seen) >= limit:
            break
    return seen


def rescore_dyad(conn: Any, dyad_id: str) -> dict[str, int]:
    """Head B over ONE dyad's whole history — equivalent to the archive pass.

    THE ARCHIVE-WIDE RULE IS ABOUT COMPLETENESS WITHIN A PAIR, NOT ABOUT DOING
    EVERY PAIR AT ONCE. `escalation.code_events` folds through a `DyadTracker`
    keyed by dyad, so a pair's baseline depends only on that pair's own earlier
    events: feeding one dyad's COMPLETE history in time order produces exactly
    what the whole-archive pass produces for it. What would be wrong by
    construction is a partial fold WITHIN a dyad — scoring its modern events
    without its deep-tier past — which is why this always loads the pair's
    entire record rather than the unscored tail.

    That equivalence is what makes the rescore a background job instead of an
    hours-long, un-resumable, API-stopping batch.
    """
    from core.games import opening as opening_module

    actor_a, actor_b = opening_module.dyad_actors(dyad_id)
    names = {
        row["node_id"]: row["name"]
        for row in kuzu_store.query(
            conn,
            "MATCH (a:Actor) WHERE a.node_id = $a OR a.node_id = $b "
            "RETURN a.node_id AS node_id, a.name AS name",
            {"a": actor_a, "b": actor_b},
        )
    }
    columns = ", ".join(f"e.{p} AS {p}" for p in _EVENT_PROPS)
    rows = kuzu_store.query(
        conn,
        f"MATCH (e:Event)-[:INITIATED_BY]->(i:Actor) "
        f"MATCH (e)-[:DIRECTED_AT]->(t:Actor) "
        f"WHERE (i.node_id = $a AND t.node_id = $b) "
        f"   OR (i.node_id = $b AND t.node_id = $a) "
        f"RETURN e.node_id AS node_id, {columns}, "
        f"i.node_id AS initiator, t.node_id AS target "
        f"ORDER BY e.event_time, e.node_id",
        {"a": actor_a, "b": actor_b},
    )
    stream = [
        {
            "node_id": row["node_id"], "event_time": row["event_time"],
            "goldstein": row["goldstein"],
            "actor_a": row["initiator"] or row["target"],
            "actor_b": row["target"] or row["initiator"],
        }
        for row in rows if row["goldstein"] is not None and (row["initiator"] or row["target"])
    ]
    if not stream:
        return {"events_rescored": 0, "dyads": 0}

    by_id = {row["node_id"]: row for row in rows}
    coding = escalation.code_events(stream, names=names)
    updates: list[dict[str, Any]] = []
    of_dyad: list[dict[str, Any]] = []
    for coded in coding.events:
        # WIRE EVENTS KEEP THE CORPUS'S CODING. Since the lean graph (2026-08-16)
        # holds only the wire's MEASURABLE events, a fold over the graph's
        # record is a fold over a filtered record — the baseline it would
        # write is not the pair's. Those events arrive coded by the corpus
        # (`work.wire`), which folded the COMPLETE record; the deep tier's
        # events, which live only in the graph, are what this writes.
        if str(coded["node_id"]).startswith("event:gdelt-"):
            continue
        base = {k: v for k, v in by_id[coded["node_id"]].items()
                if k in _EVENT_PROPS or k == "node_id"}
        for key in ("name", "region_pack", "fidelity_tier",
                    "temporal_resolution", "source_scale", "quad_class"):
            base[key] = base.get(key) or ""
        base.update({k: v for k, v in coded.items() if k.startswith("escalation_")})
        updates.append(base)
        of_dyad.append({"src": coded["node_id"], "dst": coded["dyad_id"]})

    kuzu_store.merge_nodes(conn, "Dyad", coding.dyads)
    if updates:
        kuzu_store.merge_nodes(conn, "Event", updates)
        kuzu_store.merge_edges(conn, "OF_DYAD", of_dyad)
    return {"events_rescored": len(updates), "dyads": len(coding.dyads)}
