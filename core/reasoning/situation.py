"""The situation briefing the agent (and MCP) reason over.

THE DIVISION OF LABOUR DOES NOT MOVE. This module MEASURES nothing. It
assembles a compact, already-computed briefing — the same layers the Situation
page composes from existing endpoints — so one call can argue across the wire,
the region games, packed markets, globe coverage, and frozen forecasts.

WHY THIS EXISTS. POST /reasoning/assess used to hand the agent four Forecast
rows (full `scenarios_json` blobs) and eight GLOBAL dyads ordered by EWMA.
That is not the situation, and it is not region-aware. The Situation page
already had the real briefing; the agent could not see it. One compact object,
capped and stripped of audit paragraphs, is what "reason over everything"
means here: cross-surface context, not a new estimator.

Every number in the object existed before this function ran. `explanation`
never travels — that paragraph is the audit for a human disclosure, and an
LLM that sees it will quote it as if it were the call.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from core import packs
from core.graph import kuzu_store

#: Same bar as `/api/wire` and the globe pulses. Duplicated rather than
#: imported from the routers: this module is loaded by MCP, which must not
#: pull FastAPI.
DEPARTURE_POINTS = 3.0

_WIRE_SCAN = 80
_DEPARTURE_CAP = 8
_LIVE_CAP = 4
_HEADLINE_CAP = 8
_RANKING_CAP = 6
_PULSE_CAP = 6
_FORECAST_CAP = 8

_COORDINATES = (
    Path(__file__).resolve().parent.parent
    / "ontology" / "crosswalks" / "actor_coordinates.yaml"
)

_NOTE = (
    "Every number here was measured or counted before this call. Frozen "
    "forecast likelihoods are counted base rates. Region ranking figures "
    "are from the persisted solved games, not a live re-solve. Market "
    "medians are historical abnormal-return cells. Cite a node_id or "
    "dyad_id in square brackets when you mention a number. Do not originate "
    "a figure that is not in this object."
)

_SURFACES = frozenset({
    "intel", "markets", "games", "relationships", "explorer", "cases", "network",
})
_FOCUS_KEYS = ("dyad_id", "event_id", "ticker", "slug")


def with_reader(
    briefing: dict[str, Any],
    *,
    surface: str | None = None,
    focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Where the reader is sitting — not a new measurement.

    Intel and the corner desk share one briefing. `reader` tells the agent
    which desk they summoned it from and which pair, market or event is
    open, pinning already-assembled ranking/headline rows when the ids
    match. Unknown surfaces and keys drop; nothing is invented.
    """
    reader: dict[str, Any] = {}
    name = (surface or "").strip().lower()
    if name in _SURFACES:
        reader["surface"] = name
    looking: dict[str, str] = {}
    if isinstance(focus, dict):
        for key in _FOCUS_KEYS:
            value = focus.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                looking[key] = text[:120]
    if looking:
        reader["looking_at"] = looking
        wanted_pair = looking.get("dyad_id")
        if wanted_pair:
            for row in (briefing.get("region_games") or {}).get("ranking") or []:
                if isinstance(row, dict) and row.get("dyad_id") == wanted_pair:
                    reader["pair"] = row
                    break
        ticker = looking.get("ticker")
        if ticker:
            for row in (briefing.get("markets") or {}).get("headlines") or []:
                if isinstance(row, dict) and row.get("ticker") == ticker:
                    reader["market"] = row
                    break
    if not reader:
        return briefing
    out = dict(briefing)
    out["reader"] = reader
    return out


def assemble(conn: Any | None, region: str) -> dict[str, Any]:
    """Compact situation briefing for `region`.

    `conn` is the graph if it is open; the wire, globe, live overlay and
    persisted panel layers answer without it. A missing panel or an empty
    live cache is said, never filled with a zero that looks like a reading.
    """
    pack = packs.load(region)
    names = _actor_names(pack)
    panel = _panel()
    try:
        briefing: dict[str, Any] = {
            "region": region,
            "region_label": pack.label,
            "wire": _wire(region, names),
            "live": _live(region),
            "region_games": _region_games(panel, region),
            "markets": _markets(panel, region),
            "globe": _globe(pack, names),
            "forecasts": _forecasts(conn, region),
            "note": _NOTE,
        }
    finally:
        if panel is not None:
            panel.close()
    return _without_explanations(briefing)


def _actor_names(pack: packs.Pack) -> dict[str, str]:
    names: dict[str, str] = {}
    for actor in pack.actors:
        node_id, name = str(actor.get("id") or ""), actor.get("name")
        if node_id and name:
            names[node_id] = str(name)
    return names


def _wire(region: str, names: dict[str, str]) -> dict[str, Any]:
    from core.wire import serving as wire_serving

    if not wire_serving.available():
        return {
            "departures": [],
            "as_of": None,
            "note": "no wire corpus in this process",
        }
    rows, _truncated = wire_serving.events_window(
        region, None, None, _WIRE_SCAN, newest_first=True,
    )
    departures: list[dict[str, Any]] = []
    as_of = None
    for row in rows:
        if as_of is None:
            as_of = row.get("event_time")
        magnitude = row.get("escalation_magnitude")
        if magnitude is None or float(magnitude) < DEPARTURE_POINTS:
            continue
        goldstein = row.get("goldstein")
        initiator = str(row.get("initiator_id") or "")
        target = str(row.get("target_id") or "")
        departures.append({
            "node_id": row.get("node_id"),
            "event_time": row.get("event_time"),
            "initiator_name": names.get(initiator),
            "target_name": names.get(target),
            "dyad_id": row.get("dyad_id"),
            "quad_class": row.get("quad_class"),
            "points_from_baseline": round(float(magnitude), 2),
            "pair_baseline": (
                round(float(row["escalation_baseline"]), 2)
                if row.get("escalation_baseline") is not None else None
            ),
            "tone": (
                "cooperative" if goldstein is not None and float(goldstein) > 0
                else "coercive" if goldstein is not None and float(goldstein) < 0
                else None
            ),
        })
        if len(departures) >= _DEPARTURE_CAP:
            break
    return {
        "departures": departures,
        "as_of": as_of,
        "departure_points": DEPARTURE_POINTS,
        "note": None if departures else "nothing in this scan left its pair's usual band",
    }


def _live(region: str) -> dict[str, Any]:
    """Cached GDELT 2.0 overlay only — this path must not fetch.

    Assess may be called from a page the reader is already on. Hitting GDELT
    here would couple narration to a network round-trip, and a refresh that
    fails would look like an empty region. Empty means this process has no
    batch yet, not that the region is quiet.
    """
    from core.wire import live as live_overlay

    rows = list(live_overlay.rows_for(region) or [])
    compact = []
    for row in rows[:_LIVE_CAP]:
        compact.append({
            "node_id": row.get("node_id"),
            "event_time": row.get("event_time") or row.get("available_at"),
            "initiator_name": row.get("initiator_name"),
            "target_name": row.get("target_name"),
            "quad_class": row.get("quad_class"),
            "implied_kind": row.get("implied_kind"),
            "dyad_id": row.get("dyad_id"),
        })
    return {
        "rows": compact,
        "note": None if compact else "no live batch in this process yet",
    }


def _panel() -> Any | None:
    try:
        from core import settings as settings_module
        from core.panel import pg_store

        return pg_store.connect(settings_module.load())
    except Exception:  # noqa: BLE001 - briefing without a panel still answers
        return None


def _region_games(panel: Any | None, region: str) -> dict[str, Any]:
    from core.games import scenarios
    from core.panel import pg_store

    if panel is None:
        return {
            "pending": True,
            "ranking": [],
            "lead": None,
            "note": "panel unavailable — no persisted region map to report",
        }
    stored = pg_store.game_solution(
        panel, region, scope="region", version=scenarios.PAYLOAD_VERSION,
    )
    if stored is None and pg_store.game_solution(
        panel, region, scope="region",
    ) is not None:
        return {
            "pending": True,
            "resolving": True,
            "ranking": [],
            "lead": None,
            "note": (
                "this region's scenario map is being re-solved for the "
                "current payload shape"
            ),
        }
    if stored is None:
        return {
            "pending": True,
            "ranking": [],
            "lead": None,
            "note": "no persisted region map of the current shape",
        }
    ranking = [_compact_rank(row) for row in (stored.get("ranking") or [])[:_RANKING_CAP]]
    return {
        "pending": False,
        "as_of": stored.get("as_of"),
        "dyads_solved": stored.get("dyads_solved"),
        "ranking": ranking,
        "lead": ranking[0] if ranking else None,
    }


def _compact_rank(row: dict[str, Any]) -> dict[str, Any]:
    family = row.get("family")
    family_name = family.get("family") if isinstance(family, dict) else family
    top = row.get("top_scenario") or {}
    course = None
    if isinstance(top, dict) and top:
        course = {
            "kind_label": top.get("kind_label"),
            "likelihood": top.get("likelihood"),
            "course": top.get("course"),
            "end_label": top.get("end_label"),
        }
    return {
        "dyad_id": row.get("dyad_id"),
        "dyad_name": row.get("dyad_name"),
        "coercive_events": row.get("coercive_events"),
        "hostility": row.get("hostility"),
        "sharp_departure_probability": row.get("sharp_departure_probability"),
        "opening_label": row.get("opening_label"),
        "family": family_name,
        "top_course": course,
    }


def _markets(panel: Any | None, region: str) -> dict[str, Any]:
    from core.panel import pg_store

    if panel is None:
        return {
            "pending": True,
            "headlines": [],
            "note": "panel unavailable — nothing measured to report",
        }
    stored = pg_store.market_story(panel, region)
    if stored is None:
        return {
            "pending": True,
            "headlines": [],
            "note": "the markets story has not been computed for this region yet",
        }
    headlines = []
    for market in stored.get("markets") or []:
        cell = market.get("headline")
        if not cell:
            continue
        headlines.append({
            "ticker": market.get("ticker"),
            "name": market.get("name"),
            "median": cell.get("median"),
            "n": cell.get("n"),
        })
        if len(headlines) >= _HEADLINE_CAP:
            break
    skill = stored.get("transmission_skill")
    if isinstance(skill, dict):
        # The method string names estimators. The compact block's numbers are
        # the skill; the sentence is composed on the surface.
        skill = {k: v for k, v in skill.items() if k != "method"}
    return {
        "pending": False,
        "as_of": stored.get("as_of"),
        "headlines": headlines,
        "transmission_skill": skill,
        "note": None if headlines else "no packed market holds a headline cell yet",
    }


@functools.lru_cache(maxsize=1)
def _coordinates() -> dict[str, tuple[float, float]]:
    import yaml

    with open(_COORDINATES, encoding="utf-8") as handle:
        table = yaml.safe_load(handle) or {}
    out: dict[str, tuple[float, float]] = {}
    for iso3, pair in (table.get("actor_coordinates") or {}).items():
        lat, lng = pair
        out[str(iso3).upper()] = (float(lat), float(lng))
    return out


def _globe(pack: packs.Pack, names: dict[str, str]) -> dict[str, Any]:
    """Roster coverage plus a handful of pulses — counts, not the sphere.

    Unplaced actors are the honest hole on the Situation page (blocs, funds,
    movements with no coordinate). Pulses are the same departure scan the
    globe animates, without the coded event `name`.
    """
    from core.wire import serving as wire_serving

    coords = _coordinates()
    placed = 0
    unplaced = 0
    for actor in pack.actors:
        iso3 = str(actor.get("iso3") or "").upper()
        if not actor.get("id"):
            continue
        if iso3 in coords:
            placed += 1
        else:
            unplaced += 1

    pulses: list[dict[str, Any]] = []
    if wire_serving.available():
        rows, _ = wire_serving.events_window(
            pack.name, None, None, 400, newest_first=True,
        )
        for row in rows:
            magnitude = row.get("escalation_magnitude")
            if magnitude is None or float(magnitude) < DEPARTURE_POINTS:
                continue
            conflict = str(row.get("quad_class") or "") == "material_conflict"
            if conflict and not row.get("coercion"):
                continue
            initiator = str(row.get("initiator_id") or "")
            target = str(row.get("target_id") or "")
            pulses.append({
                "event_id": row.get("node_id"),
                "event_time": row.get("event_time"),
                "initiator_name": names.get(initiator),
                "target_name": names.get(target),
                "dyad_id": row.get("dyad_id"),
                "points_from_baseline": round(float(magnitude), 2),
            })
            if len(pulses) >= _PULSE_CAP:
                break
    return {
        "placed": placed,
        "unplaced": unplaced,
        "pulses": pulses,
    }


def _forecasts(conn: Any | None, region: str) -> dict[str, Any]:
    if conn is None:
        return {
            "rows": [],
            "note": "graph unavailable — frozen forecasts not in this briefing",
        }
    rows = kuzu_store.query(
        conn,
        "MATCH (f:Forecast) WHERE f.region_pack = $region "
        "RETURN f.node_id AS node_id, f.mode AS mode, f.question AS question, "
        "f.generated_at AS generated_at, f.horizon_end AS horizon_end, "
        "f.boundary_statement AS boundary_statement, "
        "f.scenarios_json AS scenarios_json "
        "ORDER BY f.generated_at DESC, f.node_id LIMIT $limit",
        {"region": region, "limit": _FORECAST_CAP},
    )
    compact = []
    for row in rows:
        compact.append({
            "node_id": row.get("node_id"),
            "mode": row.get("mode"),
            "question": row.get("question"),
            "generated_at": row.get("generated_at"),
            "horizon_end": row.get("horizon_end"),
            "boundary_statement": row.get("boundary_statement") or None,
            "scenarios": _compact_scenarios(row.get("scenarios_json")),
        })
    return {
        "rows": compact,
        "note": None if compact else "no frozen forecasts for this region yet",
    }


def _compact_scenarios(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        name = item.get("scenario_name") or item.get("name")
        likelihood = item.get("likelihood")
        if name is None and likelihood is None:
            continue
        entry: dict[str, Any] = {}
        if name is not None:
            entry["scenario"] = str(name)
        if likelihood is not None:
            entry["likelihood"] = likelihood
        out.append(entry)
    return out


def _without_explanations(value: Any) -> Any:
    """Belt: audit paragraphs never enter the model's context."""
    if isinstance(value, dict):
        return {
            key: _without_explanations(item)
            for key, item in value.items()
            if key != "explanation"
        }
    if isinstance(value, list):
        return [_without_explanations(item) for item in value]
    return value
