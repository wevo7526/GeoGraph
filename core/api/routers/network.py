"""Network metrics over time windows — lands with Phase 2 (analytics)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["network"])


@router.get("/network/metrics")
def metrics(window_start: str | None = None, window_end: str | None = None) -> dict:
    raise HTTPException(status_code=501, detail="Phase 2 — see docs/build-spec.md section 12")
