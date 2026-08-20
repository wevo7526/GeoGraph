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
from pydantic import BaseModel

from core import packs
from core.graph import kuzu_store
from core.reasoning import agent

router = APIRouter(tags=["case-studies"])


def _conn(request: Request) -> Any:
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    return conn


def _effects_for_event(conn: Any, event_id: str) -> list[dict[str, Any]]:
    """Panel-first measured effects. GDELT lives in event_study_runs."""
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
    if effects:
        return effects
    from core.api.routers.events import _effects_from_panel

    return _effects_from_panel(conn, event_id)


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


def _episode(
    conn: Any, event_id: str, note: str = "",
    effects_by_event: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """One episode in the case-study shape: the coded event, its dyad, every
    measured effect and (for the dynamic study) the impact read — measured
    beside expected, with the surprise."""
    event = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id}) RETURN e.node_id AS node_id, "
        "e.name AS name, e.event_time AS event_time, "
        "e.action_cameo_code AS cameo_code, e.quad_class AS quad_class, "
        "e.goldstein AS goldstein, e.fidelity_tier AS fidelity_tier, "
        "e.escalation_direction AS escalation_direction, "
        "e.escalation_magnitude AS escalation_magnitude, "
        "e.escalation_baseline AS escalation_baseline",
        {"id": event_id},
    )
    if not event:
        from core.wire import serving as wire_serving

        row = wire_serving.event(event_id)
        if row:
            event = [{
                "node_id": row.get("node_id") or event_id,
                "name": row.get("name") or row.get("label") or event_id,
                "event_time": row.get("event_time"),
                "cameo_code": row.get("action_cameo_code") or row.get("cameo"),
                "quad_class": row.get("quad_class"),
                "goldstein": row.get("goldstein"),
                "fidelity_tier": row.get("fidelity_tier") or "wire",
                "escalation_direction": row.get("escalation_direction"),
                "escalation_magnitude": row.get("escalation_magnitude"),
                "escalation_baseline": row.get("escalation_baseline"),
            }]
    if not event:
        return {"node_id": event_id, "missing": "not in the graph — has the pack been seeded?"}
    # ONE READ FOR THE WHOLE STUDY, not one per episode. A dyad study asks for
    # eight episodes and every one of them used to run its own AFFECTED query
    # (plus the base-rate read behind `event_impact`) — sixteen round trips
    # against a graph a background job is writing to, measured at 20-36s for
    # the page. The dyad's whole effect set already contains every episode's
    # rows, so the caller reads it once and each episode filters.
    if effects_by_event is not None:
        effects = effects_by_event.get(event_id, [])
    else:
        effects = _effects_for_event(conn, event_id)
    from core.reasoning import impact as impact_module

    read = impact_module.event_impact(conn, event_id)
    return {
        **event[0],
        "note": note,
        "dyad": (
            {"node_id": read["event"]["dyad"], "name": read["event"]["dyad"]}
            if read else None
        ),
        "actors": read["event"].get("actors") if read else None,
        "effects": effects,
        "first_movers": sorted({row["ticker"] for row in effects if row["first_mover"]}),
        "impact": (
            {"markets": read["markets"], "precedents": read["precedents"]} if read else None
        ),
    }


def _narrate(dyad_name: str, episodes: list[dict[str, Any]]) -> dict[str, str]:
    """Summary / reading / caveat written from the numbers alone."""
    measured = [e for e in episodes if e.get("effects")]
    if not measured:
        return {
            "summary": (
                f"{len(episodes)} coded event(s) on {dyad_name}; none yet carries a "
                "measured market effect, so this study has a spine and no numbers."
            ),
            "reading": (
                "The transmission engine measures events into the panel; until "
                "it reaches these, nothing is asserted about markets."
            ),
            "caveat": "Not yet measured. Not advice.",
        }
    sig = 0
    biggest: tuple[float, str, str, str] | None = None
    movers: dict[str, int] = {}
    for e in measured:
        for row in e["effects"]:
            car = row.get("abnormal_return")
            if car is None:
                continue
            p = row.get("p_value")
            if p is not None and float(p) < 0.05:
                sig += 1
            if biggest is None or abs(float(car)) > abs(biggest[0]):
                biggest = (float(car), str(row["market"]), str(row["window"]), str(e["name"]))
        for t in e.get("first_movers", []):
            movers[t] = movers.get(t, 0) + 1
    mover = max(movers.items(), key=lambda kv: kv[1])[0] if movers else None
    total = sum(len(e["effects"]) for e in measured)
    summary = (
        f"{len(measured)} of {len(episodes)} coded events on {dyad_name} carry measured "
        f"market effects: {total} event x market x window measurements, {sig} of them "
        "significant at p<0.05."
        + (
            f" The largest abnormal move: {biggest[1]} {biggest[0]:+.2%} ({biggest[2]}) on "
            f"'{biggest[3]}'." if biggest else ""
        )
        + (f" {mover} was the first market to react most often." if mover else "")
    )
    reading = (
        "Each measurement is a cumulative abnormal return against the market's own "
        "constant-mean estimation window at its native resolution, with the calendar "
        "deciding who could react first (Gulf Sun-Thu, US Mon-Fri). The expected column "
        "is the regime-gated base rate over this pair's other measured events; the "
        "surprise is measured minus expected. Overlapping events are flagged, never "
        "averaged away."
    )
    return {
        "summary": summary,
        "reading": reading,
        "caveat": (
            "Composed on request from the graph's measured effects; every figure is the "
            "transmission engine's, every sentence a template over those figures. "
            "Not advice."
        ),
    }


@router.get("/case-studies/dynamic")
def dynamic_case_study(
    request: Request,
    dyad: str | None = None,
    event: str | None = None,
    region: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """A case study composed on request — for any dyad (its most escalatory
    measured events) or any single event — in the SAME shape as a worked
    study, so one view renders both. The prose is written from the numbers;
    the numbers are the transmission engine's."""
    conn = _conn(request)
    if not dyad and not event:
        raise HTTPException(status_code=422, detail="give `dyad` or `event`")
    from core.reasoning import impact as impact_module

    if event:
        episodes = [_episode(conn, event)]
        head = episodes[0]
        if head.get("missing"):
            raise HTTPException(status_code=404, detail=f"no event {event}")
        dyad_name = str((head.get("dyad") or {}).get("name") or event)
        title = str(head.get("name") or event)
        slug = f"dynamic:event:{event}"
    else:
        assert dyad is not None
        timeline = impact_module.dyad_timeline(conn, dyad, limit=200)
        ranked = sorted(
            timeline["events"],
            key=lambda e: (
                -abs(float(e.get("escalation_magnitude") or 0.0)),
                -max((abs(float(m["car"])) for m in e["markets"]), default=0.0),
            ),
        )[:limit]
        # The dyad's measured effects, once, keyed by event — see `_episode`.
        from core.transmission import effects as effects_module

        by_event: dict[str, list[dict[str, Any]]] = {}
        for row in effects_module.effects_for_dyad(conn, dyad):
            by_event.setdefault(str(row["event_id"]), []).append({
                "ticker": row.get("ticker") or row.get("market_id"),
                "market": row.get("market_name"),
                "market_type": row.get("market_type"),
                "window": row.get("window"),
                "resolution": row.get("resolution"),
                "raw_return": row.get("raw_return"),
                "abnormal_return": row.get("abnormal_return"),
                "t_stat": row.get("t_stat"),
                "p_value": row.get("p_value"),
                "first_mover": row.get("first_mover"),
                "overlapping": row.get("overlapping"),
                "method": row.get("method"),
            })
        for rows in by_event.values():
            rows.sort(key=lambda r: (str(r["ticker"]), str(r["window"])))
        episodes = [
            _episode(conn, e["event_id"], effects_by_event=by_event) for e in ranked
        ]
        episodes.sort(key=lambda e: str(e.get("event_time", "")))
        dyad_name = dyad
        for e in episodes:
            if e.get("actors"):
                a = e["actors"]
                pair = f"{a.get('initiator', '')} - {a.get('target', '')}"
                dyad_name = pair.strip(" -")
                break
        title = f"{dyad_name}: the measured record"
        slug = f"dynamic:dyad:{dyad}"
    prose = _narrate(dyad_name, episodes)
    measured = sum(len(e.get("effects", [])) for e in episodes)
    dek = (
        "A composed reading of the measured record — every figure is the "
        "transmission engine's. The desk can argue from it; it does not "
        "originate a number."
        if event
        else (
            "A composed reading of the events that moved this relationship "
            "most, and what markets measurably did. The desk can argue from "
            "it; it does not originate a number."
        )
    )
    return {
        "slug": slug,
        "pack": region or "",
        "title": title,
        "dek": dek,
        "summary": prose["summary"],
        "reading": prose["reading"],
        "caveat": prose["caveat"],
        "episodes": episodes,
        "measured": measured,
        "status": "measured" if measured else "not_yet_measured",
        "dynamic": True,
    }


class NarrateBody(BaseModel):
    slug: str | None = None
    dyad: str | None = None
    event: str | None = None
    region: str | None = None


@router.post("/case-studies/narrate")
def narrate_case_study(request: Request, body: NarrateBody) -> dict[str, Any]:
    """Desk argument over a study the archive already composed.

    GET stays deterministic and fast. This POST is key-gated: a missing key
    is a 503 that names it, and the pack/template prose still stands.
    """
    slug = (body.slug or "").strip()
    if body.dyad or body.event:
        study = dynamic_case_study(
            request, dyad=body.dyad, event=body.event, region=body.region,
        )
    elif slug:
        study = get_case_study(request, slug)
    else:
        raise HTTPException(status_code=422, detail="give slug, dyad, or event")
    try:
        return agent.narrate_study(study)
    except agent.AgentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
        effects = _effects_for_event(conn, event_id)
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
