"""Kuzu writers and the provenance backstop — the MarketGraph store pattern.

EVERY WRITE GOES THROUGH `merge_nodes` / `merge_edges`, which call the
validators derived from the LinkML schema. Nothing writes an edge by another
path; that discipline is what makes `check_provenance` a backstop instead of
the only line of defense.

KUZU IS SINGLE-WRITER. One process holds an exclusive lock on the graph
directory. Batch ingestion and transmission jobs therefore write ONE AT A TIME
(build-spec section 6); `connect` detects the lock error and says so rather
than blaming the path.

FIVE KUZU BEHAVIOURS FAIL SILENTLY — inherited knowledge from MarketGraph,
same engine, same traps. Do not "simplify" the workarounds:
  - `count(DISTINCT x)` and `sum(y)` in one RETURN → the sum is NULL.
  - `sum(CASE WHEN ...)` → NULL. Use arithmetic identities.
  - `MATCH (n:A|B)` → unsupported. UNION ALL per label.
  - `RETURN n` across a UNION → NODE types differ per table; return scalars.
  - `sum(x)` over INT64 → a Decimal, which FastAPI serialises as a JSON
    STRING. `_plain` normalises every row at this boundary — never per-query.
Also: `when` is a reserved word, properties bind per label, and the parser
rejects `--` comments.
"""

from __future__ import annotations

import decimal
from pathlib import Path
from typing import Any

import kuzu

from core.ontology import kuzu_schema as ontology

__all__ = [
    "GraphUnavailable",
    "connect",
    "apply_schema",
    "merge_nodes",
    "merge_edges",
    "check_provenance",
    "query",
]


class GraphUnavailable(RuntimeError):
    """The graph cannot be opened. The message names the likely fix."""


def connect(db_path: Path, *, read_only: bool = False) -> kuzu.Connection:
    """Open the embedded graph. Diagnoses the single-writer lock explicitly."""
    try:
        db = kuzu.Database(str(db_path), read_only=read_only)
        return kuzu.Connection(db)
    except RuntimeError as exc:  # kuzu raises RuntimeError for IO/lock errors
        message = str(exc)
        if "lock" in message.lower():
            raise GraphUnavailable(
                f"Another process holds the write lock on {db_path}. Kuzu is "
                "single-writer: stop the other writer (the API process, or a "
                "running ingest/transmission job) or open read-only."
            ) from exc
        raise GraphUnavailable(f"Cannot open graph at {db_path}: {message}") from exc


def apply_schema(conn: kuzu.Connection) -> None:
    """Create every table the ontology declares. Idempotent (IF NOT EXISTS)."""
    for statement in ontology.ddl():
        conn.execute(statement)


def _plain(value: Any) -> Any:
    """Normalise driver values at the boundary — THE Decimal fix.

    Kuzu returns Decimal for INT64 aggregates; FastAPI serialises Decimal as a
    JSON string, and JavaScript coerces number-shaped strings in arithmetic so
    nothing downstream ever throws — formatting just quietly breaks. Fixed
    here, once, never per-query.
    """
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def query(
    conn: kuzu.Connection, cypher: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Run one Cypher statement, returning plain dict rows."""
    result = conn.execute(cypher, parameters=params or {})
    columns = result.get_column_names()
    rows: list[dict[str, Any]] = []
    while result.has_next():
        rows.append(
            {col: _plain(val) for col, val in zip(columns, result.get_next(), strict=True)}
        )
    return rows


def merge_nodes(conn: kuzu.Connection, table: str, rows: list[dict[str, Any]]) -> int:
    """Upsert nodes by node_id. Validates every row against the ontology."""
    spec = ontology.nodes().get(table)
    if spec is None:
        raise ontology.OntologyError(f"{table!r} is not a node table.")
    written = 0
    prop_names = [p.name for p in spec.props]
    for row in rows:
        ontology.validate_node(table, row)
        present = [n for n in prop_names if n in row]
        sets = ", ".join(f"n.{n} = ${n}" for n in present)
        cypher = f"MERGE (n:{table} {{node_id: $node_id}})"
        if sets:
            cypher += f" ON CREATE SET {sets} ON MATCH SET {sets}"
        params = {"node_id": row["node_id"], **{n: row[n] for n in present}}
        conn.execute(cypher, parameters=params)
        written += 1
    return written


def merge_edges(conn: kuzu.Connection, rel: str, rows: list[dict[str, Any]]) -> int:
    """Upsert edges. THE ONLY EDGE-WRITE PATH.

    Each row: {"src": node_id, "dst": node_id, **props}. Key slots — read from
    the ontology, never from a hardcoded rel-name test — go into the MERGE
    pattern so two key values are two edges; the rest are SET.
    """
    spec = ontology.edges().get(rel)
    if spec is None:
        raise ontology.OntologyError(f"{rel!r} is not an edge table.")
    written = 0
    for row in rows:
        props = {k: v for k, v in row.items() if k not in ("src", "dst")}
        ontology.validate_edge(rel, props)
        keys = [k for k in spec.key_slots if k in props]
        rest = [k for k in props if k not in keys]
        key_pattern = (" {" + ", ".join(f"{k}: ${k}" for k in keys) + "}") if keys else ""
        sets = ", ".join(f"r.{k} = ${k}" for k in rest)
        cypher = (
            f"MATCH (a:{spec.src} {{node_id: $src}}), (b:{spec.dst} {{node_id: $dst}}) "
            f"MERGE (a)-[r:{rel}{key_pattern}]->(b)"
        )
        if sets:
            cypher += f" ON CREATE SET {sets} ON MATCH SET {sets}"
        conn.execute(cypher, parameters={"src": row["src"], "dst": row["dst"], **props})
        written += 1
    return written


def check_provenance(conn: kuzu.Connection) -> list[str]:
    """THE BACKSTOP (build-spec section 17): every sourced edge's source_id
    resolves to a Source that exists. Returns violations; ingest fails on any.

    The validator makes violations unwritable through `merge_edges`; this
    catches any path that bypassed it, and a source_id that points nowhere.
    """
    problems: list[str] = []
    for rel in ontology.sourced_edges():
        rows = query(
            conn,
            f"MATCH ()-[r:{rel}]->() WHERE r.source_id IS NULL OR r.source_id = '' "
            "RETURN count(*) AS n",
        )
        missing = rows[0]["n"] if rows else 0
        if missing:
            problems.append(f"{rel}: {missing} edge(s) with no source_id")

        cited = {
            row["sid"]
            for row in query(
                conn,
                f"MATCH ()-[r:{rel}]->() WHERE r.source_id IS NOT NULL AND r.source_id <> '' "
                "RETURN DISTINCT r.source_id AS sid",
            )
        }
        if cited:
            known = {
                row["node_id"]
                for row in query(
                    conn,
                    "MATCH (s:Source) WHERE s.node_id IN $ids RETURN s.node_id AS node_id",
                    {"ids": sorted(cited)},
                )
            }
            for orphan in sorted(cited - known):
                problems.append(f"{rel}: source_id {orphan!r} resolves to no Source node")
    return problems
