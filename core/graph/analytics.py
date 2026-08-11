"""Network analytics over time-windowed subgraphs — build-spec section 12.

The Square-and-the-Tower payload: export actors, RELATES_TO structure and
event flow for a window from Kuzu into networkx, compute centrality, brokerage
(betweenness, structural holes / Burt's constraint) and community detection,
and persist every number to `NetworkMetric` nodes — dated, windowed, with the
method string that makes it reproducible.

DETERMINISM RULE (build-spec section 17): the AI never originates a number
that lands in NetworkMetric. This module and the graph are the only writers.

PHASE 2. The signatures below are the contract the API and MCP layers are
built against; the bodies land with the phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Window:
    """A time slice of the network. Regime-aligned windows come from
    `core.reasoning.regimes`; decade windows are the explorer's slider."""

    start: str  # ISO-8601
    end: str


def export_subgraph(db_path: Path, window: Window):  # -> networkx.Graph
    """Actors alive in the window (COW state-system membership respected),
    RELATES_TO edges valid in it, and event flow between them, as a networkx
    graph. Read-only connection — analytics never writes mid-export."""
    raise NotImplementedError("Phase 2 — see docs/build-spec.md section 12")


def compute_metrics(db_path: Path, window: Window) -> int:
    """Degree, betweenness, eigenvector centrality, Burt's constraint
    (structural holes), and community/coalition detection over the window's
    subgraph. Persists NetworkMetric nodes via kuzu_store.merge_nodes and
    returns how many were written."""
    raise NotImplementedError("Phase 2 — see docs/build-spec.md section 12")


def regime_shift_report(db_path: Path) -> list[dict]:
    """How each actor's structural position moves across regime boundaries and
    decades — the 120-year reconfiguration the time slider animates."""
    raise NotImplementedError("Phase 2 — see docs/build-spec.md section 12")
