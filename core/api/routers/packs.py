"""Region packs, served: which exist, and what each declares."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from core import packs as packs_module

router = APIRouter(tags=["packs"])


@router.get("/packs")
def list_packs() -> dict:
    return {"packs": packs_module.available()}


@router.get("/packs/{name}")
def get_pack(name: str) -> dict:
    try:
        pack = packs_module.load(name)
    except packs_module.PackError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"name": pack.name, **pack.data}
