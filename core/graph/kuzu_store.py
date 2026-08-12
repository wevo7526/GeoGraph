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

import contextlib
import decimal
from pathlib import Path
from typing import Any

import kuzu

from core.ontology import kuzu_schema as ontology

__all__ = [
    "GraphUnavailable",
    "apply_schema",
    "check_provenance",
    "close",
    "connect",
    "merge_edges",
    "merge_nodes",
    "query",
]


class GraphUnavailable(RuntimeError):
    """The graph cannot be opened. The message names the likely fix."""


def connect(db_path: Path, *, read_only: bool = False) -> kuzu.Connection:
    """Open the embedded graph. Diagnoses the single-writer lock explicitly."""
    try:
        db = kuzu.Database(str(db_path), read_only=read_only)
        conn = kuzu.Connection(db)
    except RuntimeError as exc:  # kuzu raises RuntimeError for IO/lock errors
        message = str(exc)
        if "lock" in message.lower():
            raise GraphUnavailable(
                f"Another process holds the write lock on {db_path}. Kuzu is "
                "single-writer: stop the other writer (the API process, or a "
                "running ingest/transmission job) or open read-only."
            ) from exc
        if "virtualalloc" in message.lower() or "buffer manager" in message.lower():
            raise GraphUnavailable(
                f"Cannot open {db_path}: the process is out of virtual address "
                "space for graphs. EACH open Kuzu database reserves an 8 TiB "
                "virtual allocation, so ONE PROCESS CAN HOLD ONLY ~15 AT ONCE — "
                "call kuzu_store.close() on graphs you are done with rather than "
                "dropping the reference. Original: " + message
            ) from exc
        raise GraphUnavailable(f"Cannot open graph at {db_path}: {message}") from exc
    # Keep the Database reachable from the Connection so `close` can shut both.
    # Dropping the Python reference does NOT reliably release the write lock or
    # the reservation; only closing does.
    conn._geograph_db = db  # type: ignore[attr-defined]
    return conn


def close(conn: kuzu.Connection | None) -> None:
    """Release a graph: close the connection AND its database.

    WHY THIS IS NOT OPTIONAL HYGIENE. Two separate limits bite without it.
    Kuzu is single-writer, so a graph left open blocks the next writer — the
    API cannot take a lock a finished batch job still holds. And each open
    database reserves an 8 TiB virtual allocation, so a process that opens
    graphs in a loop dies at roughly the fifteenth with a buffer-manager error
    that names memory rather than the real cause.
    """
    if conn is None:
        return
    database = getattr(conn, "_geograph_db", None)
    for target in (conn, database):
        if target is None:
            continue
        with contextlib.suppress(Exception):
            target.close()


def apply_schema(conn: kuzu.Connection) -> None:
    """Create every table the ontology declares, then ADD any property an
    existing table is missing. Idempotent.

    The second half is THE MIGRATION PATH. `CREATE ... IF NOT EXISTS` skips a
    table that already exists, so a graph created before the model gained a
    column (a Railway volume survives deploys — that is its job) would refuse
    the next seed with a binder error naming the property. Additive columns
    are the only migration the ontology can imply; a REMOVED or retyped
    property still needs a rebuild, which for this graph is a delete-and-
    reseed — every fact reloads from the packs and crosswalks by design.
    """
    for statement in ontology.ddl():
        conn.execute(statement)

    tables = [(spec.table, spec.props) for spec in ontology.nodes().values()]
    tables += [(spec.rel, spec.props) for spec in ontology.edges().values()]
    for table, props in tables:
        live = {row["name"] for row in query(conn, f"CALL table_info('{table}') RETURN *")}
        for prop in props:
            if prop.name not in live:
                conn.execute(f"ALTER TABLE {table} ADD {prop.name} {prop.kuzu_type}")


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
    if isinstance(result, list):  # kuzu returns a list only for `;`-chained statements
        result = result[-1]
    columns = result.get_column_names()
    rows: list[dict[str, Any]] = []
    while result.has_next():
        rows.append(
            {col: _plain(val) for col, val in zip(columns, result.get_next(), strict=True)}
        )
    return rows


#: UNWIND batch size. One MERGE per row was the deep tier's 25-minute IGO
#: load; batched it is under a second per ten thousand — same statements,
#: same semantics, three hundred times fewer round trips.
_BATCH = 1000


def _batches(rows: list[dict[str, Any]], signature: Any) -> list[list[dict[str, Any]]]:
    """Group rows by their column signature (UNWIND structs need uniform
    fields), then chunk. Deterministic order — grouped in first-seen order.

    None-valued keys are DROPPED first: a struct field that is None in every
    row of a chunk has no inferable type, and "absent" merges as
    leave-unset — which for idempotent loaders re-merging the same facts is
    the behavior that cannot destroy a value another writer set."""
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        cleaned = {k: v for k, v in row.items() if v is not None}
        grouped.setdefault(signature(cleaned), []).append(cleaned)
    out: list[list[dict[str, Any]]] = []
    for group in grouped.values():
        out.extend(group[i : i + _BATCH] for i in range(0, len(group), _BATCH))
    return out


def merge_nodes(conn: kuzu.Connection, table: str, rows: list[dict[str, Any]]) -> int:
    """Upsert nodes by node_id. Validates every row against the ontology."""
    spec = ontology.nodes().get(table)
    if spec is None:
        raise ontology.OntologyError(f"{table!r} is not a node table.")
    prop_names = [p.name for p in spec.props]
    for row in rows:
        ontology.validate_node(table, row)
    written = 0
    for batch in _batches(rows, lambda r: tuple(n for n in prop_names if n in r)):
        present = [n for n in prop_names if n in batch[0]]
        sets = ", ".join(f"n.{name} = row.{name}" for name in present)
        cypher = f"UNWIND $rows AS row MERGE (n:{table} {{node_id: row.node_id}})"
        if sets:
            cypher += f" ON CREATE SET {sets} ON MATCH SET {sets}"
        payload = [
            {"node_id": row["node_id"], **{n: row[n] for n in present}} for row in batch
        ]
        conn.execute(cypher, parameters={"rows": payload})
        written += len(batch)
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
    for row in rows:
        ontology.validate_edge(rel, {k: v for k, v in row.items() if k not in ("src", "dst")})
    written = 0
    for batch in _batches(rows, lambda r: tuple(sorted(k for k in r if k not in ("src", "dst")))):
        props = [k for k in batch[0] if k not in ("src", "dst")]
        keys = [k for k in spec.key_slots if k in props]
        rest = [k for k in props if k not in keys]
        key_pattern = (" {" + ", ".join(f"{k}: row.{k}" for k in keys) + "}") if keys else ""
        sets = ", ".join(f"r.{k} = row.{k}" for k in rest)
        cypher = (
            f"UNWIND $rows AS row "
            f"MATCH (a:{spec.src} {{node_id: row.src}}), (b:{spec.dst} {{node_id: row.dst}}) "
            f"MERGE (a)-[r:{rel}{key_pattern}]->(b)"
        )
        if sets:
            cypher += f" ON CREATE SET {sets} ON MATCH SET {sets}"
        conn.execute(cypher, parameters={"rows": batch})
        written += len(batch)
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
