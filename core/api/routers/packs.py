"""Region packs, served: which exist, and what each declares."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from core import packs as packs_module

router = APIRouter(tags=["packs"])


@router.get("/packs")
def list_packs() -> dict[str, Any]:
    """Pack NAMES (the key every record carries) plus their display LABELS.

    Two fields rather than one because they are two different things: `packs`
    is what every other endpoint's `region=` parameter takes, `labels` is what
    a reader should be shown. A pack that fails to load still appears, under
    its name — the region selector listing every installed lens matters more
    than its caption, and the pack's own endpoint reports the fault.
    """
    names = packs_module.available()
    labels: dict[str, str] = {}
    for name in names:
        try:
            labels[name] = packs_module.load(name).label
        except packs_module.PackError:
            labels[name] = name
    return {"packs": names, "labels": labels}


@router.get("/packs/{name}")
def get_pack(name: str) -> dict[str, Any]:
    try:
        pack = packs_module.load(name)
    except packs_module.PackError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"name": pack.name, "label": pack.label, **pack.data}
