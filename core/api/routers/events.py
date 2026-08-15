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
from core.wire import serving as wire_serving

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


def _conn_or_none(request: Request) -> Any | None:
    """The graph if it is open. The event surfaces read the UNION of the graph
    and the wire corpus, and the corpus half serves during the boot window —
    a dark graph degrades these endpoints, it does not 503 them (unless the
    corpus is absent too)."""
    return request.app.state.graph


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
    # THE UNION READ (the 55-event trap, applied to the explorer): the graph
    # holds the spine, the deep tier and whatever wire years a loading boot
    # merged before the corpus move — which is why the explorer went quiet
    # after ~2022 while the corpus ran to the present. The wire lives in the
    # corpus; this surface reads BOTH, deduped by event id, graph row winning
    # (it may carry an OF_DYAD link the corpus cannot).
    conn = _conn_or_none(request)
    if conn is None and not wire_serving.available():
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    graph_rows: list[dict[str, Any]] = []
    graph_truncated = False
    if conn is not None:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit + 1}
        # NOT $start/$end: `end` is a RESERVED WORD in Kuzu and the trap
        # extends to PARAMETER names, not just properties — `$end` is a parser
        # error, which is why Regime carries start_date/end_date.
        if start:
            clauses.append("e.event_time >= $start_date")
            params["start_date"] = start
        if end:
            clauses.append("e.event_time <= $end_date")
            params["end_date"] = end
        if pack:
            # THE LENS RULE: a region shows its own tagged events PLUS the
            # global backbone — deep-tier records (COW) carry no pack because
            # a 1911 great-power dispute belongs to every lens that includes
            # its actors.
            clauses.append(
                "(e.region_pack = $pack OR e.region_pack = '' OR e.region_pack IS NULL)"
            )
            params["pack"] = pack
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        # Actor and dyad ids ride along on every row so the explorer can draw
        # the network for a time window from one request. OPTIONAL MATCH: an
        # event whose actors failed to code still lists, with nulls, rather
        # than disappearing from the archive. Events are dyadic (CAMEO), so
        # the optional matches cannot fan a row out.
        graph_rows = kuzu_store.query(
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
        graph_truncated = len(graph_rows) > limit
        graph_rows = graph_rows[:limit]

    wire_rows, wire_truncated = wire_serving.events_window(pack, start, end, limit)

    seen = {row["node_id"] for row in graph_rows}
    merged = graph_rows + [row for row in wire_rows if row["node_id"] not in seen]
    merged.sort(key=lambda row: (row["event_time"] or "", row["node_id"]))
    truncated = graph_truncated or wire_truncated or len(merged) > limit
    return {"rows": merged[:limit], "truncated": truncated}


@router.get("/events/coverage")
def coverage(request: Request, pack: str | None = None) -> dict[str, Any]:
    """Events per YEAR across the whole archive — the slider's coverage strip.

    The archive is now thousands of events, so the explorer fetches windows
    rather than everything; this aggregate is what still lets the slider show
    where the record is dense and where nothing has been ingested — absence
    of a bar meaning "not ingested", never "history was quiet".
    """
    conn = _conn_or_none(request)
    if conn is None and not wire_serving.available():
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    years: dict[str, int] = {}
    if conn is not None:
        # THE GRAPH'S NON-WIRE HALF ONLY: any wire event a loading boot merged
        # came from the same shipped artifacts the corpus parses, so counting
        # graph gdelt events AND corpus events would count that overlap twice.
        # The corpus is the wire's authority; the graph contributes the spine
        # and the deep tier.
        wire_backed = wire_serving.available()
        clauses = ["NOT e.node_id STARTS WITH 'event:gdelt-'"] if wire_backed else []
        params: dict[str, Any] = {}
        if pack:
            clauses.append(
                "(e.region_pack = $pack OR e.region_pack = '' OR e.region_pack IS NULL)"
            )
            params["pack"] = pack
        where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
        # Aggregate in Cypher, not in Python: the archive is hundreds of
        # thousands of events, and this strip is fetched on load and on every
        # region change. substring(event_time,1,4) is the year; ~120 rows come
        # back instead of the whole Event table streamed one dict per event.
        rows = kuzu_store.query(
            conn,
            f"MATCH (e:Event) {where}RETURN substring(e.event_time, 1, 4) AS year, "
            "count(e) AS n",
            params,
        )
        years = {str(row["year"]): int(row["n"]) for row in rows}
    wire_years = wire_serving.coverage(pack)
    for year, count in (wire_years or {}).items():
        years[year] = years.get(year, 0) + count
    return {"years": years, "total": sum(years.values())}


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


def _wire_event_detail(node_id: str) -> dict[str, Any] | None:
    """The detail payload for a corpus-only wire event, shaped like the graph
    one. Actor names resolve through the pack roster; provenance is the
    source_id the row carries (the corpus's own FK)."""
    row = wire_serving.event(node_id)
    if row is None:
        return None
    from core import packs

    names: dict[str, str] = {}
    try:
        pack = packs.load(row["region_pack"]) if row.get("region_pack") else None
        if pack is not None:
            names = {a["id"]: a["name"] for a in pack.actors}
    except (KeyError, FileNotFoundError):
        pass

    def actor(actor_id: str | None) -> dict[str, Any] | None:
        if not actor_id:
            return None
        return {"node_id": actor_id, "name": names.get(actor_id, actor_id)}

    detail = {
        field: row.get(field)
        for field in (
            "node_id", "name", "event_time", "cameo_code", "quad_class", "goldstein",
            "escalation_direction", "escalation_magnitude", "escalation_baseline",
            "fidelity_tier", "temporal_resolution", "source_scale", "region_pack",
        )
    }
    dyad_id = row.get("dyad_id")
    return {
        **detail,
        "initiator": actor(row.get("initiator_id")),
        "target": actor(row.get("target_id")),
        "dyad": {"node_id": dyad_id} if dyad_id else None,
        "regimes": [],
        "sources": (
            [{"node_id": row["source_id"]}] if row.get("source_id") else []
        ),
    }


@router.get("/events/{node_id:path}")
def get_event(request: Request, node_id: str) -> dict[str, Any]:
    """One event with everything the graph knows about it — falling back to
    the wire corpus for events the graph never merged (the wire lives in the
    corpus; the graph keeps what is graph-shaped)."""
    conn = _conn_or_none(request)
    found: list[dict[str, Any]] = []
    if conn is not None:
        found = kuzu_store.query(
            conn,
            f"MATCH (e:Event {{node_id: $id}}) RETURN {_EVENT_COLUMNS}",
            {"id": node_id},
        )
    if not found:
        from_wire = _wire_event_detail(node_id)
        if from_wire is not None:
            return from_wire
        if conn is None:
            raise HTTPException(
                status_code=503,
                detail=request.app.state.graph_error or "graph unavailable",
            )
        raise HTTPException(status_code=404, detail=f"no such event: {node_id}")

    # `found` is non-empty here, which only the graph branch can produce, so
    # the graph is open. (Narrows the Optional for the relationship queries.)
    assert conn is not None

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
