"""Network metrics over time windows — computed by graph/analytics.py
(build-spec section 12), read here. The API never computes a metric at
request time: the numbers are persisted, dated and windowed, so two callers
asking the same question get the same answer."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from core.graph import kuzu_store

router = APIRouter(tags=["network"])

#: NetworkMetric rows are one per (metric, actor, window); the cap exists so a
#: fully-computed 120-year archive cannot return itself in one response.
MAX_ROWS = 2000


@router.get("/network/metrics")
def metrics(
    request: Request,
    window_start: str | None = None,
    window_end: str | None = None,
    subject: str | None = None,
    metric: str | None = None,
    limit: int = Query(MAX_ROWS, ge=1, le=MAX_ROWS),
) -> dict[str, Any]:
    """Persisted structural measures, filtered by window, subject or metric.

    An empty result means no window has been COMPUTED that matches — never
    that the network has no structure. Run scripts/run_network_metrics.py
    (or reboot the container) to fill the standard windows.
    """
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit + 1}
    if window_start:
        clauses.append("m.window_start = $start_date")
        params["start_date"] = window_start
    if window_end:
        clauses.append("m.window_end = $end_date")
        params["end_date"] = window_end
    if subject:
        clauses.append("m.subject_id = $subject")
        params["subject"] = subject
    if metric:
        clauses.append("m.metric_name = $metric")
        params["metric"] = metric
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    rows = kuzu_store.query(
        conn,
        f"MATCH (m:NetworkMetric) {where}"
        "RETURN m.subject_id AS subject_id, m.metric_name AS metric_name, "
        "m.value AS value, m.window_start AS window_start, "
        "m.window_end AS window_end, m.method AS method "
        "ORDER BY m.window_start, m.metric_name, m.subject_id LIMIT $limit",
        params,
    )
    return {"rows": rows[:limit], "truncated": len(rows) > limit}
