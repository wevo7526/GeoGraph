"""The markets story for a region — what its geopolitics has DONE to prices,
what the solved games say it will do next, and how long the curve says it lasts.

THE PAGE THIS FEEDS used to be the paper book alone: a mechanical P&L over a
frozen call, which is one paragraph of the story and was the whole page. A
reader of international finance wants the transmission map first — when this
region escalates, which market moves, by how much, in which direction, and
who prints first — and every number here is a quantile of measured AFFECTED
edges or a field of a persisted solve (build-spec §17: nothing originates a
number; the template writes from the payload).

Computed by the `markets` job (core/api/work.py) and persisted, because the
region-wide read over AFFECTED is seconds and grows with the archive.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from typing import Any

from core.graph import kuzu_store
from core.panel import pg_store
from core.reasoning import strategy

#: An escalation whose magnitude — the departure from the pair's own baseline,
#: in Goldstein points — clears this is SHARP: three points is the gap between
#: a demand and a threat of force on the codebook, so it is a rupture wherever
#: it lands. Under it an escalating event is ordinary friction.
SHARP_MAGNITUDE = 3.0

#: The reading of an event's coded escalation the map is cut by.
KINDS = ("sharp_escalation", "escalation", "de-escalation", "stable")

#: The window a market's headline number is read at: three sessions catches
#: the first reaction and the first correction; one session is the Gulf/US
#: calendar artefact, five dilutes into the next news.
HEADLINE_WINDOW = "car_0_3"

#: Measurements a (market, kind, window) cell needs before its median is a
#: number rather than an anecdote.
MIN_CELL = 8

#: Significance gate already used by the sensor loop. The map used to ignore
#: p-values; clean cells apply it. Dual with the unfiltered `response`.
CLEAN_P_GATE = 0.1


def kind_of(direction: str | None, magnitude: float | None) -> str:
    """Which reading of the escalation coding an event falls under.

    Head B emits `deescalating` (no hyphen); some graph rows still carry
    `de-escalating`. Both are the same direction. A live overlay that only
    accepted the hyphen silently dumped every de-escalation into `stable`
    and attached no market cell.
    """
    if direction == "escalating":
        return "sharp_escalation" if (magnitude or 0.0) >= SHARP_MAGNITUDE else "escalation"
    if direction in ("de-escalating", "deescalating"):
        return "de-escalation"
    return "stable"


def _weekend(date: Any) -> bool:
    """Friday, Saturday or Sunday — the days a Sun–Thu and a Mon–Fri calendar
    disagree about the next session."""
    import datetime as dt

    try:
        return dt.date.fromisoformat(str(date)[:10]).weekday() >= 4
    except (TypeError, ValueError):
        return False


def _quantiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    n = len(ordered)

    def at(share: float) -> float:
        return ordered[min(n - 1, int(n * share))]

    return {"median": round(at(0.5), 6), "p25": round(at(0.25), 6), "p75": round(at(0.75), 6),
            "share_positive": round(sum(1 for v in ordered if v > 0) / n, 4)}


def coding_for(conn: Any, pack_name: str) -> dict[str, dict[str, Any]]:
    """event_id → Head B coding + actors, graph spine first then the corpus.

    Markets and game pricing used to JOIN AFFECTED to Event. After the graph
    copy of the wire is gone, the Event node is missing for GDELT ids; the
    scored corpus still has every field the quantile cut needs.
    """
    from core import packs as packs_module
    from core.wire import serving

    index: dict[str, dict[str, Any]] = {}
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event) WHERE NOT starts_with(e.node_id, 'event:gdelt-') "
        "AND (e.region_pack = $pack OR e.region_pack = '' OR e.region_pack IS NULL) "
        "OPTIONAL MATCH (e)-[:INITIATED_BY]->(i:Actor) "
        "OPTIONAL MATCH (e)-[:DIRECTED_AT]->(t:Actor) "
        "RETURN e.node_id AS event_id, e.event_time AS date, "
        "e.escalation_direction AS direction, e.escalation_magnitude AS magnitude, "
        "e.quad_class AS quad_class, e.region_pack AS region_pack, "
        "e.name AS name, i.node_id AS initiator_id, i.name AS initiator, "
        "t.node_id AS target_id, t.name AS target",
        {"pack": pack_name},
    )
    for row in rows:
        index[str(row["event_id"])] = row
    names: dict[str, str] = {}
    try:
        names = {a["id"]: a["name"] for a in packs_module.load(pack_name).actors}
    except Exception:  # noqa: BLE001 - names are display-only
        names = {}
    try:
        stream: Iterator[dict[str, Any]] = serving.iter_rows_of(pack_name)
    except Exception:  # noqa: BLE001 - corpus-less tests stay on the graph
        stream = iter(())
    for row in stream:
        event_id = str(row["node_id"])
        if event_id in index:
            continue
        index[event_id] = {
            "event_id": event_id,
            "date": row.get("event_time"),
            "direction": row.get("escalation_direction"),
            "magnitude": row.get("escalation_magnitude"),
            "quad_class": row.get("quad_class"),
            "region_pack": row.get("region_pack") or pack_name,
            "name": row.get("name") or "",
            "initiator_id": row.get("initiator_id"),
            "initiator": names.get(row.get("initiator_id") or "", row.get("initiator_id")),
            "target_id": row.get("target_id"),
            "target": names.get(row.get("target_id") or "", row.get("target_id")),
        }
    return index


def market_rows(
    conn: Any, ticker: str, region_pack: str, *,
    panel: Any | None = None, coding: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Every measured effect on one market for one region's events (the deep
    tier rides with every region: `region_pack = ''`). Small tuples only.

    Panel-first when a connection is handed in: that is where GDELT
    measurements live after the graph copy of the wire is gone. Tests without
    Postgres keep the graph path.
    """
    if panel is not None:
        coding = coding if coding is not None else coding_for(conn, region_pack)
        return _rows_from_panel(panel, ticker, region_pack, coding)
    return kuzu_store.query(
        conn,
        "MATCH (e:Event)-[a:AFFECTED]->(m:Market {ticker: $ticker}) "
        "WHERE e.region_pack = $pack OR e.region_pack = '' "
        "RETURN e.node_id AS event_id, e.escalation_direction AS direction, "
        "e.escalation_magnitude AS magnitude, e.event_time AS date, "
        "e.quad_class AS quad_class, "
        "a.window AS window, a.abnormal_return AS ar, a.first_mover AS first_mover, "
        "a.overlapping AS overlapping, a.p_value AS p_value, a.t_stat AS t_stat",
        {"ticker": ticker, "pack": region_pack},
    )


def _rows_from_panel(
    panel: Any, ticker: str, region_pack: str, coding: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for run in pg_store.computed_runs(panel, ticker=ticker):
        meta = coding.get(str(run["event_node_id"]))
        if meta is None:
            continue
        pack = meta.get("region_pack") or ""
        if pack not in (region_pack, ""):
            continue
        out.append({
            "event_id": str(run["event_node_id"]),
            "direction": meta.get("direction"),
            "magnitude": meta.get("magnitude"),
            "date": meta.get("date"),
            "quad_class": meta.get("quad_class"),
            "initiator_id": meta.get("initiator_id"),
            "target_id": meta.get("target_id"),
            "window": run["window"],
            "ar": run["abnormal_return"],
            "first_mover": run.get("first_mover"),
            "overlapping": run.get("status") == "overlapping",
            "p_value": run.get("p_value"),
            "t_stat": run.get("t_stat"),
            "status": run.get("status"),
        })
    return out


def _measured_through(
    conn: Any, region_pack: str, *,
    panel: Any | None = None, coding: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    """The newest event that actually carries a measured effect for this pack.

    Distinct from the game context's `as_of`, which is the last complete
    quarter the solver opened on. A reader who sees only that date thinks
    the transmission map stopped in July."""
    if panel is not None and coding is not None:
        last: str | None = None
        for event_id in pg_store.computed_event_ids(panel):
            meta = coding.get(str(event_id))
            if meta is None:
                continue
            pack = meta.get("region_pack") or ""
            if pack not in (region_pack, ""):
                continue
            date = str(meta.get("date") or "")
            if date and (last is None or date > last):
                last = date
        return last
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event)-[:AFFECTED]->(:Market) "
        "WHERE e.region_pack = $pack OR e.region_pack = '' "
        "RETURN max(e.event_time) AS last",
        {"pack": region_pack},
    )
    last_row = rows[0]["last"] if rows else None
    return str(last_row) if last_row else None


#: How many bindings one event can produce in the pattern above. Six is
#: generous against the duplicates seen in production (four per event) and
#: costs one query's rows, not a second query.
_DUPLICATE_HEADROOM = 6


def biggest_moves(
    conn: Any, ticker: str, region_pack: str, *, roster: set[str], limit: int = 8,
    panel: Any | None = None, coding: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """The events that moved this market most, at the headline window — OF
    THIS REGION.

    THE ROSTER IS PART OF THE FILTER, and leaving it out is what put
    "Militarized dispute: South Korea – China" and "Russia – Ukraine (2008)"
    under "the events that moved US 2-Year Treasury yield most" on MENA's
    page. Deep-tier events carry `region_pack = ''` and ride with EVERY
    region, which is right for a quantile — more measurements behind the same
    median — and wrong for a named list, which is a claim about who did what
    here. Region membership is `impact.dyad_coverage`'s rule, reused exactly:
    an event counts for a pack when the pack's own wire coded it, or when it
    is deep tier and the pack's roster holds BOTH of its actors. The actor
    edges are the provenance invariant, so an event that cannot be placed is
    dropped rather than guessed at.
    """
    if not roster:
        return []
    if panel is not None:
        coding = coding if coding is not None else coding_for(conn, region_pack)
        return _biggest_from_panel(
            panel, ticker, region_pack, roster=roster, limit=limit, coding=coding,
        )
    rows = kuzu_store.query(
        conn,
        "MATCH (i:Actor)<-[:INITIATED_BY]-(e:Event)-[:DIRECTED_AT]->(t:Actor), "
        "(e)-[a:AFFECTED]->(m:Market {ticker: $ticker}) "
        "WHERE (e.region_pack = $pack OR e.region_pack = '') "
        "AND i.node_id IN $roster AND t.node_id IN $roster "
        "AND a.window = $window AND a.abnormal_return IS NOT NULL "
        "RETURN e.node_id AS event_id, e.name AS name, e.event_time AS date, "
        "e.escalation_direction AS direction, e.escalation_magnitude AS magnitude, "
        "i.name AS initiator, t.name AS target, a.abnormal_return AS ar, "
        "a.first_mover AS first_mover "
        "ORDER BY abs(a.abnormal_return) DESC LIMIT $limit",
        {"ticker": ticker, "pack": region_pack, "window": HEADLINE_WINDOW,
         # OVER-FETCHED BECAUSE THE PATTERN MULTIPLIES. The MATCH binds one row
         # per (initiator edge x target edge x AFFECTED edge), and the wire's
         # events are written with CREATE rather than MERGE, so an event that
         # picked up a second actor edge returns the same measurement several
         # times. Gold's "events that moved it most" was one agreement printed
         # four times and then a second printed four times (2026-08-17). The
         # duplicates are collapsed below; the over-fetch is what keeps eight
         # DISTINCT events available to collapse into.
         "roster": sorted(roster), "limit": limit * _DUPLICATE_HEADROOM},
    )
    # DEDUPED TWICE, because the archive repeats an event in two ways. The
    # pattern above multiplies one event by its actor edges (same id, several
    # rows), and the WIRE ITSELF codes one happening several times — GDELT
    # carries "Engage in negotiation: United States → Turkey" and "… Turkey →
    # United States" for the same day as separate events, and a market's
    # measured reaction to them is by construction identical. Both read to a
    # human as the same line printed twice, which is what "the events that moved
    # it most" showed on 2026-08-17: one negotiation, three rows, all -16.0%.
    # A day and a measured move identify the happening; the id identifies the
    # coding of it.
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    happenings: set[tuple[str, float]] = set()
    for r in rows:
        event_id = str(r["event_id"])
        if event_id in seen:
            continue
        seen.add(event_id)
        happening = (str(r["date"])[:10], round(float(r["ar"]), 4))
        if happening in happenings:
            continue
        happenings.add(happening)
        out.append({
            "event_id": event_id, "name": r["name"], "date": r["date"],
            "kind": kind_of(r["direction"], r["magnitude"]),
            "pair": (f"{r['initiator']} → {r['target']}"
                     if r.get("initiator") and r.get("target") else None),
            "abnormal_return": round(float(r["ar"]), 6),
            "first_mover": bool(r["first_mover"]),
        })
        if len(out) == limit:
            break
    return out


def _biggest_from_panel(
    panel: Any, ticker: str, region_pack: str, *,
    roster: set[str], limit: int, coding: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for run in pg_store.computed_runs(panel, ticker=ticker, window=HEADLINE_WINDOW):
        meta = coding.get(str(run["event_node_id"]))
        if meta is None or run.get("abnormal_return") is None:
            continue
        pack = meta.get("region_pack") or ""
        initiator = meta.get("initiator_id")
        target = meta.get("target_id")
        if pack == region_pack:
            pass
        elif pack == "":
            if initiator not in roster or target not in roster:
                continue
        else:
            continue
        ranked.append({
            "event_id": str(run["event_node_id"]),
            "name": meta.get("name"),
            "date": meta.get("date"),
            "direction": meta.get("direction"),
            "magnitude": meta.get("magnitude"),
            "initiator": meta.get("initiator"),
            "target": meta.get("target"),
            "ar": run["abnormal_return"],
            "first_mover": run.get("first_mover"),
        })
    ranked.sort(key=lambda r: abs(float(r["ar"])), reverse=True)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    happenings: set[tuple[str, float]] = set()
    for r in ranked:
        event_id = str(r["event_id"])
        if event_id in seen:
            continue
        seen.add(event_id)
        happening = (str(r["date"])[:10], round(float(r["ar"]), 4))
        if happening in happenings:
            continue
        happenings.add(happening)
        out.append({
            "event_id": event_id, "name": r["name"], "date": r["date"],
            "kind": kind_of(r["direction"], r["magnitude"]),
            "pair": (f"{r['initiator']} → {r['target']}"
                     if r.get("initiator") and r.get("target") else None),
            "abnormal_return": round(float(r["ar"]), 6),
            "first_mover": bool(r["first_mover"]),
        })
        if len(out) == limit:
            break
    return out


def _is_gdelt(event_id: Any) -> bool:
    return str(event_id or "").startswith("event:gdelt-")


def _dedup_gdelt(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One happening, one row — GDELT codes the same day twice as two ids."""
    seen: set[tuple[str, str, float]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("ar") is None or not _is_gdelt(row.get("event_id")):
            out.append(row)
            continue
        key = (
            str(row.get("date") or "")[:10],
            str(row.get("window") or ""),
            round(float(row["ar"]), 4),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _clean_row(row: dict[str, Any], *, as_of: str | None) -> bool:
    """Regime-gated, non-overlapping, and (when present) significant."""
    if row.get("overlapping") is True or str(row.get("status") or "") == "overlapping":
        return False
    p_value = row.get("p_value")
    if p_value is not None:
        try:
            if float(p_value) >= CLEAN_P_GATE:
                return False
        except (TypeError, ValueError):
            pass
    if not as_of or not row.get("date"):
        return True
    return regimes_comparable(as_of, str(row["date"]))


def regimes_comparable(as_of: str, date: str) -> bool:
    from core.reasoning import regimes

    return regimes.comparable(as_of, date)


def _cells_from(rows: list[dict[str, Any]]) -> tuple[
    dict[tuple[str, str], list[float]], dict[str, list[bool]], list[str],
]:
    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    first: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        if row["ar"] is None:
            continue
        kind = kind_of(row["direction"], row["magnitude"])
        cells[(kind, str(row["window"]))].append(float(row["ar"]))
        if str(row["window"]) == HEADLINE_WINDOW and _weekend(row.get("date")):
            first[kind].append(bool(row["first_mover"]))
    windows = sorted({w for _, w in cells})
    return cells, first, windows


def _response_from_cells(
    cells: dict[tuple[str, str], list[float]], windows: list[str],
) -> dict[str, dict[str, Any]]:
    response: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        by_window: dict[str, Any] = {}
        for window in windows:
            values = cells.get((kind, window), [])
            if len(values) >= MIN_CELL:
                by_window[window] = {"n": len(values), **_quantiles(values)}
            elif values:
                by_window[window] = {"n": len(values), "thin": True, **_quantiles(values)}
        response[kind] = by_window
    return response


def _headline_of(response: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for kind in ("sharp_escalation", "escalation"):
        cell = response.get(kind, {}).get(HEADLINE_WINDOW)
        if cell and not cell.get("thin"):
            return {"kind": kind, **cell}
    return None


def market_response(
    rows: list[dict[str, Any]], *, as_of: str | None = None,
) -> dict[str, Any]:
    """One market's measured response, cut by kind and window — pure.

    `response` / `headline` keep the historical mix (overlaps in, no regime
    gate) so a published median does not silently move. `clean_response` /
    `clean_headline` are the dual: GDELT-deduped, non-overlapping, p-gated,
    and regime-gated when `as_of` is given.
    """
    deduped = _dedup_gdelt(rows)
    cells, first, windows = _cells_from(deduped)
    response = _response_from_cells(cells, windows)
    clean_rows = [row for row in deduped if _clean_row(row, as_of=as_of)]
    clean_cells, _, clean_windows = _cells_from(clean_rows)
    clean_response = _response_from_cells(clean_cells, clean_windows or windows)
    return {
        "measured": sum(len(v) for v in cells.values()),
        "windows": windows,
        "response": response,
        "headline": _headline_of(response),
        "clean_response": clean_response,
        "clean_headline": _headline_of(clean_response),
        "first_mover_share": {
            kind: round(sum(1 for f in flags if f) / len(flags), 4)
            for kind, flags in first.items() if flags
        },
    }


def _skill_observation(
    row: dict[str, Any], *, ticker: str, market_type: str, market_id: str, pack: str,
) -> dict[str, Any] | None:
    from core.reasoning.transmission_skill import observation_from_row

    return observation_from_row(
        row, ticker=ticker, market_type=market_type, market_id=market_id, pack=pack,
    )


def _compact_story_skill(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Cheap in-process walk — the rows are already in memory for the story."""
    if not observations:
        return None
    from core.reasoning import transmission_skill as skill_module

    return skill_module.compact_skill(skill_module.walk(observations, clean=True))


def story(
    conn: Any, pack: Any, *,
    game_map: dict[str, Any] | None,
    duration: dict[str, Any] | None,
    flows: list[dict[str, Any]],
    coverage: dict[str, Any] | None,
    as_of: str | None,
    dyad_names: dict[str, str] | None = None,
    panel: Any | None = None,
) -> dict[str, Any]:
    """The whole markets story for one region — assembled from measured
    effects, the persisted game map, the curve report and the SWF flows."""
    roster = {str(actor["id"]) for actor in pack.actors}
    coding = coding_for(conn, pack.name) if panel is not None else None
    markets: list[dict[str, Any]] = []
    skill_obs: list[dict[str, Any]] = []
    for market in pack.markets:
        ticker = str(market["ticker"])
        rows = market_rows(
            conn, ticker, pack.name, panel=panel, coding=coding,
        )
        shape = market_response(rows, as_of=as_of)
        markets.append({
            "ticker": ticker,
            "name": market.get("name", ticker),
            "market_type": market.get("market_type"),
            "inception_date": market.get("inception_date"),
            "trading_calendar": market.get("trading_calendar"),
            **shape,
            "strategy_signal": strategy.assess_cell(
                shape.get("headline"),
                transaction_cost_bps=strategy.ROUND_TRIP_COST_BPS,
            ),
            "biggest_moves": (
                biggest_moves(
                    conn, ticker, pack.name, roster=roster,
                    panel=panel, coding=coding,
                )
                if shape["measured"] else []
            ),
        })
        for row in rows:
            if str(row.get("window") or "") != HEADLINE_WINDOW:
                continue
            lifted = _skill_observation(
                row, ticker=ticker,
                market_type=str(market.get("market_type") or ""),
                market_id=str(market.get("id") or ""),
                pack=pack.name,
            )
            if lifted is not None:
                skill_obs.append(lifted)
    # RANKED BY THE HEADLINE: how far a sharp escalation moves the market at
    # the headline window, largest |median| first — the transmission map's
    # order. Markets without a headline (thin, or unmeasured) go last, and say
    # so rather than being dropped.
    markets.sort(key=lambda m: (
        m["headline"] is None,
        -abs(float(m["headline"]["median"])) if m["headline"] else 0.0,
    ))

    forward = _forward_from_map(game_map)
    sovereign = _sovereign(flows)
    impact = strategy.market_impact(
        markets,
        transaction_cost_bps=strategy.ROUND_TRIP_COST_BPS,
    )
    payload = {
        # THE KEY AND THE CAPTION ARE DIFFERENT FIELDS. `region` is the pack
        # key — what every `region=` parameter takes and what every record
        # carries in `region_pack` — and the prose below used to render it, so
        # the page said "when mena's coded record shows a sharp escalation".
        # `region_label` is what the pack itself declares it should be CALLED
        # (`Pack.label` ← `region_label` in its actors.yaml).
        "region": pack.name,
        "region_label": pack.label,
        # `as_of` is the GAME CONTEXT's last quarter — what the solved map
        # opened on. The markets page used to label it "archive runs to",
        # which made a July opening look like the transmission map stopped
        # in July while the wire ran to mid-August. `game_as_of` is the same
        # number under its real name; `measured_through` is the newest event
        # that actually carries an AFFECTED edge.
        "as_of": as_of,
        "game_as_of": as_of,
        "measured_through": _measured_through(
            conn, pack.name, panel=panel, coding=coding,
        ),
        "markets": markets,
        "market_impact": impact,
        "strategy": strategy.strategy_contract(),
        "forward": forward,
        "duration": _trim_duration(
            duration, dyad_display_names(conn, _kept_duration_dyads(duration), dyad_names)
        ),
        "sovereign_capital": sovereign,
        "coverage": coverage,
        "transmission_skill": _compact_story_skill(skill_obs),
        "method": (
            "Every number is a quantile of measured event-study rows (the "
            "transmission engine's abnormal returns, calendar-aware, per "
            "event × market × window; Postgres is the store, the graph AFFECTED "
            "copy is optional) or a field of the persisted game map; events "
            "are cut by Head B's escalation coding "
            f"(sharp = a departure of {SHARP_MAGNITUDE:g}+ Goldstein points from the "
            f"pair's own baseline); the headline window is {HEADLINE_WINDOW}; a cell "
            f"under {MIN_CELL} measurements is flagged thin. The strategy layer "
            f"applies a fixed {strategy.ROUND_TRIP_COST_BPS:g} bps round-trip "
            "hurdle and labels thin or sub-hurdle cells; it does not fit a "
            "threshold to the resulting equity curve."
        ),
    }
    payload["explanation"] = explain(payload)
    return payload


def _forward_from_map(game_map: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the solved games imply for markets: the escalatory courses with
    the most mass across the region's pairs, each with its priced markets."""
    if not game_map:
        return None
    # ALLIED PAIRS ARE NOT WHERE THE RISK POINTS. `scenarios_escalatory` pools
    # every family's pressing course, and an alliance's is one partner
    # declining to carry the alliance — a rift, not a confrontation. On MENA the
    # top four were all allied pairs withholding, so this beat, and the pooled
    # direction computed from it, described alliance friction while the page
    # presented it as the region's escalation risk (2026-08-17). The ally
    # courses keep their own place on the game page; here they are dropped, and
    # how many were is reported rather than silently absorbed.
    ranked = [
        sc for sc in (game_map.get("scenarios_escalatory") or [])
        if ((sc.get("family") or {}).get("family")) != "ally"
    ]
    allies_dropped = len(game_map.get("scenarios_escalatory") or []) - len(ranked)
    courses = []
    for sc in ranked[:6]:
        courses.append({
            "dyad_name": sc.get("dyad_name"),
            "kind": sc.get("kind"),
            "kind_label": sc.get("kind_label"),
            "kind_sentence": sc.get("kind_sentence"),
            # WHICH GAME THE PAIR PLAYS, carried so a reader surface can tell an
            # alliance's rift-course from a rivalry's escalation. Without it the
            # markets page listed Syria-Lebanon (formal allies since 1945) under
            # "where the games point next" and, having only the kind KEY to work
            # from, rendered it as "both sides escalate at 95%" beside a label
            # reading "mutual withholding" (2026-08-17).
            "family": sc.get("family"),
            "likelihood": sc.get("likelihood"),
            "end_label": sc.get("end_label"),
            "market_implications": (sc.get("market_implications") or [])[:4],
        })
    # Pooled: per market, the likelihood-weighted median across the listed
    # courses — a direction, stated as such.
    pooled: dict[str, dict[str, Any]] = {}
    for course in courses:
        weight = float(course.get("likelihood") or 0.0)
        for row in course["market_implications"]:
            slot = pooled.setdefault(row["market_id"], {
                "market_id": row["market_id"], "market_name": row.get("market_name"),
                "weighted": 0.0, "weight": 0.0, "n": 0, "courses": 0,
            })
            slot["weighted"] += weight * float(row["median"])
            slot["weight"] += weight
            slot["n"] += int(row.get("n") or 0)
            slot["courses"] += 1
    direction = [
        {"market_id": s["market_id"], "market_name": s["market_name"],
         "expected_abnormal_return": round(s["weighted"] / s["weight"], 6),
         "measurements": s["n"], "courses": s["courses"]}
        for s in pooled.values() if s["weight"] > 0
    ]
    direction.sort(key=lambda r: -abs(r["expected_abnormal_return"]))
    return {
        "as_of": game_map.get("as_of"),
        "computed_at": game_map.get("computed_at"),
        "courses": courses,
        "direction": direction,
        "allied_courses_excluded": allies_dropped,
        "note": (
            "likelihood-weighted medians of measured moves after comparable events, "
            "over the pressing courses of the region's adversary and rival pairs; a "
            "direction the game points in, not a forecast of a price"
            + (
                f". {allies_dropped} allied pairs' friction courses were left out — "
                "a partner declining to carry an alliance is a rift, not a "
                "confrontation, and pricing it as one is what this excludes"
                if allies_dropped else ""
            )
        ),
    }


#: What a duration row is called when NEITHER side resolves to an actor the
#: graph names. Never the key: `dyad:cow-365--cow-372` is an internal id and
#: the page printed it as if it were a pair of countries.
UNNAMED_DYAD = "a pair the graph holds no names for"

#: Duration rows the payload keeps: the ones the curve could actually read.
_DURATION_ROWS = 8


def _kept_duration_dyads(duration: dict[str, Any] | None) -> list[str]:
    """The dyad ids of the rows `_trim_duration` will keep — so names are
    resolved for exactly those, and the two selections cannot drift apart."""
    return [
        str(d.get("dyad_id") or "")
        for d in (duration or {}).get("dyads") or [] if not d.get("thin")
    ][:_DURATION_ROWS]


def dyad_display_names(
    conn: Any, dyad_ids: list[str], known: dict[str, str] | None = None
) -> dict[str, str]:
    """dyad_id → a human name, for the rows the panel's summary never named.

    THE DURATION REPORT IS KEYED BY DYADS RECONSTRUCTED FROM THE ACTOR EDGES
    (`pricing.dyad_of_event`), so it holds pairs the modelled panel does not —
    and those rows arrived with no `dyad_name` and printed their raw id. A dyad
    id IS the sorted actor pair (`escalation.dyad_id`), so the names are one
    Actor read away; ONE query for every unnamed row together, because under a
    writing loop a request's latency is its query COUNT, not its query cost.
    """
    from core.games import opening as opening_module

    out = dict(known or {})
    wanted = {
        dyad: opening_module.dyad_actors(dyad)
        for dyad in dyad_ids if dyad and not out.get(dyad)
    }
    if not wanted:
        return out
    ids = sorted({actor for pair in wanted.values() for actor in pair})
    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor) WHERE a.node_id IN $ids RETURN a.node_id AS node_id, a.name AS name",
        {"ids": ids},
    )
    names = {str(r["node_id"]): str(r["name"]) for r in rows if r["name"]}
    for dyad, (left, right) in wanted.items():
        a, b = names.get(left), names.get(right)
        if a and b:
            # The corpus's own construction (`wire/corpus.py`), so a resolved
            # name splits back into sides the same way everywhere.
            out[dyad] = f"{a}–{b}"
        elif a or b:
            out[dyad] = f"{a or b} and a side the graph does not name"
        else:
            out[dyad] = UNNAMED_DYAD
    return out


def _trim_duration(
    duration: dict[str, Any] | None, names: dict[str, str] | None = None
) -> dict[str, Any] | None:
    if not duration:
        return None
    names = names or {}
    return {
        "events_with_a_curve_response": duration.get("events_with_a_curve_response"),
        "tenors_measured": duration.get("tenors_measured"),
        "usable_dyads": duration.get("usable_dyads"),
        "dyads": [
            {**d, "dyad_name": names.get(str(d.get("dyad_id")), UNNAMED_DYAD)}
            for d in (duration.get("dyads") or []) if not d.get("thin")
        ][:_DURATION_ROWS],
        "calibration": duration.get("calibration"),
        "note": duration.get("note"),
        "method": duration.get("method"),
    }


def _sovereign(flows: list[dict[str, Any]]) -> dict[str, Any]:
    """The pack's sovereign wealth funds' reported US equity exposure — the
    latest quarter per fund, and its move from the quarter before. Coarse,
    lagged, US-long-only, and said so."""
    by_fund: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in flows:
        by_fund[str(row["actor_id"])].append(row)
    funds: list[dict[str, Any]] = []
    for actor_id, rows in by_fund.items():
        rows.sort(key=lambda r: str(r["as_of"]))
        latest = rows[-1]
        previous = rows[-2] if len(rows) > 1 else None
        value = float(latest.get("value_usd") or 0.0)
        # A quarter-on-quarter change needs a REPORTED prior quarter with a
        # value; a fund's first filing, or a prior quarter carrying no value,
        # is not a $1tn inflow.
        before = (float(previous["value_usd"]) if previous and previous.get("value_usd")
                  else None)
        funds.append({
            "actor_id": actor_id,
            "name": latest.get("actor_name"),
            "as_of": latest.get("as_of"),
            "value_usd": value,
            "previous_as_of": previous.get("as_of") if previous else None,
            "change_usd": (value - before) if before else None,
            "quarters_reported": len(rows),
        })
    funds.sort(key=lambda f: -float(f["value_usd"] or 0.0))
    return {
        "funds": funds,
        "note": (
            "13F filings: reported US long equity holdings at quarter end, 45 days "
            "lagged — where sovereign capital SAT, never what it did since"
        ),
    }


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def explain(payload: dict[str, Any]) -> list[str]:
    """Paragraphs written from the payload's own fields."""
    # THE CAPTION, NOT THE KEY. `region` is `mena`; what a reader is owed is
    # what the pack calls itself. A payload frozen before the label existed
    # falls back to the key rather than to nothing.
    region = payload.get("region_label") or payload["region"]
    out: list[str] = []
    headlined = [m for m in payload["markets"] if m.get("headline")]
    if headlined:
        parts = []
        for m in headlined[:4]:
            h = m["headline"]
            window = HEADLINE_WINDOW.replace("_", "–").replace("car", "CAR")
            parts.append(
                f"{m['name']} {_pct(float(h['median']))} (median {window}, "
                f"n={h['n']}, {h['share_positive']:.0%} positive)"
            )
        kind = headlined[0]["headline"]["kind"].replace("_", " ")
        out.append(
            f"When {region}'s coded record shows a {kind}, the measured reaction is: "
            + "; ".join(parts) + "."
        )
    else:
        out.append(
            f"No market yet holds enough measured effects for {region} to state a "
            "headline reaction — the transmission engine is still measuring."
        )
    movers = [m for m in payload["markets"] if m.get("first_mover_share")]
    gulf = [m for m in movers if m.get("trading_calendar") == "gulf"]
    if gulf:
        g = gulf[0]
        shares = g["first_mover_share"]
        share = shares.get("sharp_escalation") or shares.get("escalation")
        if share is not None:
            out.append(
                f"{g['name']} trades Sunday–Thursday and, on escalations that landed on a "
                f"Friday, Saturday or Sunday, printed first {share:.0%} of the time — the "
                "weekend gap between Gulf and US sessions is real information, not "
                "bookkeeping."
            )
    fwd = payload.get("forward") or {}
    if fwd.get("direction"):
        lead = fwd["direction"][0]
        out.append(
            f"The solved games point at {lead['market_name']} "
            f"{_pct(float(lead['expected_abnormal_return']))} over the region's escalatory "
            f"courses with the most mass ({lead['courses']} courses, {lead['measurements']} "
            "measured moves behind them) — a direction, not a price."
        )
    dur = payload.get("duration") or {}
    if dur.get("dyads"):
        d = max(dur["dyads"], key=lambda r: float(r.get("implied_persistence") or 0.0))
        out.append(
            f"The yield curve's read on duration: {d.get('dyad_name') or UNNAMED_DYAD} "
            f"carries the largest long-end share of the curve's response "
            f"({float(d.get('implied_persistence', 0.0)):.0%} at the 10Y over {d.get('n', 0)} "
            "events) — the shape of a crisis markets expect to last."
        )
    sov = payload.get("sovereign_capital") or {}
    if sov.get("funds"):
        f = sov["funds"][0]
        out.append(
            f"Sovereign capital: {f['name']} reported ${f['value_usd'] / 1e9:.1f}bn of US "
            f"equity at {f['as_of']}"
            + (
                f", {'+' if f['change_usd'] >= 0 else '−'}"
                f"${abs(f['change_usd']) / 1e9:.1f}bn on the quarter before"
                if f.get("change_usd") is not None else ""
            )
            + " — a coarse, lagged, US-long-only view."
        )
    cov = (payload.get("coverage") or {}).get("summary") or {}
    if cov.get("events_measured") is not None:
        out.append(
            f"Coverage: {cov['events_measured']:,} of {cov.get('events', 0):,} events "
            f"in this region carry a measured market effect "
            f"({cov.get('share_measured', 0.0):.0%}); every cell states its sample."
        )
    return out
