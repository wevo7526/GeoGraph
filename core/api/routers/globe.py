"""The globe's data: where the roster sits, what binds it, what just moved.

DELIBERATELY GRAPH-FREE, and that is the design constraint that shaped it.
This feeds the front door, so it has to answer every time — and the graph does
not: the study runs as a child process, a child holds Kuzu's single write
lock, and every graph endpoint returns 503 for its slice (measured at roughly
one sample in twelve). A hero that goes blank for eight percent of visits is a
broken hero, so every field here comes from a source that cannot be locked:

  * positions  — `crosswalks/actor_coordinates.yaml`, a static file
  * the roster — the packs, read from YAML at import
  * the web    — the packs' own curated RELATES_TO declarations
  * the pulse  — the wire corpus through `wire/serving`, which is parsed into
                 process memory at startup

THREE LAYERS, because they answer three different questions and a globe that
conflated them would be decoration. The roster is WHO EXISTS. The web is WHAT
IS DECLARED — dated, sourced standings, the durable structure. The pulse is
WHAT JUST HAPPENED, and it is the only layer that moves.

A pulse is a DEPARTURE from the pair's own running baseline, never a raw
score: the same Goldstein value is an ordinary week for a rivalry and a
rupture for an alliance, so animating raw scores would light up the busiest
pairs rather than the ones doing something unusual.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from core import packs
from core.wire import serving as wire_serving

router = APIRouter(tags=["globe"])

_COORDINATES = (
    Path(__file__).resolve().parent.parent.parent
    / "ontology" / "crosswalks" / "actor_coordinates.yaml"
)

#: Goldstein points from a pair's own baseline that make an event worth
#: animating. The same bar `/api/wire` leads with, so the globe and the wire
#: cannot disagree about what counts as a departure.
PULSE_DEPARTURE_POINTS = 3.0

#: How many recent events to read per lens when looking for pulses. The globe
#: shows a handful; this is the window they are drawn from.
_PULSE_SCAN = 400


@functools.lru_cache(maxsize=1)
def coordinates() -> dict[str, tuple[float, float]]:
    """ISO3 → (lat, lng). Centroids, not capitals — see the crosswalk."""
    import yaml

    with open(_COORDINATES, encoding="utf-8") as handle:
        table = yaml.safe_load(handle) or {}
    out: dict[str, tuple[float, float]] = {}
    for iso3, pair in (table.get("actor_coordinates") or {}).items():
        lat, lng = pair
        out[str(iso3).upper()] = (float(lat), float(lng))
    return out


def _packs_for(region: str | None) -> list[str]:
    available = packs.available()
    if region is None:
        return available
    if region not in available:
        raise HTTPException(status_code=404, detail=f"no pack named {region!r}")
    return [region]


@router.get("/globe")
def globe(
    region: str | None = None,
    pulses: int = Query(12, ge=0, le=60),
) -> dict[str, Any]:
    """The roster placed on a sphere, the declared web between them, and the
    departures worth animating.

    `region` omitted means every lens — which is what a front door wants: the
    whole board, not one region's slice of it.
    """
    names = _packs_for(region)
    coords = coordinates()

    nodes: dict[str, dict[str, Any]] = {}
    unplaced: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []
    seen_links: set[tuple[str, ...]] = set()
    patrons: dict[str, str] = {}

    for name in names:
        pack = packs.load(name)
        # Patronage is the one DIRECTED standing the packs declare, and the
        # margin lane draws its clients against their patron — so the map is
        # built before the actors are walked.
        for relation in pack.relations:
            if str(relation.get("relation_type") or "") == "proxy":
                patrons.setdefault(str(relation.get("b") or ""), str(relation.get("a") or ""))
        for actor in pack.actors:
            iso3 = str(actor.get("iso3") or "").upper()
            node_id = str(actor.get("id") or "")
            if not node_id:
                continue
            if iso3 not in coords:
                # NOT PLACEABLE, AND DRAWN RATHER THAN DROPPED. Nineteen of the
                # seventy-five roster actors have no coordinate and none of
                # them is a gap: ten sovereign wealth funds, six organisations
                # (OPEC, the GCC, Hezbollah, Hamas, Ansar Allah, al-Qaeda) and
                # three historical states deliberately given no iso3 — giving
                # the GDR one would route 1980s wire traffic to the wrong
                # state. A globe that silently omitted a quarter of its own
                # roster would assert coverage it does not have, on a platform
                # whose whole claim is measured-never-asserted.
                entry = unplaced.get(node_id)
                if entry is None:
                    unplaced[node_id] = {
                        "id": node_id,
                        "name": str(actor.get("name") or node_id),
                        "actor_type": str(actor.get("actor_type") or ""),
                        "packs": [name],
                    }
                elif name not in entry["packs"]:
                    entry["packs"].append(name)
                continue
            lat, lng = coords[iso3]
            existing = nodes.get(node_id)
            if existing is None:
                nodes[node_id] = {
                    "id": node_id,
                    "name": str(actor.get("name") or iso3),
                    "iso3": iso3,
                    "lat": lat,
                    "lng": lng,
                    # A shared actor belongs to every lens that names it; the
                    # list is what lets the surface dim the ones off-lens
                    # rather than dropping them and breaking an arc.
                    "packs": [name],
                }
            elif name not in existing["packs"]:
                existing["packs"].append(name)

        for relation in pack.relations:
            a, b = str(relation.get("a") or ""), str(relation.get("b") or "")
            kind = str(relation.get("relation_type") or "")
            key = (*sorted((a, b)), kind)
            if not a or not b or key in seen_links:
                continue
            seen_links.add(key)
            links.append({
                "source": a,
                "target": b,
                "relation_type": kind,
                "valid_from": relation.get("valid_from") or "",
                "valid_to": relation.get("valid_to") or "",
                "pack": name,
            })

    # THE ONLY LAYER THAT MOVES. Read newest-first from the corpus and keep the
    # departures — the pair's own baseline decides, not the raw score.
    pulse_rows: list[dict[str, Any]] = []
    if pulses:
        for name in names:
            rows, _truncated = wire_serving.events_window(
                name, None, None, _PULSE_SCAN, newest_first=True
            )
            for row in rows:
                magnitude = row.get("escalation_magnitude")
                if magnitude is None or float(magnitude) < PULSE_DEPARTURE_POINTS:
                    continue
                # A CONFLICT PULSE MUST CLEAR THE COERCION TEST — the one
                # definition every counter on this platform reads
                # (`classifier.coercion`, folded onto the row at parse time).
                # Without it the first draft lit the globe with "Assault:
                # United States → Japan": two allies, a domestic crime story
                # that GDELT resolved to both countries by name. The homograph
                # defect is documented and open, and a glowing arc across the
                # Pacific is the most expensive possible place to show it.
                # Cooperative departures need no such test — they are not
                # claims about coercion.
                conflict = str(row.get("quad_class") or "") == "material_conflict"
                if conflict and not row.get("coercion"):
                    continue
                source, target = str(row.get("initiator_id") or ""), str(row.get("target_id") or "")
                if source not in nodes or target not in nodes:
                    continue
                pulse_rows.append({
                    "source": source,
                    "target": target,
                    # NAMED FIELDS, NOT THE CODED STRING. The event's own name
                    # is machine vocabulary — "Use conventional military force,
                    # not specified: Iran → Turkey" — and
                    # `test_surface_language.py` refuses that in a component.
                    # The surface composes the sentence from these
                    # (`lib/story.ts`), as it does on every other page.
                    "initiator_name": nodes[source]["name"],
                    "target_name": nodes[target]["name"],
                    "cameo_code": row.get("cameo_code"),
                    "quad_class": row.get("quad_class"),
                    "event_id": row.get("node_id"),
                    "event_time": row.get("event_time"),
                    "points_from_baseline": round(float(magnitude), 2),
                    "direction": row.get("escalation_direction"),
                    "pack": name,
                })
        pulse_rows.sort(
            key=lambda p: (str(p["event_time"]), p["points_from_baseline"]), reverse=True
        )
        pulse_rows = pulse_rows[:pulses]

    # WHAT THE PLATFORM SAYS ABOUT EACH STATE, attached to the node so the
    # hover can be a reading rather than a label. All of it is already in hand:
    # the standings come from the pack relations walked above, and the
    # departures from the pulse scan. No extra read, no graph.
    standings: dict[str, list[dict[str, str]]] = {}
    for link in links:
        a, b = link["source"], link["target"]
        for side, other in ((a, b), (b, a)):
            if side in nodes and other in nodes:
                standings.setdefault(side, []).append({
                    "with": nodes[other]["name"],
                    "relation_type": link["relation_type"],
                    "since": str(link["valid_from"])[:4],
                })
    departures: dict[str, int] = {}
    for pulse in pulse_rows:
        for side in (pulse["source"], pulse["target"]):
            departures[side] = departures.get(side, 0) + 1
    labels = {name: packs.load(name).label for name in names}
    for node in nodes.values():
        node["standings"] = sorted(
            standings.get(node["id"], []), key=lambda r: (r["relation_type"], r["with"])
        )[:4]
        node["departures"] = departures.get(node["id"], 0)
        node["regions"] = [labels.get(p, p) for p in node["packs"]]

    placed = [n for n in nodes.values() if any(p in names for p in n["packs"])]
    off_globe = sorted(unplaced.values(), key=lambda a: (a["actor_type"], a["name"]))
    for actor in off_globe:
        patron = patrons.get(actor["id"])
        if patron in nodes:
            actor["patron_id"] = patron
            actor["patron_name"] = nodes[patron]["name"]
    kept_links = [ln for ln in links if ln["source"] in nodes and ln["target"] in nodes]
    return {
        "region": region,
        "nodes": placed,
        "unplaced": off_globe,
        "links": kept_links,
        "pulses": pulse_rows,
        # Composed from SERVED fields rather than array arithmetic in TSX, so
        # the strapline and the payload can never disagree about the roster.
        "counts": {
            "placed": len(placed),
            "unplaced": len(off_globe),
            "links": len(kept_links),
            "pulses": len(pulse_rows),
        },
        "as_of": pulse_rows[0]["event_time"] if pulse_rows else None,
        "departure_points": PULSE_DEPARTURE_POINTS,
        "method": (
            "roster positions are country centroids (a drawing instruction, not "
            "evidence); links are the packs' declared, dated standings; a pulse "
            f"is an event at least {PULSE_DEPARTURE_POINTS} Goldstein points from "
            "that pair's own running baseline, never a raw score"
        ),
    }
