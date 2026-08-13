"""Dyads as time series — what the reasoning page draws.

Served under /api/panel/ rather than /api/dyads/, which events.py already
owns: that endpoint lists dyads with their standing escalation baselines,
this one projects them into the quarterly panel the forecaster is fitted on.
Two different views of the same nodes, and one route each.

One dyad's escalation intensity per quarter, and the roster of dyads worth
asking about. Computed from the graph on read rather than persisted: it is a
projection of Event rows, not a new fact, and persisting a projection is how
two copies of the same number start to disagree.

The read is cached per (graph, region) for the process lifetime — the panel
is a scan over every dyad-coded event, and the page asks for it on every
navigation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from core.models import panel as panel_module

router = APIRouter(tags=["panel"])

#: Process-lifetime cache. The graph is single-writer and the API holds the
#: lock, so within one process the underlying events cannot change.
_CACHE: dict[str, list[dict[str, Any]]] = {}


def _panel(request: Request, region: str | None) -> list[dict[str, Any]]:
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    key = region or "*"
    if key not in _CACHE:
        rows = panel_module.dyad_event_rows(conn)
        _CACHE[key] = panel_module.build(rows, region_pack=region)
    return _CACHE[key]


@router.get("/panel/dyads")
def list_dyads(
    request: Request,
    region: str | None = None,
    limit: int = Query(40, ge=1, le=200),
) -> dict[str, Any]:
    """The dyads the archive has actually watched, most-watched first.

    `active_quarters` is the evidence bar in the open: a dyad with four
    active quarters and one with two hundred both get a forecast, and the
    reader is entitled to know which is which before reading either.
    """
    table = _panel(request, region)
    rows = panel_module.dyad_summary(table)
    return {"rows": rows[:limit], "total": len(rows), "region": region}


@router.get("/panel/dyads/{dyad_id:path}/series")
def dyad_series(
    request: Request, dyad_id: str, region: str | None = None
) -> dict[str, Any]:
    """One dyad's quarterly intensity — the arc the page opens on.

    Quiet quarters are present with intensity 0. They are the majority of any
    dyad's history and dropping them would draw a line through only the
    dyad's worst moments, which reads as permanent crisis.
    """
    table = _panel(request, region)
    rows = panel_module.series_for(table, dyad_id)
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no series for {dyad_id} — either the dyad is unknown or it has "
                f"fewer than {panel_module.MIN_OCCUPIED_QUARTERS} occupied quarters, "
                "which is too little history to normalise against."
            ),
        )
    active = [r for r in rows if r["intensity"] > 0.0]
    return {
        "dyad_id": dyad_id,
        "dyad_name": rows[0]["dyad_name"],
        "rows": rows,
        "active_quarters": len(active),
        "peak": max(r["intensity"] for r in rows),
        "span": [rows[0]["date"], rows[-1]["date"]],
    }
