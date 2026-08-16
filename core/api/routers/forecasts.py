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
    "f.brier_score AS brier_score, f.retrodiction_json AS retrodiction_json"
)


def _with_retrodiction(row: dict[str, Any]) -> dict[str, Any]:
    """retrodiction_json → a parsed `retrodiction` (or None): the API serves
    structures, not double-encoded strings."""
    out = dict(row)
    raw = out.pop("retrodiction_json", None)
    out["retrodiction"] = json.loads(raw) if raw else None
    return out


def _conn(request: Request) -> Any:
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    return conn


#: The walk is a pure function of the corpus+graph rows, which are immutable
#: for the life of a process (the corpus ships in the image; the graph's wire
#: grows only as the loader writes). ~5s per region, so it is computed once
#: and kept — the same posture as `core/wire/serving`.
_WALK_CACHE: dict[str, dict[str, Any]] = {}


@router.get("/forecasts/calibration")
def calibration_walk(request: Request, region: str = "mena") -> dict[str, Any]:
    """THE SCOREBOARD, and it exists today rather than in 2029.

    The near-term forecast asks a three-year question, so nothing frozen this
    week can be Brier-scored this week — on 2026-08-15 every frozen forecast
    carried a null score and would have for three more years. This re-runs the
    SAME estimator at every historical quarter-end whose horizon has since
    closed and scores it against what the archive then recorded.

    Read the `recent` block before the headline: the archive's density has
    moved twice, and the whole-walk number is dominated by a sparse deep past
    where near-zero calls were easy to get right.
    """
    from core.reasoning import calibration, forecasting

    if region in _WALK_CACHE:
        return _WALK_CACHE[region]
    settings = request.app.state.settings
    try:
        rows = forecasting.all_dyad_event_rows(settings.kuzu_db_path)
    except Exception as exc:  # noqa: BLE001 - the corpus alone still answers
        from core.wire import corpus

        if not corpus.installed():
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        rows = corpus.forecast_rows()
    out = calibration.walk(rows, region_pack=region)
    _WALK_CACHE[region] = out
    return out


@router.get("/forecasts")
def list_forecasts(
    request: Request, mode: str | None = None, region: str | None = None
) -> dict[str, Any]:
    """Every frozen forecast, newest first — the trail calibration scores.

    An empty list means nothing has been FROZEN yet (run
    scripts/run_forecasts.py or reboot the container), never that the future
    is unforecastable.
    """
    conn = _conn(request)
    clauses = []
    params: dict[str, Any] = {}
    if mode:
        clauses.append("f.mode = $mode")
        params["mode"] = mode
    if region:
        clauses.append("f.region_pack = $region")
        params["region"] = region
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    rows = kuzu_store.query(
        conn,
        f"MATCH (f:Forecast) {where}RETURN {_SUMMARY_COLUMNS} "
        "ORDER BY f.generated_at DESC, f.node_id",
        params,
    )
    return {"rows": [_with_retrodiction(row) for row in rows]}


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

    from core import packs
    from core import settings as settings_module
    from core.panel import pg_store
    from core.reasoning import paper

    conn = _conn(request)
    rows = kuzu_store.query(
        conn,
        "MATCH (f:Forecast {node_id: $id}) RETURN f.mode AS mode, "
        "f.region_pack AS region_pack, f.scenarios_json AS scenarios_json, "
        "f.frozen_inputs_json AS frozen_inputs_json",
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
    # The books are the REGION'S OWN translation, read from the forecast's
    # pack: a hardcoded book here would trade Brent on a Taiwan forecast.
    region = str(rows[0]["region_pack"] or "")
    try:
        books = packs.load(region).paper_books
    except packs.PackError:
        books = None
    if books is None:
        raise HTTPException(
            status_code=404,
            detail=f"packs/{region} declares no paper_books — no paper model.",
        )
    scenarios = json_module.loads(rows[0]["scenarios_json"] or "[]")
    frozen = json_module.loads(rows[0]["frozen_inputs_json"] or "{}")
    entry_after = str(frozen.get("as_of") or "")
    likelihood, net = paper.build_book(
        scenarios,
        escalation_book=books["escalation"],
        reversion_book=books["reversion"],
    )

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
        "method": paper.method_for(books["escalation"], books["reversion"]),
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
    row = _with_retrodiction(rows[0])
    row["scenarios"] = json.loads(row.pop("scenarios_json") or "[]")
    row["frozen_inputs"] = json.loads(row.pop("frozen_inputs_json") or "{}")
    return row
