"""The narrated case study: the pack's prose beside the core's numbers.

This is the endpoint the reader's first screen is built from, so it is also
where the project's central claim is either kept or broken. The prose comes
from the pack; every figure comes from the graph as the transmission engine
computed it. Nothing here derives a number, and nothing here narrates one that
was not measured — if the engine has not run, the study says so rather than
telling the story without evidence.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from core import packs
from core.graph import kuzu_store

router = APIRouter(tags=["case-studies"])


def _conn(request: Request) -> Any:
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    return conn


def _studies() -> dict[str, tuple[str, dict[str, Any]]]:
    """slug → (pack name, case study). Packs without one simply do not appear."""
    out: dict[str, tuple[str, dict[str, Any]]] = {}
    for name in packs.available():
        study = packs.load(name).case_study
        if study:
            out[str(study["slug"])] = (name, study)
    return out


@router.get("/case-studies")
def list_case_studies() -> dict[str, Any]:
    """Every narrated episode the installed packs declare."""
    return {
        "rows": [
            {
                "slug": slug,
                "pack": pack_name,
                "title": study["title"],
                "dek": study.get("dek", ""),
                "events": list(study["events"]),
            }
            for slug, (pack_name, study) in sorted(_studies().items())
        ]
    }


@router.get("/case-studies/{slug}")
def get_case_study(request: Request, slug: str) -> dict[str, Any]:
    """One episode: the prose, the coded events, and the measured effects."""
    studies = _studies()
    if slug not in studies:
        raise HTTPException(
            status_code=404,
            detail=f"no such case study: {slug}. Available: {sorted(studies)}",
        )
    pack_name, study = studies[slug]
    conn = _conn(request)
    notes = {
        e["id"]: e.get("note", "")
        for e in packs.load(pack_name).marquee_events
    }

    episodes: list[dict[str, Any]] = []
    for event_id in study["events"]:
        event = kuzu_store.query(
            conn,
            "MATCH (e:Event {node_id: $id}) RETURN e.node_id AS node_id, "
            "e.name AS name, e.event_time AS event_time, "
            "e.action_cameo_code AS cameo_code, e.quad_class AS quad_class, "
            "e.goldstein AS goldstein, "
            "e.escalation_direction AS escalation_direction, "
            "e.escalation_magnitude AS escalation_magnitude, "
            "e.escalation_baseline AS escalation_baseline",
            {"id": event_id},
        )
        if not event:
            # The pack names an event the graph does not hold: say so instead of
            # dropping it, because a silently short episode reads as complete.
            episodes.append({
                "node_id": event_id,
                "missing": "not in the graph — has the pack been seeded?",
            })
            continue
        dyad = kuzu_store.query(
            conn,
            "MATCH (e:Event {node_id: $id})-[:OF_DYAD]->(d:Dyad) "
            "RETURN d.node_id AS node_id, d.name AS name",
            {"id": event_id},
        )
        effects = kuzu_store.query(
            conn,
            "MATCH (e:Event {node_id: $id})-[a:AFFECTED]->(m:Market) "
            "RETURN m.ticker AS ticker, m.name AS market, "
            "m.market_type AS market_type, a.window AS window, "
            "a.resolution AS resolution, a.raw_return AS raw_return, "
            "a.abnormal_return AS abnormal_return, a.t_stat AS t_stat, "
            "a.p_value AS p_value, a.first_mover AS first_mover, "
            "a.overlapping AS overlapping, a.method AS method "
            "ORDER BY ticker, window",
            {"id": event_id},
        )
        episodes.append({
            **event[0],
            "note": notes.get(event_id, ""),
            "dyad": dyad[0] if dyad else None,
            "effects": effects,
            "first_movers": sorted(
                {row["ticker"] for row in effects if row["first_mover"]}
            ),
        })

    measured = sum(len(e.get("effects", [])) for e in episodes)
    return {
        "slug": slug,
        "pack": pack_name,
        "title": study["title"],
        "dek": study.get("dek", ""),
        "summary": study.get("summary", ""),
        "reading": study.get("reading", ""),
        "caveat": study.get("caveat", ""),
        "episodes": episodes,
        # The honesty line: the reader is told whether the story they are about
        # to read has numbers under it.
        "measured": measured,
        "status": "measured" if measured else "not_yet_measured",
    }
