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

from core import archive as archive_bounds
from core import settings as settings_module
from core.cite import citable_url
from core.graph import kuzu_store
from core.ingestion import gdelt
from core.wire import headline as wire_headline
from core.wire import serving as wire_serving

router = APIRouter(tags=["events"])

#: A page of events. The explorer asks for the spine, which is ~20 rows; the
#: cap exists so a GDELT-scale graph cannot return itself.
MAX_ROWS = 500


def _cited_source(
    node_id: str | None, row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A Source the surface may name, with a href only when it is a document.

    `source:gdelt` always cites the dataset. GDELT SOURCEURL (a mention string,
    sometimes a baseball story) never becomes the href.
    """
    src = str(node_id or "")
    row = row or {}
    if src == gdelt.SOURCE_GDELT:
        name = row.get("name") or "GDELT"
        url = gdelt.SOURCE_GDELT_URL
    else:
        name = row.get("name") or src
        url = row.get("url")
    return {
        "node_id": src,
        "name": name,
        "url": citable_url(url),
        "citation": row.get("citation") or "",
    }


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
    order: str = Query(
        "asc", pattern="^(asc|desc)$",
        description="asc = oldest first; desc = MOST RECENT first (what a "
        "dense window wants — the wire holds ~15-20k events/year, so the "
        "oldest 500 of a five-year window are all one early month).",
    ),
) -> dict[str, Any]:
    """The archive in time order, `limit` rows deep.

    Dates are compared as STRINGS on purpose: every date in the archive is
    ISO-8601, which sorts lexically, so a range filter is correct whether the
    event is known to the day or only to the year.

    ORDER MATTERS AT SCALE. The wire corpus made a five-year window hold tens
    of thousands of events, so `limit` no longer covers a window — it samples
    one end of it. `order=desc` returns the newest `limit`, which is what the
    explorer's "what's happening lately" list wants; the rows still come back
    in the requested order.
    """
    desc = order == "desc"
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
            params["start_date"] = archive_bounds.clamp_start(start)
        else:
            clauses.append("e.event_time >= $start_date")
            params["start_date"] = archive_bounds.START
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
        direction = "DESC" if desc else "ASC"
        graph_rows = kuzu_store.query(
            conn,
            f"MATCH (e:Event) {where}"
            "OPTIONAL MATCH (e)-[:INITIATED_BY]->(ia:Actor) "
            "OPTIONAL MATCH (e)-[:DIRECTED_AT]->(ta:Actor) "
            "OPTIONAL MATCH (e)-[:OF_DYAD]->(d:Dyad) "
            f"RETURN {_EVENT_COLUMNS}, ia.node_id AS initiator_id, "
            "ta.node_id AS target_id, d.node_id AS dyad_id "
            f"ORDER BY e.event_time {direction}, e.node_id {direction} LIMIT $limit",
            params,
        )
        graph_truncated = len(graph_rows) > limit
        graph_rows = graph_rows[:limit]

    wire_rows, wire_truncated = wire_serving.events_window(
        pack, start, end, limit, newest_first=desc
    )

    seen = {row["node_id"] for row in graph_rows}
    merged = graph_rows + [row for row in wire_rows if row["node_id"] not in seen]
    merged.sort(key=lambda row: (row["event_time"] or "", row["node_id"]), reverse=desc)
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
    if not rows:
        rows = _effects_from_panel(conn, node_id)
    return {"event": node_id, "measured": len(rows), "rows": rows}


def _effects_from_panel(conn: Any, event_id: str) -> list[dict[str, Any]]:
    """GDELT measurements live in event_study_runs after the graph copy is gone."""
    from core.panel import pg_store

    try:
        panel = pg_store.connect(settings_module.load())
    except pg_store.PanelUnavailable:
        return _effects_from_live(event_id)
    try:
        runs = pg_store.computed_runs(panel, event_id=event_id)
    finally:
        panel.close()
    if not runs:
        return _effects_from_live(event_id)
    markets = {
        str(r["ticker"]): r
        for r in kuzu_store.query(
            conn,
            "MATCH (m:Market) RETURN m.node_id AS market_node_id, m.name AS market, "
            "m.ticker AS ticker, m.market_type AS market_type",
        )
    }
    rows: list[dict[str, Any]] = []
    for run in runs:
        meta = markets.get(str(run["market_ticker"]), {})
        rows.append({
            "market_node_id": meta.get("market_node_id"),
            "market": meta.get("market"),
            "ticker": run["market_ticker"],
            "market_type": meta.get("market_type"),
            "window": run["window"],
            "resolution": run.get("resolution"),
            "raw_return": run.get("raw_return"),
            "expected_return": run.get("expected_return"),
            "abnormal_return": run["abnormal_return"],
            "t_stat": run.get("t_stat"),
            "p_value": run.get("p_value"),
            "first_mover": run.get("first_mover"),
            "overlapping": run.get("status") == "overlapping",
            "method": run.get("method"),
            "source_id": run.get("source_id"),
        })
    rows.sort(key=lambda r: (str(r["ticker"]), str(r["window"])))
    return rows


def _effects_from_live(event_id: str) -> list[dict[str, Any]]:
    """This-event CARs from the GDELT 2.0 overlay. Not the frozen map."""
    from core.wire import live as live_overlay

    row = live_overlay.row_by_id(event_id)
    measured = list((row or {}).get("measured") or [])
    rows: list[dict[str, Any]] = []
    for item in measured:
        rows.append({
            "market_node_id": item.get("ticker"),
            "market": item.get("market") or item.get("ticker"),
            "ticker": item.get("ticker"),
            "market_type": None,
            "window": item.get("window"),
            "resolution": item.get("resolution"),
            "raw_return": item.get("raw_return"),
            "expected_return": item.get("expected_return"),
            "abnormal_return": item.get("abnormal_return"),
            "t_stat": item.get("t_stat"),
            "p_value": item.get("p_value"),
            "first_mover": item.get("first_mover"),
            "overlapping": item.get("overlapping"),
            "method": item.get("method"),
            "source_id": None,
        })
    rows.sort(key=lambda r: (str(r["ticker"]), str(r["window"])))
    return rows


def _live_event_detail(node_id: str) -> dict[str, Any] | None:
    """A GDELT 2.0 overlay row shaped like the graph/corpus event detail."""
    from core.wire import live as live_overlay

    row = live_overlay.row_by_id(node_id)
    if row is None:
        return None
    from core import packs

    names: dict[str, str] = {}
    try:
        pack = packs.load(row["region_pack"]) if row.get("region_pack") else None
        if pack is not None:
            names = {a["id"]: a["name"] for a in pack.actors}
    except Exception:  # noqa: BLE001 - names are display-only
        pass

    initiator_id = row.get("initiator_id")
    target_id = row.get("target_id")
    dyad_id = row.get("dyad_id")
    initiator_name = (
        row.get("initiator_name")
        or names.get(str(initiator_id or ""), initiator_id)
    )
    target_name = (
        row.get("target_name") or names.get(str(target_id or ""), target_id)
    )
    return {
        "node_id": row.get("node_id") or node_id,
        "name": row.get("name") or node_id,
        "event_time": row.get("event_time"),
        "cameo_code": row.get("action_cameo_code") or row.get("cameo_code"),
        "quad_class": row.get("quad_class"),
        "goldstein": row.get("goldstein"),
        "escalation_direction": row.get("escalation_direction"),
        "escalation_magnitude": row.get("escalation_magnitude"),
        "escalation_baseline": row.get("escalation_baseline"),
        "fidelity_tier": "live",
        "temporal_resolution": "day",
        "source_scale": "coded",
        "region_pack": row.get("region_pack"),
        "initiator": (
            {"node_id": initiator_id, "name": initiator_name}
            if initiator_id else None
        ),
        "target": (
            {"node_id": target_id, "name": target_name}
            if target_id else None
        ),
        "dyad": {"node_id": dyad_id} if dyad_id else None,
        "regimes": [],
        "sources": [_cited_source(gdelt.SOURCE_GDELT)],
        "live": True,
    }


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
        "sources": [_cited_source(row.get("source_id"))] if row.get("source_id") else [],
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
        from_live = _live_event_detail(node_id)
        if from_live is not None:
            return from_live
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
        "sources": [_cited_source(s.get("node_id"), s) for s in sources],
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
def list_dyads(request: Request, region: str | None = None) -> dict[str, Any]:
    """Every dyad with escalation state, most conflictual baseline first.

    `region` keeps the dyads whose BOTH actors sit on that pack's roster —
    the parameter was accepted and ignored until 2026-08-15, so every lens
    served the same 32 spine dyads (Iran pairs under the Eurasia lens)."""
    conn = _conn(request)
    rows = kuzu_store.query(
        conn,
        "MATCH (d:Dyad) RETURN d.node_id AS node_id, d.name AS name, "
        "d.actor_a_id AS actor_a_id, d.actor_b_id AS actor_b_id, "
        "d.ewma_baseline AS ewma_baseline, d.ewma_as_of AS ewma_as_of "
        "ORDER BY d.node_id",
    )
    # A ROSTER DYAD HAS NO BASELINE UNTIL HEAD B CODES IT. `pack.dyad_nodes()`
    # writes every declared pair so the explorer and the games have something to
    # hang a measurement on, and Kuzu orders NULL FIRST — so "most conflictual"
    # opened with the pairs holding no coded record at all. Uncoded pairs sort
    # last and keep a null baseline: unknown is not zero and not peaceful.
    rows.sort(
        key=lambda r: (
            r["ewma_baseline"] is None,
            r["ewma_baseline"] if r["ewma_baseline"] is not None else 0.0,
            r["node_id"],
        )
    )
    if region:
        from core import packs

        try:
            roster = {a["id"] for a in packs.load(region).actors}
        except packs.PackError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        rows = [r for r in rows if r["actor_a_id"] in roster and r["actor_b_id"] in roster]
    return {"rows": rows, "region": region}


#: How far above a pair's own running baseline counts as a DEPARTURE worth
#: leading with. `escalation_magnitude` is |score − baseline| in Goldstein
#: points, so this is "three points off its own usual", not "hostile" — the
#: whole point of a relational baseline is that a −6 is routine for a rivalry
#: and a rupture for an alliance.
WIRE_DEPARTURE_POINTS = 3.0

#: Events a wire page returns. It is a feed, not an archive read.
WIRE_LIMIT = 60


@router.get("/wire")
def wire(
    request: Request,
    region: str | None = None,
    limit: int = Query(WIRE_LIMIT, ge=1, le=200),
    since: str | None = Query(None, description="ISO date, inclusive"),
) -> dict[str, Any]:
    """The newest coded events, as a wire — each with the system's first read.

    WHAT MAKES THIS A WIRE RATHER THAN A TABLE is the second half: every item
    carries the fields for a one-line read of what just happened, composed
    from numbers that already existed. It says how far the event sits from
    THAT PAIR's own running baseline, which is the only reading that survives
    contact with this archive — a −6.0 is a routine week for a rivalry and a
    rupture for an alliance, so an absolute scale would call the first a
    crisis and miss the second entirely.

    §17 holds here as it does everywhere: the fields are named and the SENTENCE
    is composed on the surface (`web/src/lib/story.ts`). Nothing in this
    payload is prose the backend invented, and the agent — when a key is set —
    narrates AROUND these numbers rather than producing them.

    Reads the same union as `/events` (graph plus corpus, deduped) so a day
    the harvest fetched an hour ago appears here before any deploy.
    """
    rows_payload = list_events(
        request, start=since, end=None, pack=region, limit=limit, order="desc",
    )
    rows = rows_payload["rows"]

    names, geo_names = _pack_roster()
    items: list[dict[str, Any]] = []
    for row in rows:
        magnitude = row.get("escalation_magnitude")
        baseline = row.get("escalation_baseline")
        goldstein = row.get("goldstein")
        departure = (
            magnitude is not None and float(magnitude) >= WIRE_DEPARTURE_POINTS
        )
        initiator_name = names.get(str(row.get("initiator_id") or ""))
        target_name = names.get(str(row.get("target_id") or ""))
        display = wire_headline.display_fields(
            {**row, "initiator_name": initiator_name, "target_name": target_name},
            geo_names=geo_names,
        )
        items.append({
            **row,
            "initiator_name": initiator_name,
            "target_name": target_name,
            # THE FIRST READ, as fields. `departure` is the judgement the
            # surface leads with; `points_from_baseline` is the number that
            # substantiates it; `baseline` is what it departed FROM, so a
            # reader can see that the bar moves per pair.
            "departure": departure,
            "points_from_baseline": (
                round(float(magnitude), 2) if magnitude is not None else None
            ),
            "pair_baseline": (
                round(float(baseline), 2) if baseline is not None else None
            ),
            "tone": (
                "cooperative" if goldstein is not None and float(goldstein) > 0
                else "coercive" if goldstein is not None and float(goldstein) < 0
                else None
            ),
            # Display/nav only: action_geo is corpus-only and is omitted when
            # the row never carried it. Never a stored retarget.
            **display,
        })
    return {
        "rows": items,
        "region": region,
        "truncated": rows_payload.get("truncated", False),
        "departure_points": WIRE_DEPARTURE_POINTS,
        "as_of": items[0]["event_time"] if items else None,
        "method": (
            "the newest coded events, graph and corpus deduped; a departure is "
            f"an event at least {WIRE_DEPARTURE_POINTS} Goldstein points from "
            "that pair's own running baseline, not from an absolute scale"
        ),
    }


def _pack_roster() -> tuple[dict[str, str], dict[str, str]]:
    """actor node_id → name, and iso3 → name, from the packs.

    FROM THE PACKS, NOT THE GRAPH, and that is deliberate. The roster is the
    same data in both places, but the graph is not always open: the study runs
    as a child process, a child holds Kuzu's single write lock, and every graph
    endpoint answers 503 for its slice — measured at ~1 sample in 12. A wire
    that sourced names from the graph would spend that window printing events
    with no actors on them, which reads as broken rather than as busy.

    It is also simply cheaper: no query at all, against one per request.
    """
    from core import packs

    names: dict[str, str] = {}
    geo: dict[str, str] = {}
    for pack_name in packs.available():
        try:
            pack = packs.load(pack_name)
        except packs.PackError:
            continue
        for actor in pack.actors:
            node_id, name = str(actor.get("id") or ""), actor.get("name")
            if node_id and name:
                names.setdefault(node_id, str(name))
            iso3 = str(actor.get("iso3") or "").strip().upper()
            if iso3 and name:
                geo.setdefault(iso3, str(name))
    return names, geo


#: The live poll is cached for this long. GDELT publishes every 15 minutes, so
#: anything under that is re-fetching a file that has not changed; 60s keeps a
#: refreshing page responsive without hammering a free service.
_LIVE_TTL_SECONDS = 60.0
_live_cache: dict[str, Any] = {"at": 0.0, "payload": None, "region": None}

#: Goldstein bands → the kinds the transmission map measured. KEPT as the
#: raw-score fallback and pinned by tests; `/wire/live` now uses Head B
#: (`markets.kind_of` over `escalation_direction`) against the snapshot EWMA.
def _implied_kind(goldstein: float | None) -> str:
    if goldstein is None:
        return "stable"
    if goldstein <= -7.0:
        return "sharp_escalation"
    if goldstein < -2.0:
        return "escalation"
    if goldstein > 2.0:
        return "de-escalation"
    return "stable"


#: `stable` is the dump bucket: every consult, public statement and near-zero
#: Goldstein score lands here, so its historical median is "what the region's
#: markets do on ordinary days", not what THIS event is worth. Attaching that
#: cell as a high-confidence trade is how a 1-mention Iran–Iraq consultation
#: shipped with "short Dubai / long the S&P" on 2026-08-17. Escalation kinds
#: are sparse enough that the cell is actually about events like this one.
_ACTIONABLE_KINDS = frozenset({"sharp_escalation", "escalation", "de-escalation"})


@router.get("/wire/live")
def wire_live(region: str = "mena", limit: int = Query(30, ge=1, le=100)) -> dict[str, Any]:
    """The wire, LIVE — GDELT 2.0's 15-minute stream, scored and priced.

    THE ARCHIVE READS GDELT 1.0, WHICH PUBLISHES A DAY IN ARREARS, and that one
    fact is why nothing here was ever tradeable: by the time an event arrived,
    session 0 — the session whose return CONTAINS the impact — had closed. The
    2.0 stream publishes every fifteen minutes, so an event can be read inside
    the session it moves.

    WHAT THE EDGE IS AND IS NOT. Fifteen minutes is slow against a headline; a
    news algorithm is in the book before this file exists, and nothing here
    competes on speed. What this archive has is the MEASURED RECORD: how the
    region's markets have actually moved on events coded like this one, with the
    sample size attached. Speed gets you the headline. The record tells you what
    headlines like it have been worth.

    Every figure on a row is a measurement — `median`/`p25`/`p75` are realised
    abnormal returns over the four sessions after past events of that coding,
    `n` is how many, and a thin sample says so rather than rounding to a number.
    """
    import time

    from core import packs
    from core.panel import pg_store
    from core.reasoning import markets as markets_module
    from core.wire import live as live_overlay

    now = time.monotonic()
    if (_live_cache["payload"] is not None and _live_cache["region"] == region
            and now - float(_live_cache["at"]) < _LIVE_TTL_SECONDS):
        cached = dict(_live_cache["payload"])
        cached["cached"] = True
        return cached

    try:
        pack = packs.load(region)
    except packs.PackError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        polled = live_overlay.ensure_pack(pack)
    except Exception as exc:  # noqa: BLE001 - a live feed failing is not a 500
        raise HTTPException(
            status_code=503, detail=f"the GDELT 2.0 stream did not answer: {exc}"
        ) from exc

    # The measured record, from the persisted market story — no recomputation
    # on a request thread, and no number invented here.
    responses: dict[str, Any] = {}
    settings = settings_module.load()
    try:
        panel = pg_store.connect(settings)
    except pg_store.PanelUnavailable:
        panel = None
    if panel is not None:
        try:
            story = pg_store.market_story(panel, region)
            for market in (story or {}).get("markets", []):
                responses[market["ticker"]] = {
                    "name": market.get("name"),
                    "response": market.get("response") or {},
                }
        except Exception:  # noqa: BLE001 - the feed stands without the record
            responses = {}
        finally:
            panel.close()

    names, geo_names = _pack_roster()
    rows: list[dict[str, Any]] = []
    for row in polled.get("rows", [])[:limit]:
        goldstein = row.get("goldstein")
        kind = markets_module.kind_of(
            row.get("escalation_direction"), row.get("escalation_magnitude"),
        )
        outlook = []
        # THE DUMP BUCKET CARRIES NO PRICE. A stable-kind median is the
        # region's ordinary-day move, identical on every consult; stamping it
        # `trade` taught the wire page to look like a blotter. Escalation
        # kinds keep the historical cell, IQR included, so a reader can see
        # the spread the strategy gate is (or is not) clearing.
        if kind in _ACTIONABLE_KINDS:
            for ticker, entry in responses.items():
                cell = ((entry["response"].get(kind) or {}).get(markets_module.HEADLINE_WINDOW)
                        or {})
                if not cell.get("n"):
                    continue
                outlook.append({
                    "ticker": ticker,
                    "market": entry["name"],
                    "median": cell.get("median"),
                    "p25": cell.get("p25"),
                    "p75": cell.get("p75"),
                    "n": cell.get("n"),
                    "share_positive": cell.get("share_positive"),
                    "thin": bool(cell.get("thin")),
                })
            outlook.sort(key=lambda m: abs(m.get("median") or 0.0), reverse=True)
        initiator_name = names.get(str(row.get("initiator_id") or ""))
        target_name = names.get(str(row.get("target_id") or ""))
        display = wire_headline.display_fields(
            {**row, "initiator_name": initiator_name, "target_name": target_name},
            geo_names=geo_names,
        )
        rows.append({
            "node_id": row.get("node_id"),
            "event_time": row.get("event_time"),
            "name": row.get("name"),
            "cameo_code": row.get("action_cameo_code"),
            "quad_class": row.get("quad_class"),
            "goldstein": goldstein,
            "escalation_baseline": row.get("escalation_baseline"),
            "escalation_direction": row.get("escalation_direction"),
            "escalation_magnitude": row.get("escalation_magnitude"),
            "initiator_id": row.get("initiator_id"),
            "target_id": row.get("target_id"),
            "initiator_name": initiator_name,
            "target_name": target_name,
            "dyad_id": row.get("dyad_id"),
            "available_at": row.get("available_at"),
            # Cite the dataset, never GDELT SOURCEURL. That field is a mention
            # string — a baseball story sharing an actor name is a known miss —
            # and a well-formed URL is not a verified article.
            "source_id": gdelt.SOURCE_GDELT,
            "source_name": "GDELT",
            "source_url": citable_url(gdelt.SOURCE_GDELT_URL),
            "mentions": row.get("mentions"),
            "num_sources": row.get("num_sources"),
            "implied_kind": kind,
            "market_outlook": outlook[:4],
            "measured": list(row.get("measured") or []),
            # Display/nav only. Live rows already carry action_geo from the
            # parser; nothing here retargets USA→SYR as a stored fact.
            **display,
        })

    payload = {
        "region": region,
        "published": polled.get("published"),
        "fetched_at": polled.get("fetched_at"),
        "scanned": polled.get("scanned"),
        "kept": polled.get("kept"),
        "rows": rows,
        "cached": False,
        "method": (
            "Newest 15-minute GDELT 2.0 export, scored against each pair's "
            "usual level in the frozen archive. `measured` on a row is this "
            "event's session move from the price panel, computed in memory "
            "and not written into the transmission map. Market cells are "
            "realised abnormal returns over the four sessions after past "
            "events of that coding, with n — analogy, not this event. "
            "Nothing here is written to the graph, nothing is a forecast, "
            "and nothing is advice."
        ),
    }
    _live_cache.update({"at": now, "payload": payload, "region": region})
    return payload
