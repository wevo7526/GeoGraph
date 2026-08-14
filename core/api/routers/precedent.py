"""Precedent — what followed, the times this happened before.

The counted half of the forecast, and deliberately independent of the model.
Given a dyad and a moment, this finds that dyad's comparable past episodes
and reports two things measured rather than predicted:

  1. what the dyad's intensity DID over the following quarters, and
  2. what the markets DID, from the AFFECTED edges the transmission engine
     measured for the events in those episodes.

Regime-gated throughout (`regimes.comparable` is an admissibility test, not a
score), so a 2025 question is never answered with Bretton Woods evidence.

Both halves come from ONE query pass, because a chart of episodes beside a
chart of their market effects must be showing the same episodes — computing
them separately is how two panels start quietly disagreeing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from core.models import panel as panel_module
from core.reasoning import regimes
from core.transmission import effects as effects_module
from core.wire import serving

router = APIRouter(tags=["precedent"])

#: Quarters of aftermath reported for each precedent episode.
_AFTERMATH_QUARTERS = 8
#: An episode opens when a quarter's intensity clears this share of the
#: dyad's own peak — relative, because a departure that is routine for a
#: rivalry is a rupture for a quiet pair.
_EPISODE_SHARE = 0.5


def _conn(request: Request) -> Any:
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    return conn


def _quantiles(values: list[float]) -> dict[str, float]:
    """Five-number summary. A distribution, never a mean: the point of
    showing precedent is the SPREAD of what followed, and an average over
    two quiet aftermaths and one war describes none of the three."""
    ordered = sorted(values)

    def at(share: float) -> float:
        if not ordered:
            return 0.0
        return ordered[min(len(ordered) - 1, int(len(ordered) * share))]

    return {
        "min": ordered[0], "p25": at(0.25), "median": at(0.5),
        "p75": at(0.75), "max": ordered[-1],
    }


@router.get("/precedent")
def precedent(
    request: Request,
    dyad: str,
    region: str | None = None,
    as_of: str | None = Query(None, description="ISO date; defaults to the dyad's last quarter"),
) -> dict[str, Any]:
    """This dyad's comparable past episodes, and what followed each."""
    # The series comes CORPUS FIRST, like every other bulk read of the wire;
    # the graph stays required for the measured effects below, which only the
    # transmission engine writes and only into the graph.
    table = serving.table(region)
    if table is None:
        table = panel_module.build(
            panel_module.dyad_event_rows(_conn(request)), region_pack=region
        )
    rows = panel_module.series_for(table, dyad)
    if not rows:
        raise HTTPException(
            status_code=404, detail=f"no series for {dyad} — too little history to compare"
        )

    anchor = as_of or rows[-1]["date"]
    peak = max(r["intensity"] for r in rows)
    threshold = peak * _EPISODE_SHARE
    by_index = {r["q"]: i for i, r in enumerate(rows)}

    episodes: list[dict[str, Any]] = []
    previous_hot = False
    for row in rows:
        hot = row["intensity"] >= threshold and row["intensity"] > 0.0
        # An episode is the START of a hot run, not every hot quarter: a
        # six-quarter war counted six times would drown the sample in one war.
        if not hot or previous_hot:
            previous_hot = hot
            continue
        previous_hot = hot
        if not regimes.comparable(anchor, row["date"]):
            continue
        if row["date"] >= anchor:
            continue
        aftermath = [
            {
                "offset": k,
                "date": rows[by_index[row["q"] + k]]["date"],
                "intensity": rows[by_index[row["q"] + k]]["intensity"],
            }
            for k in range(0, _AFTERMATH_QUARTERS + 1)
            if row["q"] + k in by_index
        ]
        if len(aftermath) < _AFTERMATH_QUARTERS + 1:
            continue  # an episode whose aftermath runs off the archive proves nothing
        episodes.append({
            "date": row["date"],
            "intensity": row["intensity"],
            "aftermath": aftermath,
        })

    # The fan: intensity at each offset across every admissible episode.
    fan = []
    for k in range(0, _AFTERMATH_QUARTERS + 1):
        values = [
            e["aftermath"][k]["intensity"] for e in episodes
            if k < len(e["aftermath"])
        ]
        if values:
            fan.append({"offset": k, "n": len(values), **_quantiles(values)})

    effects = effects_module.effects_for_dyad(_conn(request), dyad)
    by_market: dict[str, dict[str, Any]] = {}
    for effect in effects:
        if effect["abnormal_return"] is None:
            continue
        if not regimes.comparable(anchor, str(effect["event_time"])):
            continue
        entry = by_market.setdefault(
            str(effect["market_id"]),
            {"market_id": effect["market_id"], "market_name": effect["market_name"],
             "values": [], "windows": set()},
        )
        entry["values"].append(float(effect["abnormal_return"]))
        entry["windows"].add(str(effect["window"]))

    markets = [
        {
            "market_id": entry["market_id"],
            "market_name": entry["market_name"],
            "n": len(entry["values"]),
            "windows": sorted(entry["windows"]),
            **_quantiles(entry["values"]),
        }
        for entry in by_market.values()
    ]
    markets.sort(key=lambda m: (-m["n"], m["market_id"]))

    return {
        "dyad_id": dyad,
        "dyad_name": rows[0]["dyad_name"],
        "as_of": anchor,
        "regime_gated": True,
        "episode_threshold": round(threshold, 4),
        "episodes": episodes,
        "fan": fan,
        "markets": markets,
        # An empty market list is a FINDING, not an error: it means the
        # transmission engine has measured nothing for this dyad in this
        # regime, usually because the markets did not exist yet.
        "markets_note": (
            None if markets else
            "no measured market effects for this dyad inside the current regime"
        ),
        "method": (
            f"episodes are the first quarter of a run at or above {_EPISODE_SHARE:.0%} "
            f"of the dyad's own peak intensity, regime-gated against {anchor}; "
            f"aftermath is the following {_AFTERMATH_QUARTERS} quarters as measured; "
            "market rows are measured AFFECTED effects, never modelled"
        ),
    }
