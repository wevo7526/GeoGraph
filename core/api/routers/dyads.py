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
from core.wire import serving

router = APIRouter(tags=["panel"])

#: Process-lifetime cache for the GRAPH fallback path. The graph is
#: single-writer and the API holds the lock, so within one process the
#: underlying events cannot change.
_CACHE: dict[str, list[dict[str, Any]]] = {}


def _panel(request: Request, region: str | None) -> list[dict[str, Any]]:
    # THE CORPUS FIRST. The wire lives in the image's artifacts, parsed and
    # scored once per process by `serving.warm` — so the panel no longer
    # depends on the graph holding a loaded, rescored archive. The graph path
    # below is the fallback for a build without artifacts, not a peer: on
    # 2026-08-13 a rebuilt volume held 817k events but only the spine's 55
    # OF_DYAD edges (the rescore writes those, and it had not run), and this
    # page served an empty ledger while claiming the archive was watched.
    table = serving.table(region)
    if table is not None:
        return table

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


@router.get("/relationships/narrative")
def relationship_narrative(region: str, dyad: str) -> dict[str, Any]:
    """The desk's AI narrative for one relationship (History / Work / Forecast),
    served from the persisted store — no model call at read time. When nothing
    is persisted (no key, or not generated yet), returns available=false and the
    page renders its deterministic prose."""
    from core import settings as settings_module
    from core.panel import pg_store
    from core.reasoning import narrative as narrative_module

    try:
        panel = pg_store.connect(settings_module.load())
    except pg_store.PanelUnavailable:
        return {"region": region, "dyad": dyad, "available": False}
    try:
        pg_store.apply_schema(panel)
        narr = narrative_module.served_narrative(
            panel, surface="relationship", region=region, subject_id=dyad,
        )
    finally:
        panel.close()
    if narr is None:
        return {"region": region, "dyad": dyad, "available": False}
    return {"region": region, "dyad": dyad, "available": True, "narrative": narr}


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
    from core.wire import live as live_overlay

    live_rows = live_overlay.rows_for(region or "", dyad_id)
    if live_rows:
        rows = live_overlay.apply_to_own(rows, live_rows)
    active = [r for r in rows if r["intensity"] > 0.0]
    return {
        "dyad_id": dyad_id,
        "dyad_name": rows[0]["dyad_name"],
        "rows": rows,
        "active_quarters": len(active),
        "peak": max(r["intensity"] for r in rows),
        "span": [rows[0]["date"], rows[-1]["date"]],
    }
