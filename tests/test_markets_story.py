"""The markets story is written from measured effects and persisted solves —
every number a quantile of AFFECTED or a field of a payload, cut by the
escalation coding, with its sample stated."""

from __future__ import annotations

from typing import Any

import pytest

from core.graph import kuzu_store
from core.packs import Pack
from core.reasoning import markets


@pytest.fixture()
def conn(tmp_path):
    connection = kuzu_store.connect(tmp_path / "markets.kuzu")
    kuzu_store.apply_schema(connection)
    yield connection
    kuzu_store.close(connection)


def _seed(conn: Any) -> Pack:
    kuzu_store.merge_nodes(conn, "Source", [
        {"node_id": "source:test", "name": "Test", "kind": "dataset", "url": "", "citation": ""},
    ])
    kuzu_store.merge_nodes(conn, "Actor", [
        {"node_id": "actor:a", "name": "Alpha", "actor_type": "state"},
        {"node_id": "actor:b", "name": "Beta", "actor_type": "state"},
    ])
    markets_ = [
        {"id": "market:brent", "ticker": "BZ=F", "name": "Brent", "market_type": "commodity",
         "trading_calendar": "us", "inception_date": "1990-01-01", "native_frequency": ""},
        {"id": "market:tasi", "ticker": "^TASI.SR", "name": "Tadawul",
         "market_type": "equity_index",
         "trading_calendar": "gulf", "inception_date": "2001-01-01", "native_frequency": ""},
    ]
    kuzu_store.merge_nodes(conn, "Market", [
        {"node_id": m["id"], "name": m["name"], "ticker": m["ticker"],
         "market_type": m["market_type"], "trading_calendar": m["trading_calendar"],
         "calendar_eras": "", "inception_date": m["inception_date"], "native_frequency": "",
         "region_pack": "t"}
        for m in markets_
    ])
    events = []
    edges = []
    for i in range(36):
        sharp = i % 3 == 0
        direction = "escalating" if i % 4 != 3 else "de-escalating"
        events.append({
            "node_id": f"event:e{i}", "name": f"Event {i}",
            # Saturdays: the days a Sun–Thu and a Mon–Fri calendar diverge,
            # which is when first_mover carries information.
            "event_time": (
                f"2020-{1 + i % 12:02d}-{(4, 1, 7, 4, 2, 6, 4, 1, 5, 3, 7, 5)[i % 12]:02d}"
            ),
            "action_cameo_code": "190", "goldstein": -9.0, "quad_class": "material_conflict",
            "fidelity_tier": "modern_coded", "temporal_resolution": "day",
            "source_scale": "goldstein", "region_pack": "t",
            "escalation_direction": direction,
            "escalation_magnitude": 4.0 if sharp else 1.0, "escalation_baseline": -3.0,
        })
        for m in markets_:
            for w, ar in (("car_0_1", 0.01 if sharp else 0.002),
                          ("car_0_3", 0.02 if sharp else 0.004)):
                edges.append({
                    "src": f"event:e{i}", "dst": m["id"], "window": w, "resolution": "day",
                    "raw_return": ar, "expected_return": 0.0,
                    "abnormal_return": ar if direction == "escalating" else -ar,
                    "t_stat": 1.0, "p_value": 0.3,
                    "first_mover": m["ticker"] == "^TASI.SR", "overlapping": False,
                    "method": "test", "source_id": "source:test",
                })
    kuzu_store.merge_nodes(conn, "Event", events)
    kuzu_store.merge_edges(conn, "INITIATED_BY", [
        {"src": e["node_id"], "dst": "actor:a", "source_id": "source:test"} for e in events])
    kuzu_store.merge_edges(conn, "DIRECTED_AT", [
        {"src": e["node_id"], "dst": "actor:b", "source_id": "source:test"} for e in events])
    kuzu_store.write_edges(conn, "AFFECTED", edges)
    return Pack(name="t", path=tmp_path_placeholder(), data={
        "markets": {"markets": markets_}, "actors": {"actors": [], "relations": []},
        "marquee_events": {"events": []},
    })


def tmp_path_placeholder():
    from pathlib import Path
    return Path(".")


def test_the_story_is_quantiles_of_measured_effects_cut_by_the_coding(conn):
    pack = _seed(conn)
    payload = markets.story(conn, pack, game_map=None, duration=None, flows=[],
                            coverage=None, as_of="2020-12-31")
    by_ticker = {m["ticker"]: m for m in payload["markets"]}
    brent = by_ticker["BZ=F"]
    # Sharp escalations: nine events at car_0_3 — over the cell bar.
    sharp = brent["response"]["sharp_escalation"]["car_0_3"]
    assert sharp["n"] == 9 and not sharp.get("thin")
    assert sharp["median"] == pytest.approx(0.02)
    # De-escalations move the other way, and are stated as such.
    de = brent["response"]["de-escalation"]["car_0_3"]
    assert de["median"] < 0
    # The headline is the sharp cell at the headline window.
    assert brent["headline"]["kind"] == "sharp_escalation"
    assert brent["headline"]["median"] == pytest.approx(0.02)
    # Tadawul printed first every time (the Gulf calendar) — the share says so.
    assert by_ticker["^TASI.SR"]["first_mover_share"]["sharp_escalation"] == 1.0
    assert brent["first_mover_share"]["sharp_escalation"] == 0.0
    # The biggest moves are named events, largest |move| first.
    assert brent["biggest_moves"]
    assert abs(brent["biggest_moves"][0]["abnormal_return"]) == pytest.approx(0.02)
    assert brent["biggest_moves"][0]["pair"] == "Alpha → Beta"
    # The prose cites the payload.
    text = " ".join(payload["explanation"])
    assert "Brent" in text and "n=9" in text
    assert "Tadawul" in text and "Sunday" in text
    assert "still measuring" not in text


def test_an_unmeasured_region_says_so(conn):
    pack = _seed(conn)
    empty = Pack(name="empty", path=pack.path, data={
        "markets": {"markets": [{"id": "market:x", "ticker": "X", "name": "X",
                                 "market_type": "equity_index", "trading_calendar": "us",
                                 "inception_date": "2000-01-01", "native_frequency": ""}]},
        "actors": {"actors": [], "relations": []}, "marquee_events": {"events": []},
    })
    payload = markets.story(conn, empty, game_map=None, duration=None, flows=[],
                            coverage=None, as_of=None)
    assert payload["markets"][0]["headline"] is None
    assert "still measuring" in payload["explanation"][0]


def test_the_forward_map_pools_the_games_courses_by_likelihood():
    game_map = {"as_of": "2026-06-30", "scenarios_escalatory": [
        {"dyad_name": "A–B", "kind": "one_sided_pressure", "kind_label": "one-sided pressure",
         "likelihood": 0.6, "end_label": "sharp",
         "market_implications": [{"market_id": "market:brent", "market_name": "Brent",
                                  "median": 0.02, "n": 40}]},
        {"dyad_name": "C–D", "kind": "mutual_escalation", "kind_label": "mutual escalation",
         "likelihood": 0.2, "end_label": "sharp",
         "market_implications": [{"market_id": "market:brent", "market_name": "Brent",
                                  "median": -0.01, "n": 10}]},
    ]}
    fwd = markets._forward_from_map(game_map)
    assert fwd is not None
    lead = fwd["direction"][0]
    assert lead["market_name"] == "Brent"
    assert lead["expected_abnormal_return"] == pytest.approx((0.6 * 0.02 + 0.2 * -0.01) / 0.8)
    assert lead["courses"] == 2 and lead["measurements"] == 50
