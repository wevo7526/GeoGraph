"""The markets story is written from measured effects and persisted solves —
every number a quantile of AFFECTED or a field of a payload, cut by the
escalation coding, with its sample stated."""

from __future__ import annotations

from pathlib import Path
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
        "markets": {"markets": markets_},
        "actors": {
            # The roster IS the region filter for anything the page NAMES, so
            # a fixture without one is not this pack.
            "actors": [
                {"id": "actor:a", "name": "Alpha", "actor_type": "state"},
                {"id": "actor:b", "name": "Beta", "actor_type": "state"},
            ],
            "relations": [],
            # The caption, as distinct from the key — `t` is an id.
            "region_label": "Testland",
        },
        "marquee_events": {"events": []},
    })


def _deep_tier_event(
    conn: Any, *, event_id: str, name: str, initiator: str, target: str, ar: float
) -> None:
    """One event outside every pack's wire (`region_pack = ''`, the deep tier's
    own value) that moved Brent hard, between the actors given."""
    kuzu_store.merge_nodes(conn, "Actor", [
        {"node_id": initiator, "name": initiator.split(":")[-1], "actor_type": "state"},
        {"node_id": target, "name": target.split(":")[-1], "actor_type": "state"},
    ])
    kuzu_store.merge_nodes(conn, "Event", [{
        "node_id": event_id, "name": name, "event_time": "2008-08-07",
        "action_cameo_code": "190", "goldstein": -9.0, "quad_class": "material_conflict",
        "fidelity_tier": "deep_past", "temporal_resolution": "day",
        "source_scale": "cow", "region_pack": "",
        "escalation_direction": "escalating", "escalation_magnitude": 6.0,
        "escalation_baseline": -3.0,
    }])
    kuzu_store.merge_edges(conn, "INITIATED_BY", [
        {"src": event_id, "dst": initiator, "source_id": "source:test"}])
    kuzu_store.merge_edges(conn, "DIRECTED_AT", [
        {"src": event_id, "dst": target, "source_id": "source:test"}])
    kuzu_store.write_edges(conn, "AFFECTED", [{
        "src": event_id, "dst": "market:brent", "window": "car_0_3", "resolution": "day",
        "raw_return": ar, "expected_return": 0.0, "abnormal_return": ar,
        "t_stat": 1.0, "p_value": 0.3, "first_mover": False, "overlapping": False,
        "method": "test", "source_id": "source:test",
    }])


def tmp_path_placeholder() -> Path:
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
    # THE CAPTION, NOT THE KEY: the page said "when mena's coded record shows
    # a sharp escalation", rendering an internal pack key as a place.
    assert payload["region"] == "t" and payload["region_label"] == "Testland"
    assert "Testland's coded record" in text and "t's coded record" not in text
    assert payload["game_as_of"] == "2020-12-31"
    assert payload["measured_through"]


def test_the_named_moves_are_this_regions_events(conn):
    """A deep-tier event rides with every region for the QUANTILES and with
    none of them for the NAMED list.

    MENA's markets page listed "Militarized dispute: South Korea – China" and
    "Russia – Ukraine (2008)" under "the events that moved US 2-Year Treasury
    yield most" — deep-tier events (`region_pack = ''`) between actors no MENA
    pack rosters. The measurement belongs in the median (same event class,
    more sample); the NAME is a claim about who did what in this region.
    """
    pack = _seed(conn)
    _deep_tier_event(conn, event_id="event:mid-off", name="Militarized dispute: X – Y",
                     initiator="actor:x", target="actor:y", ar=0.9)
    _deep_tier_event(conn, event_id="event:mid-on", name="Militarized dispute: Alpha – Beta",
                     initiator="actor:a", target="actor:b", ar=0.5)
    payload = markets.story(conn, pack, game_map=None, duration=None, flows=[],
                            coverage=None, as_of="2026-08-16")
    brent = {m["ticker"]: m for m in payload["markets"]}["BZ=F"]
    named = [e["name"] for e in brent["biggest_moves"]]
    # The off-roster event is the single largest move on the market and is not
    # named; the deep-tier event between roster actors leads the list.
    assert "Militarized dispute: X – Y" not in named
    assert named[0] == "Militarized dispute: Alpha – Beta"
    # …and both were still measured: the quantiles read the whole deep tier.
    assert brent["measured"] == 36 * 2 + 2
    # A pack with no roster names nothing rather than naming another region.
    assert markets.biggest_moves(conn, "BZ=F", "t", roster=set()) == []


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


def test_a_duration_row_never_prints_its_dyad_id(conn):
    """The duration table is keyed by dyads RECONSTRUCTED FROM ACTOR EDGES, so
    it holds pairs the modelled panel never named — and the page printed
    "dyad:cow-365--cow-372" where a pair of countries belongs."""
    pack = _seed(conn)
    duration = {
        "events_with_a_curve_response": 12,
        "tenors_measured": ["front", "long"],
        "usable_dyads": 2,
        "dyads": [
            {"dyad_id": "dyad:a--b", "n": 9, "implied_persistence": 0.7,
             "p25": 0.6, "p75": 0.8, "thin": False},
            {"dyad_id": "dyad:cow-999--cow-998", "n": 6, "implied_persistence": 0.4,
             "p25": 0.3, "p75": 0.5, "thin": False},
        ],
    }
    payload = markets.story(conn, pack, game_map=None, duration=duration, flows=[],
                            coverage=None, as_of="2026-08-16")
    named = {d["dyad_id"]: d["dyad_name"] for d in payload["duration"]["dyads"]}
    # Resolved off the actor edges the dyad id IS.
    assert named["dyad:a--b"] == "Alpha–Beta"
    # And where the graph holds no actor, the absence is said in words.
    assert named["dyad:cow-999--cow-998"] == markets.UNNAMED_DYAD
    text = " ".join(payload["explanation"])
    assert "Alpha–Beta" in text and "dyad:" not in text


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


def test_one_happening_is_named_once(conn):
    """THE WIRE CODES ONE HAPPENING SEVERAL WAYS, and the page printed each.

    "The events that moved Tadawul most" opened with "Engage in negotiation:
    United States → Turkey" at −16.0%, then the same line again, then "Turkey →
    United States" at −16.0% — GDELT carries the directions and re-reports as
    separate events, and a market's measured reaction to them is by
    construction identical. A day and a measured move identify the happening.
    """
    pack = _seed(conn)
    for i, (initiator, target) in enumerate(
        [("actor:a", "actor:b"), ("actor:b", "actor:a"), ("actor:a", "actor:b")]
    ):
        _deep_tier_event(conn, event_id=f"event:same-{i}",
                         name=f"Engage in negotiation ({i})",
                         initiator=initiator, target=target, ar=0.42)
    _deep_tier_event(conn, event_id="event:other", name="A different day",
                     initiator="actor:a", target="actor:b", ar=0.31)

    brent = {m["ticker"]: m for m in markets.story(
        conn, pack, game_map=None, duration=None, flows=[], coverage=None,
        as_of="2026-08-16")["markets"]}["BZ=F"]
    named = brent["biggest_moves"]
    at_42 = [e for e in named if abs(e["abnormal_return"] - 0.42) < 1e-9]
    assert len(at_42) == 1, "one happening, named once"
    # …and a genuinely different measurement on the same day still gets a line.
    assert any(abs(e["abnormal_return"] - 0.31) < 1e-9 for e in named)


def test_an_alliances_friction_is_not_where_the_risk_points(conn):
    """`scenarios_escalatory` pools every family's pressing course, and an
    alliance's is a partner declining to carry the alliance. MENA's four
    highest were all allied pairs withholding, so "where the solved games point
    next" — and the pooled direction computed from it — described alliance
    friction while the page presented it as the region's escalation risk."""
    pack = _seed(conn)
    game_map = {
        "as_of": "2026-08-16",
        "scenarios_escalatory": [
            {"dyad_name": "Ally A–Ally B", "kind": "mutual_escalation",
             "kind_label": "mutual withholding", "likelihood": 0.95,
             "end_label": "well above", "family": {"family": "ally"},
             "market_implications": [{"market_id": "market:brent",
                                      "market_name": "Brent", "median": -0.05, "n": 60}]},
            {"dyad_name": "Rival A–Rival B", "kind": "mutual_escalation",
             "kind_label": "mutual escalation", "likelihood": 0.30,
             "end_label": "far above", "family": {"family": "adversary"},
             "market_implications": [{"market_id": "market:brent",
                                      "market_name": "Brent", "median": 0.03, "n": 90}]},
        ],
    }
    forward = markets.story(conn, pack, game_map=game_map, duration=None, flows=[],
                            coverage=None, as_of="2026-08-16")["forward"]
    assert [c["dyad_name"] for c in forward["courses"]] == ["Rival A–Rival B"]
    # The direction follows the courses it kept, not the one it dropped.
    assert forward["direction"][0]["expected_abnormal_return"] > 0
    # And the exclusion is REPORTED, never silent.
    assert forward["allied_courses_excluded"] == 1
    assert "allied pairs" in forward["note"]
