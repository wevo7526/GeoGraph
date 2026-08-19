"""A predicted step → what markets did after comparable events, MEASURED.

The half of the deliverable that makes it "a sequenced event WITH its market
movement" rather than just a sequence. And the half that must not be modelled:
`AFFECTED` edges are what the transmission engine measured from the price
panel, and every number here is a quantile of those. The game predicts events;
the archive prices them (build-spec section 17).

THE MATCHING IS DELIBERATELY COARSE. A step is matched to past events by quad
class, intensity band and regime — not by dyad, and not by exact magnitude.
Tightening it produces samples of two, and a median of two abnormal returns is
not a market implication, it is an anecdote with a percent sign. Where even
the coarse match is thin, the row SAYS the sample is thin rather than quietly
reporting a number nobody should act on.

NOTHING IS PREDICTED HERE. If a dyad's comparable events never touched a
market that existed at the time, the answer is that there is no measurement —
which is a real and common outcome, because the transmission engine records a
SKIP for markets that did not exist at event time.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from core import packs
from core.games import state as state_module
from core.graph import kuzu_store
from core.reasoning import regimes

#: Measurements a (quad, band, market) cell needs before its quantiles are
#: reported as a market implication rather than flagged as thin.
MIN_MEASUREMENTS = 8


def measured_effects(
    conn: Any, *, region_pack: str | None = None, panel: Any | None = None,
) -> list[dict[str, Any]]:
    """Every measured event→market effect, with the event's coded shape.

    One query. The alternative — fetching effects per predicted step — would
    hit the graph once per path per step per market, which for a distribution
    over eight paths is hundreds of round trips for data that fits in memory.

    Panel-first when Postgres is available: GDELT measurements live there
    after the graph copy of the wire is gone. Graph AFFECTED remains the
    fallback for tests and for spine/deep-tier edges.
    """
    own_panel = False
    if panel is None:
        try:
            from core import settings as settings_module
            from core.panel import pg_store

            panel = pg_store.connect(settings_module.load())
            own_panel = True
        except Exception:  # noqa: BLE001 - graph fallback
            panel = None
    try:
        if panel is not None and region_pack:
            rows = _from_panel(conn, panel, region_pack)
            if rows:
                return _compact(rows)
    finally:
        if own_panel and panel is not None:
            panel.close()
    # The region predicate binds to the clause it FOLLOWS: placed after the
    # OPTIONAL MATCH it filters the optional pattern (which cannot drop rows)
    # and every region's effects flow into every game's pricing. It must sit
    # on the outer MATCH.
    # The pack filter KEEPS pack-agnostic deep-tier events (COW MIDs carry
    # region_pack = ''): they are the only measured effects most historical
    # dyads have, and dropping them priced every game over the wire alone.
    where = "WHERE (e.region_pack = $pack OR e.region_pack = '') " if region_pack else ""
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event)-[a:AFFECTED]->(m:Market) "
        f"{where}"
        # OPTIONAL: an event with a measured effect but no coded dyad is still
        # a priceable event — dropping it here would quietly shrink the sample
        # every step is matched against. The ACTOR edges (the provenance
        # invariant, present on every event) reconstruct the dyad where
        # OF_DYAD is absent — production holds 278k AFFECTED beside 55 OF_DYAD,
        # so a consumer keying on d.node_id alone sees almost no dyads.
        "OPTIONAL MATCH (e)-[:OF_DYAD]->(d:Dyad) "
        "OPTIONAL MATCH (e)-[:INITIATED_BY]->(ia:Actor) "
        "OPTIONAL MATCH (e)-[:DIRECTED_AT]->(ta:Actor) "
        "RETURN e.node_id AS event_id, e.event_time AS event_time, "
        "e.quad_class AS quad_class, "
        "e.escalation_magnitude AS magnitude, d.node_id AS dyad_id, "
        "ia.node_id AS initiator_id, ta.node_id AS target_id, "
        "m.node_id AS market_id, m.name AS market_name, "
        "a.abnormal_return AS abnormal_return",
        {"pack": region_pack} if region_pack else {},
    )
    return _compact(_keep_pack_markets(rows, region_pack))


def clip_to_pack(
    payload: dict[str, Any] | None, region_pack: str,
) -> dict[str, Any] | None:
    """Drop priced rows for markets this pack does not name.

    Deep-tier events (`region_pack = ''`) and overlapping-roster GDELT ids
    are measured against EVERY pack's markets. The panel stores those rows
    under the same event id, so a Eurasia solve that read every ticker
    priced US–Russia to TAIEX and KOSPI. Persisted maps keep that leak
    until the next games job; clipping at serve is what the surface sees
    today, and `_from_panel` is what the next solve writes.
    """
    if not payload:
        return payload
    allowed = _pack_market_ids(region_pack)
    if allowed is None:
        return payload
    return _clip_market_rows(payload, allowed)


def _pack_market_ids(region_pack: str) -> frozenset[str] | None:
    try:
        return packs.load(region_pack).market_ids
    except packs.PackError:
        return None


def _pack_market_lookup(region_pack: str) -> dict[str, tuple[str, str]] | None:
    try:
        pack = packs.load(region_pack)
    except packs.PackError:
        return None
    return {
        str(m["ticker"]): (str(m["id"]), str(m.get("name") or m["ticker"]))
        for m in pack.markets
    }


def _keep_pack_markets(
    rows: list[dict[str, Any]], region_pack: str | None,
) -> list[dict[str, Any]]:
    if not region_pack:
        return rows
    allowed = _pack_market_ids(region_pack)
    if allowed is None:
        return rows
    return [row for row in rows if row.get("market_id") in allowed]


def _clip_market_rows(value: Any, allowed: frozenset[str]) -> Any:
    if isinstance(value, dict):
        out = {key: _clip_market_rows(item, allowed) for key, item in value.items()}
        for key in ("market_implications", "direction", "market"):
            rows = out.get(key)
            if isinstance(rows, list):
                out[key] = [
                    row for row in rows
                    if not (
                        isinstance(row, dict)
                        and row.get("market_id")
                        and row["market_id"] not in allowed
                    )
                ]
        return out
    if isinstance(value, list):
        return [_clip_market_rows(item, allowed) for item in value]
    return value


def _from_panel(
    conn: Any, panel: Any, region_pack: str,
) -> list[dict[str, Any]]:
    """Join panel measurements to Head B coding. GDELT keeps the headline
    window only — extra windows of the same event were correlated copies."""
    from core.classifier import escalation
    from core.panel import pg_store
    from core.reasoning.markets import HEADLINE_WINDOW, coding_for

    coding = coding_for(conn, region_pack)
    # THIS LENS'S MARKETS, not every Market node. Deep-tier events are
    # measured once per pack against that pack's tickers; the panel then
    # holds Hang Seng rows on the same event id Eurasia prices from. Reading
    # the whole Market table made every region's games point at Asia.
    markets = _pack_market_lookup(region_pack)
    if markets is None:
        markets = {
            str(r["ticker"]): (str(r["id"]), str(r["name"]))
            for r in kuzu_store.query(
                conn,
                "MATCH (m:Market) RETURN m.node_id AS id, m.name AS name, "
                "m.ticker AS ticker",
            )
        }
    # ONLY THE EVENTS THIS REGION CAN CODE. `computed_runs()` with no filter
    # materialises every measured row in one statement — the same unbounded
    # read that killed the refill job — and `context.build` asks for this on
    # the first games/markets tick after boot. Chunk the ids so a region's
    # working set is that region's rows, not the whole panel.
    wanted = [
        eid for eid, meta in coding.items()
        if (meta.get("region_pack") or "") in (region_pack, "")
    ]
    if not wanted:
        return []
    out: list[dict[str, Any]] = []
    chunk = 4_000
    for start in range(0, len(wanted), chunk):
        for run in pg_store.computed_runs(panel, event_ids=wanted[start:start + chunk]):
            event_id = str(run["event_node_id"])
            meta = coding.get(event_id)
            if meta is None:
                continue
            pack = meta.get("region_pack") or ""
            if pack not in (region_pack, ""):
                continue
            if event_id.startswith("event:gdelt-") and str(run["window"]) != HEADLINE_WINDOW:
                continue
            ticker = str(run["market_ticker"])
            if ticker not in markets:
                continue
            market_id, market_name = markets[ticker]
            initiator = meta.get("initiator_id")
            target = meta.get("target_id")
            dyad = None
            if initiator and target:
                dyad = escalation.dyad_id(initiator, target)
            out.append({
                "event_id": event_id,
                "event_time": meta.get("date"),
                "quad_class": meta.get("quad_class"),
                "magnitude": meta.get("magnitude"),
                "dyad_id": dyad,
                "initiator_id": initiator,
                "target_id": target,
                "market_id": market_id,
                "market_name": market_name,
                "abnormal_return": run["abnormal_return"],
                "overlapping": str(run.get("status") or "") == "overlapping",
                "p_value": run.get("p_value"),
            })
    return out


#: Columns whose distinct values are few beside the number of rows. AFFECTED
#: passed 900,000 edges on 2026-08-16 and points at TWENTY markets and four
#: quad classes, so `market_id` alone is 900,000 separate string objects
#: holding one of twenty values.
_REPEATED = (
    "market_id", "market_name", "quad_class", "event_time",
    "dyad_id", "initiator_id", "target_id", "event_id",
)


def _compact(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the repeated strings to shared objects.

    THIS CACHE GROWS WITH THE ARCHIVE, which is what makes it worth the pass.
    A region's context holds every one of these rows for the life of the
    process, and the loop's whole purpose is to make there be more of them —
    so the games page getting faster and the container getting closer to the
    kernel's kill line were the same event. Measured before this: the process
    walked 5.17 GB to 6.93 GB in half an hour with reclaims recovering nothing.

    Interning is the cheap half of the fix and needs no consumer to change:
    the eight columns below hold a handful of distinct values each (twenty
    markets, four quad classes, ~200k events across ~900k rows), so the strings
    become pointers into one table instead of a million separate objects.
    `event_id` is the least repetitive and still averages four rows per event.
    """
    seen: dict[str, str] = {}
    for row in rows:
        for column in _REPEATED:
            value = row.get(column)
            if isinstance(value, str):
                row[column] = seen.setdefault(value, value)
    return rows


def dyad_of_event(effects: list[dict[str, Any]]) -> dict[str, str]:
    """event_id → dyad_id, for the duration-by-dyad report.

    Reconstructs the dyad from the actor edges when OF_DYAD is absent (it
    almost always is — 278k AFFECTED beside 55 OF_DYAD in production), and
    OMITS an event whose dyad cannot be determined rather than mapping it to a
    fabricated key. The old call sites did `str(e.get("dyad_id", ""))`, which
    returns the string "None" for a present-but-null dyad_id — truthy, so
    every event collapsed into one bogus "None" dyad and the real ones
    vanished."""
    from core.classifier import escalation

    out: dict[str, str] = {}
    for e in effects:
        event_id = e.get("event_id")
        if not event_id:
            continue
        dyad = e.get("dyad_id")
        if not dyad and e.get("initiator_id") and e.get("target_id"):
            dyad = escalation.dyad_id(e["initiator_id"], e["target_id"])
        if dyad:
            out[str(event_id)] = str(dyad)
    return out


def _quantiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def at(share: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * share))]

    return {
        "min": round(ordered[0], 6),
        "p25": round(at(0.25), 6),
        "median": round(at(0.5), 6),
        "p75": round(at(0.75), 6),
        "max": round(ordered[-1], 6),
    }


def build_index(
    effects: list[dict[str, Any]],
    *,
    as_of: str,
    scale: float,
    exclude_overlapping: bool = False,
) -> dict[tuple[str, int], dict[str, list[float]]]:
    """(quad class, intensity band) → market → the measured abnormal returns.

    Regime-gated against `as_of` by the same admissibility test the analogy
    engine uses: a 2026 question is never answered with Bretton Woods
    evidence, however similar the event shape.

    `exclude_overlapping` is the clean cell: contaminated windows stay in the
    default index so a published game price does not silently move.
    """
    index: dict[tuple[str, int], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for effect in effects:
        if effect["abnormal_return"] is None or not effect["quad_class"]:
            continue
        if not regimes.comparable(as_of, str(effect["event_time"])):
            continue
        if exclude_overlapping and (
            effect.get("overlapping") is True
            or str(effect.get("status") or "") == "overlapping"
        ):
            continue
        magnitude = effect["magnitude"]
        band = state_module.intensity_band(
            float(magnitude) if magnitude is not None else 0.0, scale
        )
        key = (str(effect["quad_class"]), band)
        index[key][str(effect["market_id"])].append(float(effect["abnormal_return"]))
    return index


def price_step(
    step: dict[str, Any],
    index: dict[tuple[str, int], dict[str, list[float]]],
    names: dict[str, str],
    *,
    min_measurements: int = MIN_MEASUREMENTS,
) -> list[dict[str, Any]]:
    """One predicted step → a measured distribution per market.

    Falls back from (quad, band) to (quad, any band) when the exact cell is
    thin, and says which match it used. A reader who knows the row came from
    a looser match reads it differently, which is the entire reason to say so.
    """
    exact = index.get((str(step["quad"]), int(step["intensity_band"])), {})
    loosened: dict[str, list[float]] = defaultdict(list)
    for (quad, _band), markets in index.items():
        if quad == step["quad"]:
            for market, values in markets.items():
                loosened[market].extend(values)

    rows: list[dict[str, Any]] = []
    for market in sorted(set(exact) | set(loosened)):
        values = exact.get(market, [])
        match = "quad+band"
        if len(values) < min_measurements:
            values = loosened.get(market, [])
            match = "quad only"
        if not values:
            continue
        rows.append({
            "market_id": market,
            "market_name": names.get(market, market),
            "n": len(values),
            "match": match,
            # Thin is reported, not hidden and not dropped: an implication
            # resting on five measurements is still evidence, but a reader is
            # entitled to know it is five.
            "thin": len(values) < min_measurements,
            **_quantiles(values),
        })
    rows.sort(key=lambda r: (-r["n"], r["market_id"]))
    return rows


def price_paths(
    paths: dict[str, Any],
    effects: list[dict[str, Any]],
    *,
    as_of: str,
    scale: float,
    min_measurements: int = MIN_MEASUREMENTS,
) -> dict[str, Any]:
    """Attach measured market distributions to every step of every path."""
    index = build_index(effects, as_of=as_of, scale=scale)
    names = {
        str(e["market_id"]): str(e["market_name"])
        for e in effects if e.get("market_name")
    }
    def _price(course: dict[str, Any]) -> dict[str, Any]:
        return {
            **course,
            "steps": [
                {**step, "market": price_step(step, index, names,
                                              min_measurements=min_measurements)}
                for step in course["steps"]
            ],
        }

    priced = [_price(path) for path in paths.get("paths", [])]
    # The per-KIND representatives are priced too: a kind can hold real mass
    # without any of its courses surviving the top-N reading cut, and a named
    # scenario with no market row would be the one place the surface stops
    # saying what such courses have historically moved.
    kinds = [_price(kind) for kind in paths.get("kinds", [])]

    measured = sum(len(m) for markets in index.values() for m in markets.values())
    return {
        **paths,
        "paths": priced,
        "kinds": kinds,
        "pricing": {
            "measurements": measured,
            "cells": len(index),
            "regime_gated_to": as_of,
            "min_measurements": min_measurements,
            "method": (
                "measured AFFECTED abnormal returns for events of the same quad "
                "class and intensity band, regime-gated; quantiles over the "
                "matched set, never a modelled price"
            ),
            # An empty index is a FINDING. It means the transmission engine has
            # measured nothing admissible for this region and regime — usually
            # because the markets did not exist at event time — and the paths
            # should render with no prices rather than with invented ones.
            "note": (
                None if measured else
                "no measured market effects are admissible for this regime; "
                "the sequence stands without prices"
            ),
        },
    }
