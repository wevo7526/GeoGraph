"""Forecasts and scenarios — lands with Phase 5 (reasoning layer).

When these arrive: near-term responses carry likelihoods and Brier history;
long-horizon responses ALWAYS carry the boundary statement (structural
pressure over windows, never dated point predictions).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["forecasts"])


@router.get("/forecasts")
def list_forecasts() -> dict[str, Any]:
    raise HTTPException(status_code=501, detail="Phase 5 — see docs/build-spec.md section 13")


@router.get("/forecasts/{node_id}")
def get_forecast(node_id: str) -> dict[str, Any]:
    raise HTTPException(status_code=501, detail="Phase 5 — see docs/build-spec.md section 13")
