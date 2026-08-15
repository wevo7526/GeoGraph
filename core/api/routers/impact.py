"""Event → market prices. The one endpoint pair behind the north-star output.

`GET /api/impact/{event_id}` — the historical read: what markets did (measured)
beside what they typically do in comparable periods (expected), with the
surprise. `POST /api/impact` — the predictive read for a specified event: the
base rate alone. Both return the one `EventImpact` object; the composition and
the honesty rules live in `core/reasoning/impact.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.classifier import escalation
from core.reasoning import impact as impact_module

router = APIRouter(tags=["impact"])


def _conn(request: Request) -> Any:
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    return conn


class HypotheticalRequest(BaseModel):
    """A specified event to price. Give a `dyad` id directly, or the two actor
    ids; `as_of` is the date the base rate is taken as of."""

    dyad: str | None = None
    initiator: str | None = None
    target: str | None = None
    as_of: str
    region: str | None = None


@router.get("/impact/{event_id}")
def impact_for_event(request: Request, event_id: str) -> dict[str, Any]:
    result = impact_module.event_impact(_conn(request), event_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no event {event_id}")
    return result


@router.post("/impact")
def impact_hypothetical(request: Request, body: HypotheticalRequest) -> dict[str, Any]:
    if body.dyad:
        dyad_id = body.dyad
    elif body.initiator and body.target:
        dyad_id = escalation.dyad_id(body.initiator, body.target)
    else:
        raise HTTPException(
            status_code=422, detail="give a dyad id, or both initiator and target"
        )
    return impact_module.hypothetical_impact(
        _conn(request), dyad_id=dyad_id, as_of=body.as_of, region=body.region
    )
