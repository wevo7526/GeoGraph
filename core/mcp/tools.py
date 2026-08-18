"""The MCP tool implementations — build-spec section 14.

THE AGENT SURFACE IS A DIFFERENT DESIGN TARGET FROM THE UI (MarketGraph
lesson, kept): tools return compact ROWS, capped and truncation-flagged,
never raw {nodes, edges} payloads — measured on MarketGraph, those cost
14K–20K tokens per call. Every result that reads the graph carries the
coverage caveat so an agent reports "the graph holds no X" narrowly and
truthfully.

Implemented now: find_actor, regime_at, the graph-backed shells, plus
markets_story, event_impact, region_call, and wire_live — thin, capped
wrappers over the same functions the API uses, never a second estimator.
The rest land with their layers and raise a phase-naming error until then —
an agent told "Phase 2" reports that; an agent given an empty result invents.
"""

from __future__ import annotations

from typing import Any

import kuzu

from core.graph import kuzu_store
from core.reasoning import regimes as regime_module

MAX_ROWS = 50

#: Attached to every graph-backed result. An agent that does not know the
#: archive currently holds one region pack and no deep tier will answer "nothing
#: happened in 1956" when the truthful answer is "this archive does not cover
#: 1956 yet" — a different claim, and the difference matters.
_COVERAGE = (
    "This archive holds the installed region packs' roster, the deep-tier "
    "spine, and a lean projection of the modern wire. Absence of an event "
    "here is not evidence it did not happen."
)


def find_actor(conn: kuzu.Connection, name: str) -> dict[str, Any]:
    """Case-insensitive substring match over Actor names."""
    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor) WHERE lower(a.name) CONTAINS lower($name) "
        "RETURN a.node_id AS node_id, a.name AS name, a.actor_type AS actor_type, "
        "a.state_from AS state_from, a.state_to AS state_to "
        "LIMIT $limit",
        {"name": name, "limit": MAX_ROWS + 1},
    )
    return {"rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS}


def neighbors(conn: kuzu.Connection, node_id: str) -> dict[str, Any]:
    """One hop out along TRAVERSABLE edges only — classification edges
    (OCCURRED_IN, DERIVED_FROM) would make everything two hops from
    everything. Per-rel queries because Kuzu rejects MATCH (n:A|B)."""
    from core.ontology import kuzu_schema as ontology

    rows: list[dict[str, Any]] = []
    for rel in ontology.traversable_edges():
        spec = ontology.edges()[rel]
        for direction, pattern in (
            ("out", f"(a:{spec.src} {{node_id: $id}})-[r:{rel}]->(b:{spec.dst})"),
            ("in", f"(b:{spec.src})-[r:{rel}]->(a:{spec.dst} {{node_id: $id}})"),
        ):
            rows += [
                {**row, "rel": rel, "direction": direction}
                for row in kuzu_store.query(
                    conn,
                    f"MATCH {pattern} RETURN b.node_id AS node_id, b.name AS name LIMIT $limit",
                    {"id": node_id, "limit": MAX_ROWS},
                )
            ]
    return {"rows": rows[:MAX_ROWS], "truncated": len(rows) > MAX_ROWS}


def regime_at(date: str) -> dict[str, Any]:
    """Which monetary order and polarity epoch a date sits in."""
    return regime_module.regimes_at(date)


def events_between(conn: kuzu.Connection, actor_a: str, actor_b: str,
                   start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Events on the dyad these two actors form, in time order.

    Matched through the DYAD rather than by walking INITIATED_BY and
    DIRECTED_AT in both directions, because the dyad is already the unordered
    pair: asking for events "between" two actors means the relationship, not
    one side's actions against the other.
    """
    from core.classifier import escalation

    params: dict[str, Any] = {"id": escalation.dyad_id(actor_a, actor_b), "limit": MAX_ROWS + 1}
    clauses = ["true"]
    # `end` is reserved in Kuzu and the trap covers PARAMETER names too, so a
    # bare `$end` is a parser error rather than a binding.
    if start:
        clauses.append("e.event_time >= $start_date")
        params["start_date"] = start
    if end:
        clauses.append("e.event_time <= $end_date")
        params["end_date"] = end
    rows = kuzu_store.query(
        conn,
        f"MATCH (e:Event)-[:OF_DYAD]->(d:Dyad {{node_id: $id}}) "
        f"WHERE {' AND '.join(clauses)} "
        "RETURN e.node_id AS node_id, e.name AS name, e.event_time AS event_time, "
        "e.action_cameo_code AS cameo_code, e.goldstein AS goldstein, "
        "e.escalation_direction AS escalation_direction, "
        "e.escalation_magnitude AS escalation_magnitude "
        "ORDER BY e.event_time, e.node_id LIMIT $limit",
        params,
    )
    return {
        "dyad": params["id"],
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        "coverage": _COVERAGE,
    }


def escalation_trajectory(conn: kuzu.Connection, dyad_id: str) -> dict[str, Any]:
    """One dyad's escalation history against its own moving baseline.

    The baseline column is the point: escalation here is RELATIONAL, so the
    same score means different things in different dyads, and a reader without
    the baseline cannot tell a routine act from a rupture.
    """
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event)-[:OF_DYAD]->(d:Dyad {node_id: $id}) "
        "RETURN e.event_time AS event_time, e.name AS name, "
        "e.goldstein AS goldstein, e.escalation_baseline AS baseline, "
        "e.escalation_direction AS direction, "
        "e.escalation_magnitude AS magnitude "
        "ORDER BY e.event_time, e.node_id LIMIT $limit",
        {"id": dyad_id, "limit": MAX_ROWS + 1},
    )
    return {
        "dyad": dyad_id,
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        "coverage": _COVERAGE,
    }


def network_metrics(conn: kuzu.Connection, window_start: str, window_end: str) -> dict[str, Any]:
    """Persisted structural measures for one window — deterministic numbers
    from graph/analytics.py, never derived at call time.

    An empty result means the window has not been COMPUTED, never that the
    network has no structure; the standard windows are decades and regime
    spans, so ask with those bounds first.
    """
    rows = kuzu_store.query(
        conn,
        "MATCH (m:NetworkMetric) "
        "WHERE m.window_start = $start_date AND m.window_end = $end_date "
        "RETURN m.subject_id AS subject_id, m.metric_name AS metric_name, "
        "m.value AS value, m.method AS method "
        "ORDER BY m.metric_name, m.subject_id LIMIT $limit",
        {"start_date": window_start, "end_date": window_end, "limit": MAX_ROWS + 1},
    )
    return {
        "window": f"{window_start}..{window_end}",
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
    }


def event_effects(conn: kuzu.Connection, event_id: str) -> dict[str, Any]:
    """Measured effects for one event: abnormal return, significance, flags.

    Only MEASURED effects appear. A market the engine skipped is absent here
    and recorded in the panel's event_study_runs, so an empty result means
    "nothing measured", never "no effect" — which is why `measured` is
    reported rather than left to be inferred from the row count.
    """
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: $id})-[a:AFFECTED]->(m:Market) "
        "RETURN m.ticker AS ticker, a.window AS window, a.resolution AS resolution, "
        "a.abnormal_return AS abnormal_return, a.t_stat AS t_stat, "
        "a.p_value AS p_value, a.first_mover AS first_mover, "
        "a.overlapping AS overlapping, a.method AS method "
        "ORDER BY ticker, window LIMIT $limit",
        {"id": event_id, "limit": MAX_ROWS + 1},
    )
    if not rows:
        rows = _effects_from_panel(event_id)
    return {
        "event": event_id,
        "measured": len(rows[:MAX_ROWS]),
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        "coverage": _COVERAGE,
    }


def _effects_from_panel(event_id: str) -> list[dict[str, Any]]:
    try:
        from core import settings as settings_module
        from core.panel import pg_store

        panel = pg_store.connect(settings_module.load())
    except Exception:  # noqa: BLE001 - graph-only MCP
        return []
    try:
        runs = pg_store.computed_runs(panel, event_id=event_id)
    finally:
        panel.close()
    return [
        {
            "ticker": r["market_ticker"],
            "window": r["window"],
            "resolution": r.get("resolution"),
            "abnormal_return": r["abnormal_return"],
            "t_stat": r.get("t_stat"),
            "p_value": r.get("p_value"),
            "first_mover": r.get("first_mover"),
            "overlapping": r.get("status") == "overlapping",
            "method": r.get("method"),
        }
        for r in runs
    ]


def analogues_for(conn: kuzu.Connection, query_ref: str) -> dict[str, Any]:
    """Regime-admissible structural analogues for an event already in the graph.

    Read-only: ranks, does not persist Analogue nodes (the MCP server holds a
    reader against the single-writer lock). The vector-index half is still
    unbuilt; this is the deterministic half that disposes.
    """
    from core.reasoning import analogy

    try:
        rows = analogy.rank_from_conn(conn, query_ref, k=min(MAX_ROWS, 8), persist=False)
    except KeyError as exc:
        return {"rows": [], "error": str(exc), "coverage": _COVERAGE}
    return {
        "rows": [
            {
                "event_id": r["event_id"],
                "similarity": r["similarity"],
                "regime_matched": r["regime_matched"],
                "rationale": r["rationale"],
            }
            for r in rows
        ],
        "truncated": False,
        "coverage": _COVERAGE,
    }


def forecast(
    conn: kuzu.Connection, question: str, mode: str = "near_term",
) -> dict[str, Any]:
    """The frozen Forecast nodes already in the graph — not a new computation.

    The question string is recorded, not parsed: this archive freezes calls
    offline and serves them. near_term is the three-year continuation window
    (modern-era base rate ~0.92-0.97); long_horizon carries the boundary
    statement and no likelihoods.
    """
    rows = kuzu_store.query(
        conn,
        "MATCH (f:Forecast) WHERE f.mode = $mode "
        "RETURN f.node_id AS node_id, f.region_pack AS region_pack, "
        "f.question AS question, f.generated_at AS generated_at, "
        "f.horizon_end AS horizon_end, "
        "f.boundary_statement AS boundary_statement, "
        "f.brier_score AS brier_score "
        "ORDER BY f.generated_at DESC LIMIT $limit",
        {"mode": mode, "limit": MAX_ROWS + 1},
    )
    return {
        "rows": rows[:MAX_ROWS],
        "truncated": len(rows) > MAX_ROWS,
        "asked": question,
        "mode": mode,
        "note": (
            "frozen Forecast nodes; this tool does not compute a new call from "
            "the question string. near_term marks P(escalate again within three "
            "years) — a nearly vacuous modern-era question. The game layer's "
            "sharp_departure_probability is the harder one."
        ),
        "coverage": _COVERAGE,
    }


def _panel() -> Any | None:
    try:
        from core import settings as settings_module
        from core.panel import pg_store

        return pg_store.connect(settings_module.load())
    except Exception:  # noqa: BLE001 - MCP without a panel still answers
        return None


def markets_story(region: str = "mena") -> dict[str, Any]:
    """The persisted markets story — same store the API reads. Compact headlines
    plus the transmission-skill block. Never recomputes the event study."""
    from core.panel import pg_store

    panel = _panel()
    if panel is None:
        return {
            "region": region,
            "pending": True,
            "headlines": [],
            "coverage": _COVERAGE,
            "note": "panel unavailable — nothing measured to report",
        }
    try:
        stored = pg_store.market_story(panel, region)
    finally:
        panel.close()
    if stored is None:
        return {
            "region": region,
            "pending": True,
            "headlines": [],
            "coverage": _COVERAGE,
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
        if len(headlines) >= 12:
            break
    return {
        "region": stored.get("region", region),
        "region_label": stored.get("region_label"),
        "as_of": stored.get("as_of"),
        "pending": False,
        "headlines": headlines,
        "transmission_skill": stored.get("transmission_skill"),
        "coverage": _COVERAGE,
        "truncated": sum(1 for m in (stored.get("markets") or []) if m.get("headline")) > 12,
    }


def event_impact(conn: kuzu.Connection, event_id: str) -> dict[str, Any]:
    """Measured vs expected vs surprise — same function as GET /api/impact/{id}."""
    from core.reasoning import impact as impact_module

    result = impact_module.event_impact(conn, event_id)
    if result is None:
        return {
            "event": event_id,
            "markets": [],
            "coverage": _COVERAGE,
            "note": "no such event, or nothing measured",
        }
    markets = list(result.get("markets") or [])
    return {
        "mode": result.get("mode"),
        "event": result.get("event"),
        "markets": markets[:12],
        "precedents": result.get("precedents"),
        "boundary_statement": result.get("boundary_statement"),
        "truncated": len(markets) > 12,
        "coverage": _COVERAGE,
    }


def region_call(region: str = "mena") -> dict[str, Any]:
    """The persisted region map's lead pair — not a live re-solve."""
    from core.games import scenarios
    from core.panel import pg_store

    panel = _panel()
    if panel is None:
        return {
            "region": region,
            "lead": None,
            "coverage": _COVERAGE,
            "note": "panel unavailable — no persisted region map to report",
        }
    try:
        stored = pg_store.game_solution(
            panel, region, scope="region", version=scenarios.PAYLOAD_VERSION
        )
    finally:
        panel.close()
    if stored is None:
        return {
            "region": region,
            "lead": None,
            "coverage": _COVERAGE,
            "note": "no persisted region map of the current shape",
        }
    ranking = stored.get("ranking") or []
    lead = ranking[0] if ranking else None
    return {
        "region": stored.get("region", region),
        "as_of": stored.get("as_of"),
        "dyads_solved": stored.get("dyads_solved"),
        "lead": lead,
        "coverage": _COVERAGE,
    }


def wire_live(region: str = "mena", limit: int = 12) -> dict[str, Any]:
    """Scored live overlay rows — the same GDELT 2.0 cache the Wire reads.

    Does not invent a number. An empty result means this process has no live
    batch yet, not that the region is quiet.
    """
    from core.wire import live as live_overlay

    cap = max(1, min(int(limit), 12))
    rows = list(live_overlay.rows_for(region) or [])
    if not rows:
        try:
            from core import packs

            live_overlay.refresh_pack(packs.load(region))
            rows = list(live_overlay.rows_for(region) or [])
        except Exception as exc:  # noqa: BLE001 - live feed failing is not a 500
            return {
                "region": region,
                "rows": [],
                "coverage": _COVERAGE,
                "note": f"no live batch: {exc}",
            }
    compact = []
    for row in rows[:cap]:
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
        "region": region,
        "rows": compact,
        "truncated": len(rows) > cap,
        "coverage": _COVERAGE,
        "note": None if compact else "no live batch in this process yet",
    }
