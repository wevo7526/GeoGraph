"""Graph meta and stats: the ontology as data, and what the graph holds.

COVERAGE SHIPS AS DATA, not prose — an agent or a reader that does not know
the graph currently holds one region pack and no deep tier yet will claim
"nothing happened in 1956" when the truthful claim is far narrower.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core.graph import kuzu_store
from core.ontology import kuzu_schema as ontology

router = APIRouter(tags=["graph"])


def _conn(request: Request) -> Any:
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503,
            detail=request.app.state.graph_error or "graph unavailable",
        )
    return conn


@router.get("/ontology")
def ontology_summary() -> dict[str, Any]:
    """The running schema, inspectable rather than taken on trust."""
    return ontology.summary()


#: THE COUNTS ARE A JOB'S WORK, NOT A READER'S. Answering /stats scans every
#: table — about twenty scans, measured at 12.4s once the archive passed a
#: million events, on the endpoint the front page and the explorer both open
#: with. A background pass (core/api/work.py::counts) refills this every few
#: minutes so a reader gets a snapshot in milliseconds; the numbers move
#: continuously now that jobs write, so a count is a snapshot whatever it
#: costs. `fresh=true` forces the scan for a caller who wants it exact.
_STATS_TTL_SECONDS = 600.0
_stats_cache: dict[str, Any] = {"at": 0.0, "value": None}


def count_tables(conn: Any) -> dict[str, Any]:
    """The scan itself, shared by the endpoint and the job that warms it."""
    import time

    node_counts = {
        table: kuzu_store.query(conn, f"MATCH (n:{table}) RETURN count(*) AS n")[0]["n"]
        for table in ontology.nodes()
    }
    edge_counts = {
        rel: kuzu_store.query(conn, f"MATCH ()-[r:{rel}]->() RETURN count(*) AS n")[0]["n"]
        for rel in ontology.edges()
    }
    out = {"nodes": node_counts, "edges": edge_counts}
    _stats_cache.update({"at": time.monotonic(), "value": out})
    return dict(out)


@router.get("/stats")
def stats(request: Request, fresh: bool = False) -> dict[str, Any]:
    """Node and edge counts per table — the coverage statement, honest even
    (especially) when the answer is zero."""
    import time

    conn = _conn(request)
    cached = _stats_cache["value"]
    fresh_enough = (
        cached is not None
        and time.monotonic() - float(_stats_cache["at"]) < _STATS_TTL_SECONDS
    )
    if not fresh and fresh_enough:
        return dict(cached)
    return count_tables(conn)


#: RELATES_TO at deep-tier scale is tens of thousands of membership spells;
#: the cap exists so the network cannot return itself in one response.
MAX_RELATION_ROWS = 4000


@router.get("/relations")
def relations(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    relation_type: str | None = None,
) -> dict[str, Any]:
    """The durable network: RELATES_TO edges with their validity windows.

    This is the structure the explorer draws UNDER the event flow — the proxy
    web, the alliances, IGO membership — as distinct from the dyads Head B
    measures escalation against. `proxy` rows are directed patron → client.
    With start/end, only edges whose validity intersects the window return —
    ISO strings compare lexically at every resolution.
    """
    conn = _conn(request)
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": MAX_RELATION_ROWS + 1}
    if end:
        clauses.append("(r.valid_from = '' OR r.valid_from <= $end_date)")
        params["end_date"] = end
    if start:
        clauses.append("(r.valid_to = '' OR r.valid_to IS NULL OR r.valid_to >= $start_date)")
        params["start_date"] = start
    if relation_type:
        clauses.append("r.relation_type = $relation_type")
        params["relation_type"] = relation_type
    columns = (
        "RETURN a.node_id AS a_id, a.name AS a_name, "
        "b.node_id AS b_id, b.name AS b_name, "
        "r.relation_type AS relation_type, r.valid_from AS valid_from, "
        "r.valid_to AS valid_to, r.source_id AS source_id "
        "ORDER BY relation_type, a_id, b_id LIMIT $limit"
    )
    if relation_type:
        where = f"WHERE {' AND '.join(clauses)} "
        rows = kuzu_store.query(
            conn,
            f"MATCH (a:Actor)-[r:RELATES_TO]->(b:Actor) {where}{columns}",
            params,
        )
    else:
        # TWO queries, membership LAST: the alphabetical cap was silently
        # truncating proxy and rivalry rows behind eighteen thousand IGO
        # membership spells. The sparse layers always arrive whole; the bulk
        # layer fills whatever room the cap leaves.
        sparse_clauses = [*clauses, "r.relation_type <> 'membership'"]
        rows = kuzu_store.query(
            conn,
            f"MATCH (a:Actor)-[r:RELATES_TO]->(b:Actor) "
            f"WHERE {' AND '.join(sparse_clauses)} {columns}",
            params,
        )
        room = MAX_RELATION_ROWS + 1 - len(rows)
        if room > 0:
            bulk_clauses = [*clauses, "r.relation_type = 'membership'"]
            rows += kuzu_store.query(
                conn,
                f"MATCH (a:Actor)-[r:RELATES_TO]->(b:Actor) "
                f"WHERE {' AND '.join(bulk_clauses)} {columns}",
                {**params, "limit": room},
            )
    return {
        "rows": rows[:MAX_RELATION_ROWS],
        "truncated": len(rows) > MAX_RELATION_ROWS,
    }


@router.get("/flows")
def flows(
    request: Request,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """SWF capital deployment: FLOW edges from 13F filings, filer → market.

    The money layer's own caveat rides along in the class description and
    here: a COARSE, LAGGED (45-day), US-LONG-ONLY view — an edge says where
    reported capital sat at quarter end, never what it did since.
    """
    conn = _conn(request)
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if start:
        clauses.append("f.as_of >= $start_date")
        params["start_date"] = start
    if end:
        clauses.append("f.as_of <= $end_date")
        params["end_date"] = end
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    rows = kuzu_store.query(
        conn,
        f"MATCH (a:Actor)-[f:FLOW]->(m:Market) {where}"
        "RETURN a.node_id AS actor_id, a.name AS actor_name, "
        "m.node_id AS market_id, m.name AS market_name, m.ticker AS ticker, "
        "f.as_of AS as_of, f.value_usd AS value_usd, f.source_id AS source_id "
        "ORDER BY as_of, actor_id, market_id",
        params,
    )
    return {"rows": rows}


@router.get("/actors")
def actors(
    request: Request,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """The actor roster, optionally windowed by state-system membership.

    THE ACTOR SET IS TIME-VARYING: with start/end, states outside their COW
    membership window are absent — 1914 shows Austria-Hungary and not the
    PIF. Actors without membership dates (orgs, funds) are always present;
    their relevance in a window is their edges, which the explorer styles.
    """
    conn = _conn(request)
    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor) RETURN a.node_id AS node_id, a.name AS name, "
        "a.actor_type AS actor_type, a.cow_ccode AS cow_ccode, "
        "a.state_from AS state_from, a.state_to AS state_to, "
        "a.region_pack AS region_pack ORDER BY a.node_id",
    )
    if start or end:
        def alive(row: dict[str, Any]) -> bool:
            state_from = str(row.get("state_from") or "")
            state_to = str(row.get("state_to") or "")
            if end and state_from and state_from > end:
                return False
            return not (start and state_to and state_to < start)

        rows = [row for row in rows if alive(row)]
    return {"rows": rows}


@router.get("/provenance")
def provenance(request: Request) -> dict[str, Any]:
    """The invariant, checkable over HTTP: every sourced edge cites a Source
    that exists. An empty violation list is the claim this project makes."""
    violations = kuzu_store.check_provenance(_conn(request))
    return {"ok": not violations, "violations": violations}
