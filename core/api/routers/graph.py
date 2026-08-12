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


@router.get("/stats")
def stats(request: Request) -> dict[str, Any]:
    """Node and edge counts per table — the coverage statement, honest even
    (especially) when the answer is zero."""
    conn = _conn(request)
    node_counts = {
        table: kuzu_store.query(conn, f"MATCH (n:{table}) RETURN count(*) AS n")[0]["n"]
        for table in ontology.nodes()
    }
    edge_counts = {
        rel: kuzu_store.query(conn, f"MATCH ()-[r:{rel}]->() RETURN count(*) AS n")[0]["n"]
        for rel in ontology.edges()
    }
    return {"nodes": node_counts, "edges": edge_counts}


@router.get("/relations")
def relations(request: Request) -> dict[str, Any]:
    """The durable network: RELATES_TO edges with their validity windows.

    This is the structure the explorer draws UNDER the event flow — the proxy
    web, the alliances — as distinct from the dyads Head B measures escalation
    against. `proxy` rows are directed patron → client.
    """
    conn = _conn(request)
    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor)-[r:RELATES_TO]->(b:Actor) "
        "RETURN a.node_id AS a_id, a.name AS a_name, "
        "b.node_id AS b_id, b.name AS b_name, "
        "r.relation_type AS relation_type, r.valid_from AS valid_from, "
        "r.valid_to AS valid_to, r.source_id AS source_id "
        "ORDER BY relation_type, a_id, b_id",
    )
    return {"rows": rows}


@router.get("/provenance")
def provenance(request: Request) -> dict[str, Any]:
    """The invariant, checkable over HTTP: every sourced edge cites a Source
    that exists. An empty violation list is the claim this project makes."""
    violations = kuzu_store.check_provenance(_conn(request))
    return {"ok": not violations, "violations": violations}
