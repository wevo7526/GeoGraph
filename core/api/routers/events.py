"""Events, escalation and measured effects — the Phase 0 read surface.

Every response here is a projection of what the deterministic core computed:
the spine as coded by Head B, and the AFFECTED edges written by the
transmission engine. Nothing is derived at request time, so two callers asking
the same question get the same numbers.

Unbuilt layers still 501 with their phase named. A wrong empty answer teaches a
caller the archive is empty; a named phase teaches them what is coming.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from core.graph import kuzu_store

router = APIRouter(tags=["events"])

#: A page of events. The explorer asks for the spine, which is ~20 rows; the
#: cap exists so a GDELT-scale graph cannot return itself.
MAX_ROWS = 500


def _conn(request: Request) -> Any:
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    return conn


_EVENT_COLUMNS = (
    "e.node_id AS node_id, e.name AS name, e.event_time AS event_time, "
    "e.action_cameo_code AS cameo_code, e.quad_class AS quad_class, "
    "e.goldstein AS goldstein, e.escalation_direction AS escalation_direction, "
    "e.escalation_magnitude AS escalation_magnitude, "
    "e.escalation_baseline AS escalation_baseline, "
    "e.fidelity_tier AS fidelity_tier, e.temporal_resolution AS temporal_resolution, "
    "e.source_scale AS source_scale, e.region_pack AS region_pack"
)


@router.get("/events")
def list_events(
    request: Request,
    start: str | None = Query(None, description="ISO date, inclusive"),
    end: str | None = Query(None, description="ISO date, inclusive"),
    pack: str | None = None,
    limit: int = Query(200, ge=1, le=MAX_ROWS),
) -> dict[str, Any]:
    """The spine in time order.

    Dates are compared as STRINGS on purpose: every date in the archive is
    ISO-8601, which sorts lexically, so a range filter is correct whether the
    event is known to the day or only to the year.
    """
    conn = _conn(request)
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit + 1}
    # NOT $start/$end: `end` is a RESERVED WORD in Kuzu and the trap extends to
    # PARAMETER names, not just properties — `$end` is a parser error, which is
    # why Regime carries start_date/end_date rather than start/end.
    if start:
        clauses.append("e.event_time >= $start_date")
        params["start_date"] = start
    if end:
        clauses.append("e.event_time <= $end_date")
        params["end_date"] = end
    if pack:
        clauses.append("e.region_pack = $pack")
        params["pack"] = pack
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    # Actor and dyad ids ride along on every row so the explorer can draw the
    # network for a time window from one request. OPTIONAL MATCH: an event
    # whose actors failed to code still lists, with nulls, rather than
    # disappearing from the archive. Events are dyadic (CAMEO), so the
    # optional matches cannot fan a row out.
    rows = kuzu_store.query(
        conn,
        f"MATCH (e:Event) {where}"
        "OPTIONAL MATCH (e)-[:INITIATED_BY]->(ia:Actor) "
        "OPTIONAL MATCH (e)-[:DIRECTED_AT]->(ta:Actor) "
        "OPTIONAL MATCH (e)-[:OF_DYAD]->(d:Dyad) "
        f"RETURN {_EVENT_COLUMNS}, ia.node_id AS initiator_id, "
        "ta.node_id AS target_id, d.node_id AS dyad_id "
        "ORDER BY e.event_time, e.node_id LIMIT $limit",
        params,
    )
    return {"rows": rows[:limit], "truncated": len(rows) > limit}


@router.get("/events/coverage")
def coverage(request: Request, pack: str | None = None) -> dict[str, Any]:
    """Events per YEAR across the whole archive — the slider's coverage strip.

    The archive is now thousands of events, so the explorer fetches windows
    rather than everything; this aggregate is what still lets the slider show
    where the record is dense and where nothing has been ingested — absence
    of a bar meaning "not ingested", never "history was quiet".
    """
    conn = _conn(request)
    where = "WHERE e.region_pack = $pack " if pack else ""
    params: dict[str, Any] = {"pack": pack} if pack else {}
    rows = kuzu_store.query(
        conn,
        f"MATCH (e:Event) {where}RETURN e.event_time AS event_time",
        params,
    )
    years: dict[str, int] = {}
    for row in rows:
        year = str(row["event_time"])[:4]
        years[year] = years.get(year, 0) + 1
    return {"years": years, "total": len(rows)}


@router.get("/events/{node_id:path}/effects")
def event_effects(request: Request, node_id: str) -> dict[str, Any]:
    """What this event was measured to have done, per market and window.

    `skipped` markets are NOT in this list — a skip lives in the panel's
    event_study_runs, because it is a fact about coverage rather than about
    the event. An empty list here means the engine has not run for this event,
    which is why `measured` is reported alongside.
    """
    conn = _conn(request)
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id})-[a:AFFECTED]->(m:Market) "
        "RETURN m.node_id AS market_node_id, m.name AS market, m.ticker AS ticker, "
        "m.market_type AS market_type, a.window AS window, a.resolution AS resolution, "
        "a.raw_return AS raw_return, a.expected_return AS expected_return, "
        "a.abnormal_return AS abnormal_return, a.t_stat AS t_stat, "
        "a.p_value AS p_value, a.first_mover AS first_mover, "
        "a.overlapping AS overlapping, a.method AS method, a.source_id AS source_id "
        "ORDER BY ticker, window",
        {"id": node_id},
    )
    return {"event": node_id, "measured": len(rows), "rows": rows}


@router.get("/events/{node_id:path}")
def get_event(request: Request, node_id: str) -> dict[str, Any]:
    """One event with everything the graph knows about it."""
    conn = _conn(request)
    found = kuzu_store.query(
        conn,
        f"MATCH (e:Event {{node_id: $id}}) RETURN {_EVENT_COLUMNS}",
        {"id": node_id},
    )
    if not found:
        raise HTTPException(status_code=404, detail=f"no such event: {node_id}")

    # One query per relationship: Kuzu rejects MATCH (n:A|B), and a UNION
    # returning whole nodes breaks because the NODE type differs per table.
    initiator = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id})-[:INITIATED_BY]->(a:Actor) "
        "RETURN a.node_id AS node_id, a.name AS name",
        {"id": node_id},
    )
    target = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id})-[:DIRECTED_AT]->(a:Actor) "
        "RETURN a.node_id AS node_id, a.name AS name",
        {"id": node_id},
    )
    dyad = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id})-[:OF_DYAD]->(d:Dyad) "
        "RETURN d.node_id AS node_id, d.name AS name, "
        "d.ewma_baseline AS ewma_baseline, d.ewma_as_of AS ewma_as_of",
        {"id": node_id},
    )
    regimes = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id})-[:OCCURRED_IN]->(r:Regime) "
        "RETURN r.node_id AS node_id, r.name AS name, r.kind AS kind",
        {"id": node_id},
    )
    sources = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id})-[:DERIVED_FROM]->(s:Source) "
        "RETURN s.node_id AS node_id, s.name AS name, s.url AS url, "
        "s.citation AS citation",
        {"id": node_id},
    )
    return {
        **found[0],
        "initiator": initiator[0] if initiator else None,
        "target": target[0] if target else None,
        "dyad": dyad[0] if dyad else None,
        "regimes": regimes,
        "sources": sources,
    }


@router.get("/escalation/{dyad_id:path}")
def escalation_trajectory(request: Request, dyad_id: str) -> dict[str, Any]:
    """One dyad's escalation history, in time order.

    This is the shape that makes relational escalation legible: the same
    Goldstein score reads as routine in one dyad and as a rupture in another,
    and the baseline column is what shows why.
    """
    conn = _conn(request)
    dyad = kuzu_store.query(
        conn,
        "MATCH (d:Dyad {node_id: $id}) RETURN d.node_id AS node_id, d.name AS name, "
        "d.actor_a_id AS actor_a_id, d.actor_b_id AS actor_b_id, "
        "d.ewma_baseline AS ewma_baseline, d.ewma_as_of AS ewma_as_of",
        {"id": dyad_id},
    )
    if not dyad:
        raise HTTPException(status_code=404, detail=f"no such dyad: {dyad_id}")
    events = kuzu_store.query(
        conn,
        "MATCH (e:Event)-[:OF_DYAD]->(d:Dyad {node_id: $id}) "
        "RETURN e.node_id AS node_id, e.name AS name, e.event_time AS event_time, "
        "e.goldstein AS goldstein, e.escalation_baseline AS escalation_baseline, "
        "e.escalation_direction AS escalation_direction, "
        "e.escalation_magnitude AS escalation_magnitude "
        "ORDER BY e.event_time, e.node_id",
        {"id": dyad_id},
    )
    return {**dyad[0], "events": events}


@router.get("/dyads")
def list_dyads(request: Request) -> dict[str, Any]:
    """Every dyad with escalation state, most conflictual baseline first."""
    conn = _conn(request)
    rows = kuzu_store.query(
        conn,
        "MATCH (d:Dyad) RETURN d.node_id AS node_id, d.name AS name, "
        "d.actor_a_id AS actor_a_id, d.actor_b_id AS actor_b_id, "
        "d.ewma_baseline AS ewma_baseline, d.ewma_as_of AS ewma_as_of "
        "ORDER BY d.ewma_baseline, d.node_id",
    )
    return {"rows": rows}
