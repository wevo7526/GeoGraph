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


@router.get("/forecasts/{node_id:path}/paper")
def paper_book(request: Request, node_id: str) -> dict[str, Any]:
    """The frozen forecast's market implications as a marked paper book.

    A mechanical, unfitted translation (core/reasoning/paper.py — the rule is
    in the payload's method string): positions weighted by the frozen
    base-rate likelihood, entered at the first panel close after the data
    cutoff, marked at the latest close. Needs the panel; without it the
    answer is 503 and says so, never an invented fill.
    """
    import json as json_module

    from core import settings as settings_module
    from core.panel import pg_store
    from core.reasoning import paper

    conn = _conn(request)
    rows = kuzu_store.query(
        conn,
        "MATCH (f:Forecast {node_id: $id}) RETURN f.mode AS mode, "
        "f.scenarios_json AS scenarios_json, f.frozen_inputs_json AS frozen_inputs_json",
        {"id": node_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"no such forecast: {node_id}")
    if rows[0]["mode"] != "near_term":
        raise HTTPException(
            status_code=400,
            detail=(
                "the paper book translates the near-term mode only — "
                "long-horizon output carries no likelihoods, by design."
            ),
        )
    scenarios = json_module.loads(rows[0]["scenarios_json"] or "[]")
    frozen = json_module.loads(rows[0]["frozen_inputs_json"] or "{}")
    entry_after = str(frozen.get("as_of") or "")
    likelihood, net = paper.build_book(scenarios)

    settings = settings_module.load()
    try:
        panel = pg_store.connect(settings)
    except pg_store.PanelUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        series = {
            ticker: pg_store.series(
                panel, ticker, start=entry_after, end="2100-01-01"
            )
            for ticker in net
        }
    finally:
        panel.close()
    report = paper.mark_book(net, series, entry_after=entry_after)
    return {
        "forecast": node_id,
        "escalation_likelihood": likelihood,
        "entry_after": entry_after,
        **report,
    }


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
