"""The MCP tool implementations — build-spec section 14.

THE AGENT SURFACE IS A DIFFERENT DESIGN TARGET FROM THE UI (MarketGraph
lesson, kept): tools return compact ROWS, capped and truncation-flagged,
never raw {nodes, edges} payloads — measured on MarketGraph, those cost
14K–20K tokens per call. Every result that reads the graph carries the
coverage caveat so an agent reports "the graph holds no X" narrowly and
truthfully.

Implemented now: find_actor, regime_at, and the graph-backed shells. The rest
land with their layers and raise a phase-naming error until then — an agent
told "Phase 2" reports that; an agent given an empty result invents.
"""

from __future__ import annotations

from typing import Any

import kuzu

from core.graph import kuzu_store
from core.reasoning import regimes as regime_module

MAX_ROWS = 50

#: Attached to every graph-backed result. An agent that does not know the
#: archive currently holds one region pack and no deep tier will answer "nothing
#: happened in 1956" when the truthful answer is "this archive does not cover
#: 1956 yet" — a different claim, and the difference matters.
_COVERAGE = (
    "This archive currently holds curated marquee spines for the installed "
    "region packs, not a complete event record. Absence of an event here is not "
    "evidence it did not happen. Deep-tier history (1905-1979) lands in Phase 3."
)


def find_actor(conn: kuzu.Connection, name: str) -> dict[str, Any]:
    """Case-insensitive substring match over Actor names."""
    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor) WHERE lower(a.name) CONTAINS lower($name) "
        "RETURN a.node_id AS node_id, a.name AS name, a.actor_type AS actor_type, "
        "a.state_from AS state_from, a.state_to AS state_to "
        "LIMIT $limit",
        {"name": name, "limit": MAX_ROWS + 1},
    )
    return {"rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS}


def neighbors(conn: kuzu.Connection, node_id: str) -> dict[str, Any]:
    """One hop out along TRAVERSABLE edges only — classification edges
    (OCCURRED_IN, DERIVED_FROM) would make everything two hops from
    everything. Per-rel queries because Kuzu rejects MATCH (n:A|B)."""
    from core.ontology import kuzu_schema as ontology

    rows: list[dict[str, Any]] = []
    for rel in ontology.traversable_edges():
        spec = ontology.edges()[rel]
        for direction, pattern in (
            ("out", f"(a:{spec.src} {{node_id: $id}})-[r:{rel}]->(b:{spec.dst})"),
            ("in", f"(b:{spec.src})-[r:{rel}]->(a:{spec.dst} {{node_id: $id}})"),
        ):
            rows += [
                {**row, "rel": rel, "direction": direction}
                for row in kuzu_store.query(
                    conn,
                    f"MATCH {pattern} RETURN b.node_id AS node_id, b.name AS name LIMIT $limit",
                    {"id": node_id, "limit": MAX_ROWS},
                )
            ]
    return {"rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS}


def regime_at(date: str) -> dict[str, Any]:
    """Which monetary order and polarity epoch a date sits in."""
    return regime_module.regimes_at(date)


def _not_yet(tool: str, phase: str) -> dict[str, Any]:
    return {
        "status": "not_implemented",
        "tool": tool,
        "phase": phase,
        "note": "Report this honestly: the layer is not built, the data is not absent.",
    }


def events_between(conn: kuzu.Connection, actor_a: str, actor_b: str,
                   start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Events on the dyad these two actors form, in time order.

    Matched through the DYAD rather than by walking INITIATED_BY and
    DIRECTED_AT in both directions, because the dyad is already the unordered
    pair: asking for events "between" two actors means the relationship, not
    one side's actions against the other.
    """
    from core.classifier import escalation

    params: dict[str, Any] = {"id": escalation.dyad_id(actor_a, actor_b), "limit": MAX_ROWS + 1}
    clauses = ["true"]
    # `end` is reserved in Kuzu and the trap covers PARAMETER names too, so a
    # bare `$end` is a parser error rather than a binding.
    if start:
        clauses.append("e.event_time >= $start_date")
        params["start_date"] = start
    if end:
        clauses.append("e.event_time <= $end_date")
        params["end_date"] = end
    rows = kuzu_store.query(
        conn,
        f"MATCH (e:Event)-[:OF_DYAD]->(d:Dyad {{node_id: $id}}) "
        f"WHERE {' AND '.join(clauses)} "
        "RETURN e.node_id AS node_id, e.name AS name, e.event_time AS event_time, "
        "e.action_cameo_code AS cameo_code, e.goldstein AS goldstein, "
        "e.escalation_direction AS escalation_direction, "
        "e.escalation_magnitude AS escalation_magnitude "
        "ORDER BY e.event_time, e.node_id LIMIT $limit",
        params,
    )
    return {
        "dyad": params["id"],
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        "coverage": _COVERAGE,
    }


def escalation_trajectory(conn: kuzu.Connection, dyad_id: str) -> dict[str, Any]:
    """One dyad's escalation history against its own moving baseline.

    The baseline column is the point: escalation here is RELATIONAL, so the
    same score means different things in different dyads, and a reader without
    the baseline cannot tell a routine act from a rupture.
    """
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event)-[:OF_DYAD]->(d:Dyad {node_id: $id}) "
        "RETURN e.event_time AS event_time, e.name AS name, "
        "e.goldstein AS goldstein, e.escalation_baseline AS baseline, "
        "e.escalation_direction AS direction, "
        "e.escalation_magnitude AS magnitude "
        "ORDER BY e.event_time, e.node_id LIMIT $limit",
        {"id": dyad_id, "limit": MAX_ROWS + 1},
    )
    return {
        "dyad": dyad_id,
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        "coverage": _COVERAGE,
    }


def network_metrics(conn: kuzu.Connection, window_start: str, window_end: str) -> dict[str, Any]:
    return _not_yet("network_metrics", "Phase 2")


def event_effects(conn: kuzu.Connection, event_id: str) -> dict[str, Any]:
    """Measured effects for one event: abnormal return, significance, flags.

    Only MEASURED effects appear. A market the engine skipped is absent here
    and recorded in the panel's event_study_runs, so an empty result means
    "nothing measured", never "no effect" — which is why `measured` is
    reported rather than left to be inferred from the row count.
    """
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id})-[a:AFFECTED]->(m:Market) "
        "RETURN m.ticker AS ticker, a.window AS window, a.resolution AS resolution, "
        "a.abnormal_return AS abnormal_return, a.t_stat AS t_stat, "
        "a.p_value AS p_value, a.first_mover AS first_mover, "
        "a.overlapping AS overlapping, a.method AS method "
        "ORDER BY ticker, window LIMIT $limit",
        {"id": event_id, "limit": MAX_ROWS + 1},
    )
    return {
        "event": event_id,
        "measured": len(rows[:MAX_ROWS]),
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        "coverage": _COVERAGE,
    }


def analogues_for(query_ref: str) -> dict[str, Any]:
    return _not_yet("analogues_for", "Phase 5")


def forecast(question: str, mode: str = "near_term") -> dict[str, Any]:
    return _not_yet("forecast", "Phase 5")
