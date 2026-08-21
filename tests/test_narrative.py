"""The AI-narrative safety net: packets carry only measured numbers, and the
numeric-provenance validator refuses prose that states a figure the packet does
not contain. This is what makes "the AI never originates a number" a runtime
guarantee for the History/Work/Forecast surfaces, not just a prompt rule."""

from __future__ import annotations

from core.reasoning import narrative


def _markets_story() -> dict:
    return {
        "region": "mena",
        "region_label": "the Middle East",
        "as_of": "2026-06-30",
        "measured_through": "2026-06-22",
        "coverage": {"summary": {"events": 100, "events_measured": 40}},
        "markets": [
            {
                "name": "Brent crude",
                "ticker": "BZ=F",
                "headline": {"kind": "sharp_escalation", "median": -0.1236,
                             "p25": -0.2, "p75": 0.0, "n": 9},
                "biggest_moves": [
                    {"date": "2025-06-22", "name": "Op", "pair": "A->B",
                     "abnormal_return": -0.1319, "kind": "sharp_escalation"},
                ],
            },
        ],
        "forward": {"direction": [
            {"market_name": "Brent", "expected_abnormal_return": 0.02, "measurements": 5},
        ]},
        "backtest": {"summary": {"total_return": 0.909, "hit_rate": 0.6}},
    }


def test_markets_packet_is_compact_and_fingerprint_is_stable():
    pkt = narrative.markets_packet(_markets_story())
    assert pkt["region"] == "the Middle East"
    assert pkt["coverage"]["events_measured"] == 40
    assert pkt["markets"][0]["ticker"] == "BZ=F"
    # capped to the fields the prose may cite; raw explanation prose excluded
    assert "explanation" not in pkt
    fp = narrative.fingerprint_of(pkt)
    assert fp == narrative.fingerprint_of(narrative.markets_packet(_markets_story()))
    assert len(fp) == 16


def test_validator_accepts_numbers_drawn_from_the_packet():
    pkt = narrative.markets_packet(_markets_story())
    # -12.36% is the median as a percent; -13.19% the biggest move; 40 of 100
    # the coverage; +90.9% the paper book — all present in the packet.
    blocks = {
        "history": "Brent crude fell -12.36% on the record; the largest single "
                   "move was -13.19% on 2025-06-22.",
        "work": "The engine has measured 40 of 100 coded events.",
        "forecast": "A fund following the calls returned +90.9%.",
    }
    ok, offending = narrative.validate(blocks, pkt)
    assert ok, offending


def test_validator_rejects_an_originated_number():
    pkt = narrative.markets_packet(_markets_story())
    blocks = {"history": "Brent moved 77.7%, a figure the archive never measured.",
              "work": "", "forecast": ""}
    ok, offending = narrative.validate(blocks, pkt)
    assert not ok
    assert "77.7" in offending


def test_validator_allows_years_as_factual_dates():
    pkt = narrative.markets_packet(_markets_story())
    blocks = {"history": "The archive runs from 1972 to the present.",
              "work": "", "forecast": ""}
    ok, offending = narrative.validate(blocks, pkt)
    assert ok, offending


def test_game_region_packet_keeps_ranking_and_audit():
    region_map = {
        "region": "mena", "as_of": "2026-06-30", "dyads_solved": 12,
        "dyads_cinc": 12, "dyads_tilted": 12,
        "nash_gap": {"mean": 0.005, "max": 0.007},
        "kernel": {"share_measured": 1.0, "observations": 18324},
        "ranking": [
            {"dyad_id": "d1", "dyad_name": "A-B", "standing": "rivalry",
             "coercive_events": 1194, "hostility": 0.8,
             "sharp_departure_probability": 0.4,
             "top_scenario": {"kind": "probe_and_retreat"}},
        ],
        "scenarios_escalatory": [
            {"dyad_name": "A-B", "kind_label": "mutual escalation", "likelihood": 0.61,
             "market_implications": [{"market_name": "KOSPI", "median": -0.0739}]},
        ],
        "scenarios_calming": [],
    }
    pkt = narrative.game_region_packet(region_map)
    assert pkt["dyads_solved"] == 12
    assert pkt["ranking"][0]["coercive_events"] == 1194
    assert pkt["escalatory"][0]["kind"] == "mutual escalation"
    # a prose reading of these numbers validates
    blocks = {
        "history": "**A-B** carries the most coercion at 1194 events.",
        "work": "12 pairs solved; the LP nash gap averages 0.005 (worst 0.007), "
                "the kernel 100% measured over 18,324 dyad-quarters.",
        "forecast": "Mutual escalation leads at 61%, historically moving KOSPI -7.39%.",
    }
    ok, offending = narrative.validate(blocks, pkt)
    assert ok, offending


def test_compose_is_dark_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    try:
        narrative.compose("markets", _markets_story())
    except narrative.AgentUnavailable:
        return
    raise AssertionError("compose must be dark without OPENAI_API_KEY")
