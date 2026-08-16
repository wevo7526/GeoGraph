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
from typing import Any

from core.graph import kuzu_store

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


def kind_of(direction: str | None, magnitude: float | None) -> str:
    """Which reading of the escalation coding an event falls under."""
    if direction == "escalating":
        return "sharp_escalation" if (magnitude or 0.0) >= SHARP_MAGNITUDE else "escalation"
    if direction == "de-escalating":
        return "de-escalation"
    return "stable"


def _quantiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    n = len(ordered)

    def at(share: float) -> float:
        return ordered[min(n - 1, int(n * share))]

    return {"median": round(at(0.5), 6), "p25": round(at(0.25), 6), "p75": round(at(0.75), 6),
            "share_positive": round(sum(1 for v in ordered if v > 0) / n, 4)}


def market_rows(conn: Any, ticker: str, region_pack: str) -> list[dict[str, Any]]:
    """Every measured effect on one market for one region's events (the deep
    tier rides with every region: `region_pack = ''`). Small tuples only."""
    return kuzu_store.query(
        conn,
        "MATCH (e:Event)-[a:AFFECTED]->(m:Market {ticker: $ticker}) "
        "WHERE e.region_pack = $pack OR e.region_pack = '' "
        "RETURN e.escalation_direction AS direction, e.escalation_magnitude AS magnitude, "
        "a.window AS window, a.abnormal_return AS ar, a.first_mover AS first_mover, "
        "a.overlapping AS overlapping",
        {"ticker": ticker, "pack": region_pack},
    )


def biggest_moves(
    conn: Any, ticker: str, region_pack: str, *, limit: int = 8
) -> list[dict[str, Any]]:
    """The events that moved this market most, at the headline window."""
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event)-[a:AFFECTED]->(m:Market {ticker: $ticker}) "
        "WHERE (e.region_pack = $pack OR e.region_pack = '') AND a.window = $window "
        "AND a.abnormal_return IS NOT NULL "
        "OPTIONAL MATCH (e)-[:INITIATED_BY]->(i:Actor) "
        "OPTIONAL MATCH (e)-[:DIRECTED_AT]->(t:Actor) "
        "RETURN e.node_id AS event_id, e.name AS name, e.event_time AS date, "
        "e.escalation_direction AS direction, e.escalation_magnitude AS magnitude, "
        "i.name AS initiator, t.name AS target, a.abnormal_return AS ar, "
        "a.first_mover AS first_mover "
        "ORDER BY abs(a.abnormal_return) DESC LIMIT $limit",
        {"ticker": ticker, "pack": region_pack, "window": HEADLINE_WINDOW, "limit": limit},
    )
    return [
        {
            "event_id": r["event_id"], "name": r["name"], "date": r["date"],
            "kind": kind_of(r["direction"], r["magnitude"]),
            "pair": (f"{r['initiator']} → {r['target']}"
                     if r.get("initiator") and r.get("target") else None),
            "abnormal_return": round(float(r["ar"]), 6),
            "first_mover": bool(r["first_mover"]),
        }
        for r in rows
    ]


def market_response(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One market's measured response, cut by kind and window — pure."""
    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    first: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        if r["ar"] is None:
            continue
        kind = kind_of(r["direction"], r["magnitude"])
        cells[(kind, str(r["window"]))].append(float(r["ar"]))
        if str(r["window"]) == HEADLINE_WINDOW or str(r["window"]) in ("monthly", "annual"):
            first[kind].append(bool(r["first_mover"]))
    windows = sorted({w for _, w in cells})
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
    headline = None
    for kind in ("sharp_escalation", "escalation"):
        cell = response.get(kind, {}).get(HEADLINE_WINDOW)
        if cell and not cell.get("thin"):
            headline = {"kind": kind, **cell}
            break
    measured = sum(len(v) for v in cells.values())
    return {
        "measured": measured,
        "windows": windows,
        "response": response,
        "headline": headline,
        "first_mover_share": {
            kind: round(sum(1 for f in flags if f) / len(flags), 4)
            for kind, flags in first.items() if flags
        },
    }


def story(
    conn: Any, pack: Any, *,
    game_map: dict[str, Any] | None,
    duration: dict[str, Any] | None,
    flows: list[dict[str, Any]],
    coverage: dict[str, Any] | None,
    as_of: str | None,
    dyad_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The whole markets story for one region — assembled from measured
    effects, the persisted game map, the curve report and the SWF flows."""
    markets: list[dict[str, Any]] = []
    for market in pack.markets:
        ticker = str(market["ticker"])
        rows = market_rows(conn, ticker, pack.name)
        shape = market_response(rows)
        markets.append({
            "ticker": ticker,
            "name": market.get("name", ticker),
            "market_type": market.get("market_type"),
            "inception_date": market.get("inception_date"),
            "trading_calendar": market.get("trading_calendar"),
            **shape,
            "biggest_moves": biggest_moves(conn, ticker, pack.name) if shape["measured"] else [],
        })
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
    payload = {
        "region": pack.name,
        "as_of": as_of,
        "markets": markets,
        "forward": forward,
        "duration": _trim_duration(duration, dyad_names),
        "sovereign_capital": sovereign,
        "coverage": coverage,
        "method": (
            "Every number is a quantile of measured AFFECTED edges (the event study's "
            "abnormal returns, calendar-aware, per event × market × window) or a field "
            "of the persisted game map; events are cut by Head B's escalation coding "
            f"(sharp = a departure of {SHARP_MAGNITUDE:g}+ Goldstein points from the "
            f"pair's own baseline); the headline window is {HEADLINE_WINDOW}; a cell "
            f"under {MIN_CELL} measurements is flagged thin. Nothing here is modelled."
        ),
    }
    payload["explanation"] = explain(payload)
    return payload


def _forward_from_map(game_map: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the solved games imply for markets: the escalatory courses with
    the most mass across the region's pairs, each with its priced markets."""
    if not game_map:
        return None
    courses = []
    for sc in (game_map.get("scenarios_escalatory") or [])[:6]:
        courses.append({
            "dyad_name": sc.get("dyad_name"),
            "kind": sc.get("kind"),
            "kind_label": sc.get("kind_label"),
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
        "note": (
            "likelihood-weighted medians of measured moves after comparable events, "
            "over the region's escalatory courses with the most mass; a direction the "
            "game points in, not a forecast of a price"
        ),
    }


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
            {**d, "dyad_name": names.get(str(d.get("dyad_id")), str(d.get("dyad_id")))}
            for d in (duration.get("dyads") or []) if not d.get("thin")
        ][:8],
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
        before = float(previous.get("value_usd") or 0.0) if previous else None
        funds.append({
            "actor_id": actor_id,
            "name": latest.get("actor_name"),
            "as_of": latest.get("as_of"),
            "value_usd": value,
            "previous_as_of": previous.get("as_of") if previous else None,
            "change_usd": (value - before) if before is not None else None,
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
    region = payload["region"]
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
                f"{g['name']} trades Sunday–Thursday and printed first on {share:.0%} of "
                "escalations — the weekend gap between Gulf and US sessions is real "
                "information, not bookkeeping."
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
            f"The yield curve's read on duration: {d.get('dyad_name') or d.get('dyad_id')} "
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
