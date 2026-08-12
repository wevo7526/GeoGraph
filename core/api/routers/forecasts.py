"""Forecasts and scenarios — the reasoning layer's read surface (build-spec
sections 13, 14).

Persisted Forecast nodes only, frozen at generation with their inputs: the
API never computes a forecast at request time, so two callers see the same
frozen call and calibration can score it later. Near-term rows carry
base-rate likelihoods; long-horizon rows ALWAYS carry the boundary statement
(structural pressure over windows, never dated point predictions).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core.graph import kuzu_store

router = APIRouter(tags=["forecasts"])

_SUMMARY_COLUMNS = (
    "f.node_id AS node_id, f.mode AS mode, f.region_pack AS region_pack, "
    "f.question AS question, f.generated_at AS generated_at, "
    "f.horizon_end AS horizon_end, f.boundary_statement AS boundary_statement, "
    "f.brier_score AS brier_score"
)


def _conn(request: Request) -> Any:
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    return conn


@router.get("/forecasts")
def list_forecasts(request: Request, mode: str | None = None) -> dict[str, Any]:
    """Every frozen forecast, newest first — the trail calibration scores.

    An empty list means nothing has been FROZEN yet (run
    scripts/run_forecasts.py or reboot the container), never that the future
    is unforecastable.
    """
    conn = _conn(request)
    where = "WHERE f.mode = $mode " if mode else ""
    params: dict[str, Any] = {"mode": mode} if mode else {}
    rows = kuzu_store.query(
        conn,
        f"MATCH (f:Forecast) {where}RETURN {_SUMMARY_COLUMNS} "
        "ORDER BY f.generated_at DESC, f.node_id",
        params,
    )
    return {"rows": rows}


@router.get("/forecasts/{node_id:path}")
def get_forecast(request: Request, node_id: str) -> dict[str, Any]:
    """One frozen forecast, whole: scenarios and the inputs it froze —
    enough to recount every likelihood from the archive."""
    conn = _conn(request)
    rows = kuzu_store.query(
        conn,
        f"MATCH (f:Forecast {{node_id: $id}}) RETURN {_SUMMARY_COLUMNS}, "
        "f.scenarios_json AS scenarios_json, f.frozen_inputs_json AS frozen_inputs_json",
        {"id": node_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"no such forecast: {node_id}")
    row = dict(rows[0])
    row["scenarios"] = json.loads(row.pop("scenarios_json") or "[]")
    row["frozen_inputs"] = json.loads(row.pop("frozen_inputs_json") or "{}")
    return row
