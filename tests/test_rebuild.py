"""AFFECTED is a projection of the panel, and the projection round-trips.

`event_study_runs` holds every computed effect's numbers, keyed as the edge
is. So a table that cannot be written (2026-08-16: SIGSEGV on every AFFECTED
write, every other writer clean, every AFFECTED read clean) can be dropped and
re-projected in minutes instead of re-measured over days. What must hold is
that the projection is EXACT: the same edges, the same numbers, the same
provenance, the same first-mover flags the study itself would write.
"""

from __future__ import annotations

import datetime as dt
import statistics
from pathlib import Path
from typing import Any

import pytest

from core.graph import kuzu_store
from core.packs import Pack
from core.transmission import effects as effects_writer
from core.transmission import event_study, rebuild, runner

_ESTIMATION = [0.001, -0.002, 0.0015, -0.001] * 30
_GAP = [0.0] * 5


def _series(event_date: dt.date, before: list[float], after: list[float]) -> list[dict[str, Any]]:
    """A daily price path around `event_date` (weekdays only)."""
    days: list[dt.date] = []
    cursor = event_date - dt.timedelta(days=1)
    while len(days) < len(before) + 1:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= dt.timedelta(days=1)
    days.reverse()
    cursor = event_date
    post: list[dt.date] = []
    while len(post) < len(after):
        if cursor.weekday() < 5:
            post.append(cursor)
        cursor += dt.timedelta(days=1)
    price = 100.0
    out = [{"obs_date": days[0].isoformat(), "price": price}]
    for when, ret in zip(days[1:] + post, before + after, strict=True):
        price *= 1.0 + ret
        out.append({"obs_date": when.isoformat(), "price": price})
    return out


def _pack(name: str, markets: list[dict[str, Any]]) -> Pack:
    return Pack(name=name, path=Path("."), data={
        "markets": {"markets": markets},
        "actors": {"actors": [], "relations": []},
        "marquee_events": {"events": []},
    })


@pytest.fixture()
def conn(tmp_path):
    connection = kuzu_store.connect(tmp_path / "rebuild.kuzu")
    kuzu_store.apply_schema(connection)
    yield connection
    kuzu_store.close(connection)


def _seed(conn: Any) -> tuple[Pack, Pack, list[dict[str, Any]]]:
    kuzu_store.merge_nodes(conn, "Source", [
        {"node_id": "source:yfinance", "name": "yfinance", "source_type": "dataset",
         "url": "https://example.invalid", "retrieved_at": "2026-08-16"},
        {"node_id": "source:test", "name": "Test", "source_type": "dataset",
         "url": "https://example.invalid", "retrieved_at": "2026-08-16"},
    ])
    kuzu_store.merge_nodes(conn, "Actor", [
        {"node_id": "actor:a", "name": "A", "actor_type": "state"},
        {"node_id": "actor:b", "name": "B", "actor_type": "state"},
    ])
    markets = [
        {"id": "market:spx", "ticker": "^GSPC", "name": "S&P 500", "market_type": "equity_index",
         "trading_calendar": "us", "inception_date": "2000-01-01",
         "native_frequency": '{"2000": "day"}'},
        {"id": "market:tasi", "ticker": "^TASI.SR", "name": "Tadawul",
         "market_type": "equity_index",
         "trading_calendar": "gulf", "inception_date": "2000-01-01",
         "native_frequency": '{"2000": "day"}'},
    ]
    kuzu_store.merge_nodes(conn, "Market", [
        {"node_id": m["id"], "name": m["name"], "ticker": m["ticker"],
         "market_type": m["market_type"], "trading_calendar": m["trading_calendar"],
         "calendar_eras": "", "inception_date": m["inception_date"],
         "native_frequency": m["native_frequency"], "region_pack": "mena"}
        for m in markets
    ])
    events = [
        {"node_id": "event:one", "name": "One", "event_time": "2025-06-13"},
        {"node_id": "event:two", "name": "Two", "event_time": "2025-06-14"},  # a Saturday
    ]
    kuzu_store.merge_nodes(conn, "Event", [
        {**e, "action_cameo_code": "190", "goldstein": -9.0,
         "quad_class": "material_conflict", "fidelity_tier": "modern_coded",
         "temporal_resolution": "day", "source_scale": "goldstein", "region_pack": "mena"}
        for e in events
    ])
    # A second pack sharing one market — brent-style: it must be written ONCE.
    mena = _pack("mena", markets)
    china = _pack("china", [markets[0]])
    return mena, china, events


def _measure(conn: Any, pack: Pack, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run the study the way the runner does and return PANEL-SHAPED rows —
    what `pg_store.record_runs` would have written — plus the graph edges."""
    panel_rows: list[dict[str, Any]] = []
    market_node_ids = {m["ticker"]: m["id"] for m in pack.markets}
    for event in events:
        date = dt.date.fromisoformat(event["event_time"])
        prices = {
            m["ticker"]: _series(date, _ESTIMATION + _GAP, [0.05, 0.0, 0.01, -0.02, 0.0, 0.0])
            for m in pack.markets
        }
        results, _skips = event_study.compute_effects(
            {"node_id": event["node_id"], "event_time": event["event_time"]},
            pack.markets, prices=prices,
        )
        by_source: dict[str, list[event_study.EffectResult]] = {}
        for r in results:
            by_source.setdefault(runner.effect_source(r), []).append(r)
        for source_id, group in by_source.items():
            effects_writer.write_effects(
                conn, group, market_node_ids=market_node_ids, source_id=source_id
            )
        for r in results:
            panel_rows.append({
                "event_node_id": r.event_node_id, "market_ticker": r.market_ticker,
                "effect_window": r.window, "resolution": r.resolution,
                "status": "overlapping" if r.overlapping else "computed",
                "raw_return": r.raw_return, "expected_return": r.expected_return,
                "abnormal_return": r.abnormal_return, "t_stat": r.t_stat,
                "p_value": r.p_value, "method": r.method,
            })
    return panel_rows


_EDGE_QUERY = (
    "MATCH (e:Event)-[a:AFFECTED]->(m:Market) "
    "RETURN e.node_id AS ev, m.ticker AS tk, a.window AS w, a.resolution AS res, "
    "a.raw_return AS raw, a.expected_return AS exp, a.abnormal_return AS abn, "
    "a.t_stat AS ts, a.p_value AS p, a.first_mover AS fm, a.overlapping AS ov, "
    "a.method AS method, a.source_id AS src ORDER BY ev, tk, w"
)


def test_the_panel_projects_back_onto_affected_exactly(conn):
    """Measure → drop → refill from panel rows → identical edges."""
    mena, china, events = _seed(conn)
    panel_rows = _measure(conn, mena, events)
    assert panel_rows, "the fixture must measure something"
    before = kuzu_store.query(conn, _EDGE_QUERY)
    assert len(before) == len(panel_rows)
    assert statistics.mean(1 if r["fm"] else 0 for r in before) < 1.0, (
        "the Saturday event must give Tadawul the first move alone — the flag "
        "carries information the refill has to re-derive")

    # The repair: drop, then project the panel back through the one door.
    kuzu_store.recreate_edge_table(conn, "AFFECTED")
    assert kuzu_store.query(conn, "MATCH ()-[a:AFFECTED]->() RETURN count(a) AS n")[0]["n"] == 0

    dates = rebuild.event_dates(conn)
    outcome = rebuild.refill(conn, panel_rows, [china, mena], dates)
    assert outcome["complete"] and not outcome["stopped_early"]
    assert outcome["edges_written"] == len(panel_rows), outcome
    after = kuzu_store.query(conn, _EDGE_QUERY)
    assert after == before, "the projection must be exact — numbers, flags, provenance"
    assert kuzu_store.check_provenance(conn) == []


def test_a_shared_market_is_written_once_by_the_broadest_pack(conn):
    mena, china, events = _seed(conn)
    panel_rows = _measure(conn, mena, events)
    kuzu_store.recreate_edge_table(conn, "AFFECTED")
    dates = rebuild.event_dates(conn)
    # china is listed first but names only ^GSPC; mena names both, so mena
    # OWNS ^GSPC (the broadest comparison set decides first_mover) and china
    # writes nothing. Every row written exactly once, and exactly.
    outcome = rebuild.refill(conn, panel_rows, [china, mena], dates)
    assert outcome["edges_written"] == len(panel_rows)
    n = kuzu_store.query(conn, "MATCH ()-[a:AFFECTED]->() RETURN count(a) AS n")[0]["n"]
    assert n == len(panel_rows)


def test_the_refill_resumes_from_its_marker(conn, tmp_path):
    """A budgeted slice stops at a chunk boundary and the next call resumes —
    without re-writing what landed, and without losing anything."""
    mena, _china, events = _seed(conn)
    panel_rows = _measure(conn, mena, events)
    kuzu_store.recreate_edge_table(conn, "AFFECTED")
    dates = rebuild.event_dates(conn)
    marker = rebuild.Marker(tmp_path / "marker.json")

    # A deadline already in the past stops after nothing (the deadline is
    # checked before each chunk) — the marker records "nothing yet".
    first = rebuild.refill(
        conn, panel_rows, [mena], dates, marker=marker, chunk_events=1, deadline=0.0
    )
    assert first["stopped_early"] and not first["complete"]
    assert first["events"] == 0

    second = rebuild.refill(conn, panel_rows, [mena], dates, marker=marker, chunk_events=1)
    assert second["complete"]
    assert second["edges_written"] == len(panel_rows)
    assert marker.state["done_packs"] == ["mena"]

    # Running again is a no-op: the pack is done in the marker.
    third = rebuild.refill(conn, panel_rows, [mena], dates, marker=marker)
    assert third["complete"] and third["edges_written"] == 0


def test_an_event_the_graph_no_longer_holds_is_dropped_and_counted(conn):
    mena, _china, events = _seed(conn)
    panel_rows = _measure(conn, mena, events)
    kuzu_store.recreate_edge_table(conn, "AFFECTED")
    dates = rebuild.event_dates(conn)
    ghost = [dict(r, event_node_id="event:gone") for r in panel_rows[:2]]
    outcome = rebuild.refill(conn, panel_rows + ghost, [mena], dates)
    assert outcome["edges_written"] == len(panel_rows)
    assert outcome["dropped"] == {"event no longer in the graph": 2}


def test_delete_edges_and_nodes_go_through_the_one_door(conn):
    """The probe removes the edge it created; the prune removes actors. Both
    must take the lock and honour key_slots — so both live in kuzu_store."""
    mena, _china, events = _seed(conn)
    _measure(conn, mena, events)
    rows = kuzu_store.query(conn, _EDGE_QUERY)
    victim = rows[0]
    market_id = {m["ticker"]: m["id"] for m in mena.markets}[victim["tk"]]
    deleted = kuzu_store.delete_edges(conn, "AFFECTED", [
        {"src": victim["ev"], "dst": market_id, "window": victim["w"]},
    ])
    assert deleted == 1
    left = kuzu_store.query(conn, _EDGE_QUERY)
    assert len(left) == len(rows) - 1
    assert not any(r["ev"] == victim["ev"] and r["tk"] == victim["tk"] and r["w"] == victim["w"]
                   for r in left), "only the named edge goes"

    # DETACH DELETE takes the node and every edge on it.
    kuzu_store.merge_edges(conn, "RELATES_TO", [
        {"src": "actor:a", "dst": "actor:b", "relation_type": "alliance",
         "valid_from": "1949-04-04", "source_id": "source:test"},
    ])
    assert kuzu_store.delete_nodes(conn, "Actor", ["actor:b"]) == 1
    assert kuzu_store.query(conn, "MATCH (a:Actor) RETURN count(a) AS n")[0]["n"] == 1
    assert kuzu_store.query(conn, "MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS n")[0]["n"] == 0
