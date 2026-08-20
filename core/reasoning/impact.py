"""Event → market prices. The product's north star, as one deterministic read.

Given an event, this answers two things and never confuses them:

  1. MEASURED — what markets actually did (the AFFECTED edges the transmission
     engine wrote for this event). The actual.
  2. EXPECTED — what markets typically do when this relationship flares in a
     comparable period (the base rate over regime-comparable past events of the
     same dyad). The prediction.

The rule that keeps it honest (build-spec §17): every price here is a MEASURED
abnormal return. This module never originates a number — it reads what
`transmission/effects.write_effects` wrote and aggregates it, gated by
`regimes.comparable`. The ML and the game (a later `forward` block) only decide
which class of event comes next; they never author a price.

`expected` is `None` (not `0.0`) when there is no admissible precedent — an
absent measurement is not a flat one.
"""

from __future__ import annotations

from typing import Any

from core.classifier import escalation
from core.graph import kuzu_store
from core.reasoning import regimes
from core.transmission import effects as effects_module

#: Long-horizon honesty, echoed onto every impact read so a market number is
#: never mistaken for a dated promise.
BOUNDARY_STATEMENT = (
    "measured moves over event windows and their base rate in comparable "
    "periods — not a dated prediction"
)


def _summary(values: list[float]) -> dict[str, Any]:
    """A distribution, never a mean alone: the point of a base rate is the
    SPREAD of what followed. lo/hi are the 10th/90th percentiles."""
    ordered = sorted(values)
    n = len(ordered)

    def at(share: float) -> float:
        return ordered[min(n - 1, int(n * share))]

    return {
        "mean_car": round(sum(ordered) / n, 6),
        "median_car": round(at(0.5), 6),
        "lo": round(at(0.1), 6),
        "hi": round(at(0.9), 6),
        "n_precedents": n,
    }


def _event_context(conn: Any, event_id: str) -> dict[str, Any] | None:
    """Resolve the event to its dyad, actors, date, band and regime. None when
    the event is missing from both the graph and the corpus, or carries no
    actor edges (the provenance invariant guarantees INITIATED_BY/DIRECTED_AT
    on graph events; the corpus row carries the same pair)."""
    rows = kuzu_store.query(
        conn,
        "MATCH (i:Actor)<-[:INITIATED_BY]-(e:Event {node_id: $id})"
        "-[:DIRECTED_AT]->(t:Actor) "
        "RETURN i.node_id AS initiator, t.node_id AS target, "
        "e.event_time AS event_time, e.escalation_direction AS direction, "
        "e.escalation_magnitude AS magnitude, e.goldstein AS goldstein, "
        "e.region_pack AS region_pack",
        {"id": event_id},
    )
    if not rows:
        from core.wire import serving as wire_serving

        wire = wire_serving.event(event_id)
        if not wire or not wire.get("initiator_id") or not wire.get("target_id"):
            return None
        initiator = str(wire["initiator_id"])
        target = str(wire["target_id"])
        return {
            "id": event_id,
            "date": str(wire.get("event_time") or ""),
            "dyad": escalation.dyad_id(initiator, target),
            "actors": {"initiator": initiator, "target": target},
            "region": wire.get("region_pack"),
            "escalation": {
                "direction": wire.get("escalation_direction"),
                "magnitude": wire.get("escalation_magnitude"),
            },
            "goldstein": wire.get("goldstein"),
        }
    row = rows[0]
    return {
        "id": event_id,
        "date": str(row["event_time"]),
        "dyad": escalation.dyad_id(str(row["initiator"]), str(row["target"])),
        "actors": {"initiator": row["initiator"], "target": row["target"]},
        "region": row.get("region_pack"),
        "escalation": {
            "direction": row.get("direction"),
            "magnitude": row.get("magnitude"),
        },
        "goldstein": row.get("goldstein"),
    }


def _measured_for_event(conn: Any, event_id: str) -> dict[str, dict[str, Any]]:
    """This event's own measured effects, keyed by market. What markets DID."""
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id})-[a:AFFECTED]->(m:Market) "
        "RETURN m.node_id AS market_id, m.name AS market_name, "
        "a.abnormal_return AS abnormal_return, a.window AS window, "
        "a.first_mover AS first_mover, a.resolution AS resolution",
        {"id": event_id},
    )
    if not rows:
        from core.api.routers.events import _effects_from_panel

        rows = [
            {
                "market_id": r.get("market_node_id") or r.get("ticker"),
                "market_name": r.get("market") or r.get("ticker"),
                "abnormal_return": r.get("abnormal_return"),
                "window": r.get("window"),
                "first_mover": r.get("first_mover"),
                "resolution": r.get("resolution"),
            }
            for r in _effects_from_panel(conn, event_id)
        ]
    measured: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["abnormal_return"] is None:
            continue
        measured[str(row["market_id"])] = {
            "market_id": row["market_id"],
            "market_name": row["market_name"],
            "car": round(float(row["abnormal_return"]), 6),
            "window": str(row["window"]),
            "first_mover": bool(row["first_mover"]),
            "resolution": row.get("resolution"),
        }
    return measured


def _expected_for_dyad(
    conn: Any, dyad_id: str, as_of: str, *, exclude_event_id: str | None = None,
    exclude_overlapping: bool = False,
) -> tuple[dict[str, dict[str, Any]], int]:
    """The base rate: this dyad's measured effects in periods regime-comparable
    to `as_of`, aggregated per market. Returns (per-market summary, number of
    distinct precedent events).

    Membership comes from the actor edges via `effects_for_dyad` — the same
    reconstruction that reaches production's 278k AFFECTED beside 55 OF_DYAD
    edges. Regime-gated so a modern question is never answered with
    Bretton-Woods evidence.
    """
    rows = effects_module.effects_for_dyad(conn, dyad_id)
    by_market: dict[str, dict[str, Any]] = {}
    precedent_events: set[str] = set()
    for row in rows:
        if row["abnormal_return"] is None:
            continue
        if exclude_event_id is not None and str(row["event_id"]) == exclude_event_id:
            continue
        if exclude_overlapping and row.get("overlapping"):
            continue
        if not regimes.comparable(as_of, str(row["event_time"])):
            continue
        entry = by_market.setdefault(
            str(row["market_id"]),
            {"market_id": row["market_id"], "market_name": row["market_name"], "values": []},
        )
        entry["values"].append(float(row["abnormal_return"]))
        precedent_events.add(str(row["event_id"]))

    expected: dict[str, dict[str, Any]] = {}
    for market_id, entry in by_market.items():
        expected[market_id] = {
            "market_id": entry["market_id"],
            "market_name": entry["market_name"],
            **_summary(entry["values"]),
        }
    return expected, len(precedent_events)


def _merge_markets(
    measured: dict[str, dict[str, Any]], expected: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """One row per market that has a measured move OR a base rate, with the
    surprise (measured − expected) where both exist."""
    markets: list[dict[str, Any]] = []
    for market_id in sorted(set(measured) | set(expected)):
        m = measured.get(market_id)
        e = expected.get(market_id)
        name = (m or e or {}).get("market_name")
        surprise = (
            round(m["car"] - e["mean_car"], 6) if m is not None and e is not None else None
        )
        markets.append({
            "market_id": market_id,
            "market_name": name,
            "measured": m,
            "expected": e,
            "surprise": surprise,
        })
    # Most-evidenced / largest measured first, so the answer leads with signal.
    markets.sort(
        key=lambda row: (
            -(row["expected"]["n_precedents"] if row["expected"] else 0),
            -abs(row["measured"]["car"]) if row["measured"] else 0.0,
        )
    )
    return markets


def event_impact(conn: Any, event_id: str) -> dict[str, Any] | None:
    """The historical read for one event: measured actual + expected base rate
    + surprise. None when the event does not exist."""
    context = _event_context(conn, event_id)
    if context is None:
        return None
    measured = _measured_for_event(conn, event_id)
    expected, n = _expected_for_dyad(
        conn, context["dyad"], context["date"], exclude_event_id=event_id,
        exclude_overlapping=False,
    )
    return {
        "mode": "historical",
        "event": context,
        "markets": _merge_markets(measured, expected),
        "precedents": {"n": n, "as_of": context["date"], "regime_gated": True},
        "boundary_statement": BOUNDARY_STATEMENT,
    }


def dyad_timeline(conn: Any, dyad_id: str, *, limit: int = 40) -> dict[str, Any]:
    """A relationship's market-moving events, most recent first: one entry per
    event that carries a measured AFFECTED edge, each listing what its markets
    did. The feed behind the Relationship page's past→now spine — the north
    star made visible per event. Empty is honest (this dyad has no measured
    effects yet), never fabricated."""
    rows = effects_module.effects_for_dyad(conn, dyad_id)
    by_event: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["abnormal_return"] is None:
            continue
        event = by_event.setdefault(
            str(row["event_id"]),
            {
                "event_id": row["event_id"], "date": str(row["event_time"]),
                # What happened, who did it to whom, how it was coded — the
                # timeline used to carry the date alone and read as a list of
                # numbers with no story (the redesign spec's "what happened,
                # which markets moved, who reacted first").
                "name": row.get("event_name"),
                "goldstein": row.get("goldstein"),
                "escalation_direction": row.get("escalation_direction"),
                "escalation_magnitude": row.get("escalation_magnitude"),
                "fidelity_tier": row.get("fidelity_tier"),
                "initiator_id": row.get("initiator_id"),
                "target_id": row.get("target_id"),
                "first_mover": None,
                "markets": [],
            },
        )
        if row.get("first_mover") and not event["first_mover"]:
            event["first_mover"] = row["market_name"]
        event["markets"].append({
            "market_id": row["market_id"],
            "market_name": row["market_name"],
            "car": round(float(row["abnormal_return"]), 6),
            "window": str(row["window"]),
            "p_value": row.get("p_value"),
            "first_mover": bool(row.get("first_mover")),
        })
    events = sorted(by_event.values(), key=lambda e: e["date"], reverse=True)[:limit]
    return {"dyad": dyad_id, "events": events, "total": len(by_event)}


def hypothetical_impact(
    conn: Any, *, dyad_id: str, as_of: str, region: str | None = None
) -> dict[str, Any]:
    """The predictive read for a specified event: the base rate for this dyad
    as of a date, with no measured actual. `expected` markets are `[]` when no
    admissible precedent exists — honest, not empty-as-zero."""
    expected, n = _expected_for_dyad(conn, dyad_id, as_of)
    markets = [
        {"market_id": mid, "market_name": e["market_name"], "measured": None,
         "expected": e, "surprise": None}
        for mid, e in expected.items()
    ]
    markets.sort(key=lambda row: -(row["expected"]["n_precedents"] if row["expected"] else 0))
    return {
        "mode": "hypothetical",
        "event": {"dyad": dyad_id, "date": as_of, "region": region, "actors": None,
                  "escalation": None},
        "markets": markets,
        "precedents": {"n": n, "as_of": as_of, "regime_gated": True},
        "boundary_statement": BOUNDARY_STATEMENT,
    }


# ── coverage: the market-movement trace, registered per dyad ────────────────

_COVERAGE_CACHE: dict[str, dict[str, Any]] = {}


def dyad_coverage(conn: Any, region_pack: str, roster: set[str]) -> dict[str, Any]:
    """Per dyad in a pack: how many of its graph events carry a measured
    market effect, so "no measured effects" is distinguishable from "not yet
    measured". Cached per process — AFFECTED changes only on a measuring
    boot, and the API process is replaced on deploy.

    Deep-tier events carry region_pack '' and count for every pack whose
    roster holds both actors; the pack's own wire events count once.
    """
    cached = _COVERAGE_CACHE.get(region_pack)
    if cached is not None:
        return cached
    events = kuzu_store.query(
        conn,
        "MATCH (x:Actor)<-[:INITIATED_BY]-(e:Event)-[:DIRECTED_AT]->(y:Actor) "
        "WHERE e.region_pack = $pack OR e.region_pack = '' "
        "RETURN x.node_id AS a, y.node_id AS b, count(e) AS n",
        {"pack": region_pack},
    )
    measured = kuzu_store.query(
        conn,
        "MATCH (x:Actor)<-[:INITIATED_BY]-(e:Event)-[:DIRECTED_AT]->(y:Actor), "
        "(e)-[:AFFECTED]->(m:Market) "
        "WHERE e.region_pack = $pack OR e.region_pack = '' "
        "RETURN x.node_id AS a, y.node_id AS b, count(DISTINCT e) AS n",
        {"pack": region_pack},
    )
    per_dyad: dict[str, dict[str, Any]] = {}

    def slot(a: str, b: str) -> dict[str, Any] | None:
        if a not in roster or b not in roster:
            return None
        did = escalation.dyad_id(a, b)
        return per_dyad.setdefault(did, {"dyad_id": did, "events": 0, "measured": 0})

    for row in events:
        s_ = slot(str(row["a"]), str(row["b"]))
        if s_ is not None:
            s_["events"] += int(row["n"] or 0)
    for row in measured:
        s_ = slot(str(row["a"]), str(row["b"]))
        if s_ is not None:
            s_["measured"] += int(row["n"] or 0)
    rows = []
    for entry in per_dyad.values():
        n = entry["events"]
        entry["share_measured"] = round(entry["measured"] / n, 4) if n else 0.0
        entry["status"] = (
            "measured" if entry["measured"] else ("unmeasured" if n else "no_events")
        )
        rows.append(entry)
    rows.sort(key=lambda r: (-r["measured"], -r["events"], r["dyad_id"]))
    total_events = sum(r["events"] for r in rows)
    total_measured = sum(r["measured"] for r in rows)
    out = {
        "region": region_pack,
        "dyads": rows,
        "summary": {
            "dyads": len(rows),
            "dyads_measured": sum(1 for r in rows if r["measured"]),
            "events": total_events,
            "events_measured": total_measured,
            "share_measured": round(total_measured / total_events, 4) if total_events else 0.0,
        },
        "note": (
            "events = graph events between two roster actors (pack wire + deep tier); "
            "measured = those carrying at least one AFFECTED edge. Corpus-only events "
            "are not in the graph and cannot carry effects until a loading boot merges "
            "them; a measuring boot (GEOGRAPH_STUDY_ON_BOOT=1) grows `measured`."
        ),
    }
    _COVERAGE_CACHE[region_pack] = out
    return out
