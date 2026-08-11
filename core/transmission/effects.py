"""Where measured effects become graph structure.

The one-way door between the numeric store and the graph: EffectResults
computed by event_study.py land as AFFECTED edges through the validated write
path. Nothing else writes AFFECTED — that is what keeps every number on the
money edge deterministic and reproducible.
"""

from __future__ import annotations

import kuzu

from core.graph import kuzu_store
from core.transmission.event_study import EffectResult


def write_effects(
    conn: kuzu.Connection,
    results: list[EffectResult],
    *,
    market_node_ids: dict[str, str],
    source_id: str,
) -> int:
    """Persist results as AFFECTED edges.

    `market_node_ids` maps ticker → Market node_id; `source_id` names the
    Source of the PRICE SERIES the effects were computed from (the provenance
    requirement on AFFECTED is about the data the number came from — the
    event's own provenance lives on DERIVED_FROM).
    """
    rows = [
        {
            "src": result.event_node_id,
            "dst": market_node_ids[result.market_ticker],
            "window": result.window,
            "resolution": result.resolution,
            "raw_return": result.raw_return,
            "expected_return": result.expected_return,
            "abnormal_return": result.abnormal_return,
            "t_stat": result.t_stat,
            "p_value": result.p_value,
            "first_mover": result.first_mover,
            "overlapping": result.overlapping,
            "method": result.method,
            "source_id": source_id,
        }
        for result in results
    ]
    return kuzu_store.merge_edges(conn, "AFFECTED", rows)
