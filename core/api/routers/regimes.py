"""Regime endpoints — the conditioning layer, served."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from core.reasoning import regimes as regime_module

router = APIRouter(tags=["regimes"])


@router.get("/regimes")
def segmentation() -> dict[str, Any]:
    """The full monetary-order and polarity-epoch segmentation."""
    return regime_module.segmentation()


@router.get("/regimes/at/{date}")
def at(date: str) -> dict[str, Any]:
    """Both regime kinds covering an ISO-8601 date."""
    try:
        return regime_module.regimes_at(date)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
