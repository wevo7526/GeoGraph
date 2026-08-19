"""Network metrics over time windows — computed by graph/analytics.py
(build-spec section 12), read here. The API never computes a metric at
request time: the numbers are persisted, dated and windowed, so two callers
asking the same question get the same answer."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from core import packs
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
    region: str | None = None,
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
    roster: set[str] = set()
    if region:
        try:
            roster = {str(a["id"]) for a in packs.load(region).actors}
        except packs.PackError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
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
    if roster:
        rows = [row for row in rows if row["subject_id"] in roster]
    return {"rows": rows[:limit], "truncated": len(rows) > limit}


@router.get("/network/snapshot")
def snapshot(
    request: Request,
    region: str = "mena",
    top: int = Query(8, ge=1, le=20),
) -> dict[str, Any]:
    """The latest computed window, ranked for this lens's roster.

    The API never computes a metric here. An empty brokers list means no
    window has been persisted yet — run the metrics job — not that the
    network has no centre.
    """
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    try:
        pack = packs.load(region)
    except packs.PackError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    names = {str(a["id"]): str(a["name"]) for a in pack.actors}
    roster = set(names)

    latest = kuzu_store.query(
        conn,
        "MATCH (m:NetworkMetric) "
        "RETURN m.window_start AS window_start, m.window_end AS window_end "
        "ORDER BY m.window_end DESC LIMIT 1",
    )
    if not latest:
        return {
            "region": region,
            "region_label": pack.label,
            "window_start": None,
            "window_end": None,
            "brokers": [],
            "holes": [],
            "degree": [],
            "communities": [],
            "note": "no NetworkMetric window has been computed yet",
        }
    window_start = latest[0]["window_start"]
    window_end = latest[0]["window_end"]
    rows = kuzu_store.query(
        conn,
        "MATCH (m:NetworkMetric) "
        "WHERE m.window_start = $start_date AND m.window_end = $end_date "
        "RETURN m.subject_id AS subject_id, m.metric_name AS metric_name, "
        "m.value AS value, m.method AS method "
        "ORDER BY m.metric_name, m.subject_id",
        {"start_date": window_start, "end_date": window_end},
    )

    def ranked(metric: str, *, reverse: bool) -> list[dict[str, Any]]:
        picked = [
            row for row in rows
            if row["metric_name"] == metric and row["subject_id"] in roster
            and row.get("value") is not None
        ]
        picked.sort(key=lambda row: (float(row["value"]), row["subject_id"]), reverse=reverse)
        out = []
        for row in picked[:top]:
            out.append({
                "subject_id": row["subject_id"],
                "name": names[row["subject_id"]],
                "value": float(row["value"]),
            })
        return out

    by_community: dict[int, list[str]] = {}
    for row in rows:
        if row["metric_name"] != "community" or row["subject_id"] not in roster:
            continue
        label = int(float(row["value"]))
        by_community.setdefault(label, []).append(names[row["subject_id"]])
    communities = [
        {"id": label, "members": sorted(members), "size": len(members)}
        for label, members in sorted(by_community.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]
    return {
        "region": region,
        "region_label": pack.label,
        "window_start": window_start,
        "window_end": window_end,
        "brokers": ranked("betweenness", reverse=True),
        "holes": ranked("constraint", reverse=False),
        "degree": ranked("degree", reverse=True),
        "communities": communities[:6],
        "n": sum(1 for row in rows if row["subject_id"] in roster),
        "method": (
            "persisted NetworkMetric for the latest window, clipped to this "
            "pack's roster; betweenness ranks who sits between others, "
            "constraint (Burt) ranks who has room to broker, degree ranks "
            "who is most connected"
        ),
    }
