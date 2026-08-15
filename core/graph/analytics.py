"""Network analytics over time-windowed subgraphs — build-spec section 12.

The Square-and-the-Tower payload: export actors, RELATES_TO structure and
event flow for a window from Kuzu into networkx, compute centrality, brokerage
(betweenness, structural holes / Burt's constraint) and community detection,
and persist every number to `NetworkMetric` nodes — dated, windowed, with the
method string that makes it reproducible.

DETERMINISM RULE (build-spec section 17): the AI never originates a number
that lands in NetworkMetric. This module and the graph are the only writers.
Everything here is a pure function of the graph and the window — node order
is sorted before every algorithm so ties break the same way every run, and
metric node_ids embed the window so a re-run MERGEs onto itself.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx

from core.graph import kuzu_store


@dataclass(frozen=True)
class Window:
    """A time slice of the network. Regime-aligned windows come from
    `core.reasoning.regimes`; decade windows are the explorer's slider."""

    start: str  # ISO-8601
    end: str


def _alive(row: dict[str, Any], window: Window) -> bool:
    """COW state-system membership respected: a state outside its window is
    not in the network. Actors without membership dates (orgs, funds — and
    states the deep tier has not dated yet) are treated as present."""
    state_from = str(row.get("state_from") or "")
    state_to = str(row.get("state_to") or "")
    if state_from and state_from > window.end:
        return False
    return not (state_to and state_to < window.start)


def _subgraph(conn: Any, window: Window) -> nx.Graph:
    """The window's network from an already-open connection.

    Two edge layers on one graph: `relates` (durable RELATES_TO structure
    valid in the window) and `flow` (dyadic event counts inside it). ISO
    strings compare lexically, so the range logic is correct whether a date
    is known to the day or only to the year.
    """
    graph = nx.Graph()

    actors = kuzu_store.query(
        conn,
        "MATCH (a:Actor) RETURN a.node_id AS node_id, a.name AS name, "
        "a.actor_type AS actor_type, a.state_from AS state_from, a.state_to AS state_to "
        "ORDER BY a.node_id",
    )
    for row in actors:
        if _alive(row, window):
            graph.add_node(row["node_id"], name=row["name"], actor_type=row["actor_type"])

    relates = kuzu_store.query(
        conn,
        "MATCH (a:Actor)-[r:RELATES_TO]->(b:Actor) "
        "RETURN a.node_id AS a_id, b.node_id AS b_id, r.relation_type AS relation_type, "
        "r.valid_from AS valid_from, r.valid_to AS valid_to "
        "ORDER BY a_id, b_id, relation_type",
    )
    for row in relates:
        valid_from = str(row.get("valid_from") or "")
        valid_to = str(row.get("valid_to") or "")
        if valid_from and valid_from > window.end:
            continue
        if valid_to and valid_to < window.start:
            continue
        if graph.has_node(row["a_id"]) and graph.has_node(row["b_id"]):
            graph.add_edge(row["a_id"], row["b_id"], layer="relates",
                           relation_type=row["relation_type"])

    # count() only — the Kuzu trap: count(DISTINCT x) and sum(y) in one RETURN
    # yields a NULL sum, so flow weight is the event count, full stop.
    flow = kuzu_store.query(
        conn,
        "MATCH (i:Actor)<-[:INITIATED_BY]-(e:Event)-[:DIRECTED_AT]->(t:Actor) "
        "WHERE e.event_time >= $start_date AND e.event_time <= $end_date "
        "AND i.node_id <> t.node_id "
        "RETURN i.node_id AS a_id, t.node_id AS b_id, count(*) AS events "
        "ORDER BY a_id, b_id",
        {"start_date": window.start, "end_date": window.end},
    )
    for row in flow:
        if not (graph.has_node(row["a_id"]) and graph.has_node(row["b_id"])):
            continue
        if graph.has_edge(row["a_id"], row["b_id"]):
            graph[row["a_id"]][row["b_id"]]["events"] = int(row["events"])
        else:
            graph.add_edge(row["a_id"], row["b_id"], layer="flow", events=int(row["events"]))

    return graph


def export_subgraph(db_path: Path, window: Window) -> nx.Graph:
    """Actors alive in the window (COW state-system membership respected),
    RELATES_TO edges valid in it, and event flow between them, as a networkx
    graph. Read-only connection — analytics never writes mid-export."""
    conn = kuzu_store.connect(db_path, read_only=True)
    try:
        return _subgraph(conn, window)
    finally:
        kuzu_store.close(conn)


def _metric_rows(graph: nx.Graph, window: Window) -> list[dict[str, Any]]:
    """Every metric for every connected actor in the window, as NetworkMetric
    node rows. Pure — no I/O, so the numbers are testable exactly."""
    version = nx.__version__
    span = f"{window.start}..{window.end}"

    def row(metric: str, subject: str, value: float, method: str) -> dict[str, Any]:
        return {
            # The window is part of the IDENTITY: the same measure over two
            # windows is two facts, and a re-run of one window merges onto
            # itself instead of multiplying.
            "node_id": f"metric:{metric}:{subject}:{span}",
            "subject_id": subject,
            "metric_name": metric,
            "value": float(value),
            "window_start": window.start,
            "window_end": window.end,
            "method": method,
        }

    rows: list[dict[str, Any]] = []
    # Isolates carry no structural information for this window; measuring
    # them would persist a wall of zeros that reads as data.
    connected = graph.subgraph(n for n in sorted(graph.nodes) if graph.degree(n) > 0)
    if connected.number_of_nodes() == 0:
        return rows

    degree = nx.degree_centrality(connected)
    for subject in sorted(degree):
        rows.append(row("degree", subject, degree[subject],
                        f"networkx {version} degree_centrality"))

    betweenness = nx.betweenness_centrality(connected, normalized=True)
    for subject in sorted(betweenness):
        rows.append(row("betweenness", subject, betweenness[subject],
                        f"networkx {version} betweenness_centrality normalized"))

    try:
        eigen = nx.eigenvector_centrality_numpy(connected)
        for subject in sorted(eigen):
            rows.append(row("eigenvector", subject, eigen[subject],
                            f"networkx {version} eigenvector_centrality_numpy"))
    except (nx.NetworkXException, ValueError, TypeError):
        # A graph the eigensolver cannot rank gets no eigenvector rows rather
        # than wrong ones: degenerate spectra, and scipy's sparse path raising
        # TypeError outright when the window's graph has fewer than three
        # nodes (k >= N - 1).
        pass

    constraint = nx.constraint(connected)
    for subject in sorted(constraint):
        value = constraint[subject]
        if value is None or math.isnan(value):
            continue
        rows.append(row("constraint", subject, value,
                        f"networkx {version} constraint (Burt structural holes)"))

    communities = nx.community.greedy_modularity_communities(connected)
    # Deterministic labels: communities ordered by size then smallest member,
    # so the same partition always gets the same numbering.
    ordered = sorted(communities, key=lambda c: (-len(c), min(c)))
    for label, members in enumerate(ordered):
        for subject in sorted(members):
            rows.append(row("community", subject, float(label),
                            f"networkx {version} greedy_modularity_communities"))

    return rows


def _write_metrics(conn: Any, window: Window) -> int:
    """Compute and persist one window's metrics over an ALREADY-OPEN write
    connection. The core of compute_metrics/compute_windows."""
    rows = _metric_rows(_subgraph(conn, window), window)
    if rows:
        kuzu_store.merge_nodes(conn, "NetworkMetric", rows)
    return len(rows)


def compute_metrics(db_path: Path, window: Window) -> int:
    """Degree, betweenness, eigenvector centrality, Burt's constraint
    (structural holes), and community/coalition detection over the window's
    subgraph. Persists NetworkMetric nodes via kuzu_store.merge_nodes and
    returns how many were written.

    ONE write connection for both the read and the write: Kuzu is
    single-writer, and opening the graph twice from one process is how the
    8 TiB address-space reservations pile up."""
    conn = kuzu_store.connect(db_path)
    try:
        return _write_metrics(conn, window)
    finally:
        kuzu_store.close(conn)


def compute_windows(db_path: Path, windows: list[Window]) -> list[tuple[Window, int]]:
    """Every window over ONE open connection — the boot's path. Opening the
    graph once instead of per window avoids the 20-40 cold opens (and their
    8 TiB reservations) the standard decade+regime set would otherwise pay."""
    conn = kuzu_store.connect(db_path)
    try:
        return [(window, _write_metrics(conn, window)) for window in windows]
    finally:
        kuzu_store.close(conn)


def regime_shift_report(db_path: Path) -> list[dict[str, Any]]:
    """How each actor's structural position moves across the windows that have
    been computed — the 120-year reconfiguration the time slider animates.
    Reads PERSISTED metrics only; computing belongs to compute_metrics."""
    conn = kuzu_store.connect(db_path, read_only=True)
    try:
        rows = kuzu_store.query(
            conn,
            "MATCH (m:NetworkMetric) "
            "RETURN m.subject_id AS subject_id, m.metric_name AS metric_name, "
            "m.window_start AS window_start, m.window_end AS window_end, "
            "m.value AS value ORDER BY subject_id, metric_name, window_start",
        )
    finally:
        kuzu_store.close(conn)

    report: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["subject_id"], row["metric_name"])
        entry = report.setdefault(
            key, {"subject_id": key[0], "metric_name": key[1], "windows": []}
        )
        entry["windows"].append(
            {"start": row["window_start"], "end": row["window_end"], "value": row["value"]}
        )
    return [report[key] for key in sorted(report)]
