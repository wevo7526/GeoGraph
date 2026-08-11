"""Events and effects — lands with Phase 0 (spine) and Phase 1 (effects).

The endpoints 501 rather than pretend: a wrong empty answer teaches a caller
the graph is empty; a named phase teaches them what is coming.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["events"])

_PHASE = "Phase 0/1 — see docs/build-spec.md sections 14 and 18"


@router.get("/events")
def list_events() -> dict:
    raise HTTPException(status_code=501, detail=_PHASE)


@router.get("/events/{node_id}/effects")
def event_effects(node_id: str) -> dict:
    raise HTTPException(status_code=501, detail=_PHASE)


@router.get("/escalation/{dyad_id}")
def escalation_trajectory(dyad_id: str) -> dict:
    raise HTTPException(status_code=501, detail=_PHASE)
