"""Leave-one-out skill of event → market cells: the honesty rules.

The scorer must not see the future, must not mix Bretton Woods into a fiat
question, must not count a GDELT happening twice, and must not turn a thin
cell into a number. Those are the defects that would make a published MAE
a look-ahead.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.reasoning import impact_backtest
from core.reasoning import transmission_skill as skill


def _obs(i: int, **kwargs: Any) -> dict[str, Any]:
    year = 2015 + (i // 12)
    month = 1 + (i % 12)
    row = {
        "event_id": f"event:e{i}",
        "date": f"{year}-{month:02d}-15",
        "ticker": "BZ=F",
        "market_id": "market:brent",
        "market_type": "commodity",
        "pack": "mena",
        "ar": 0.02,
        "window": "car_0_3",
        "overlapping": False,
        "p_value": 0.01,
        "kind": "sharp_escalation",
        "direction": "escalating",
        "magnitude": 4.0,
        "quad_class": "material_conflict",
        "dyad_id": "dyad:a--b",
    }
    row.update(kwargs)
    return row


def test_a_future_event_cannot_enter_expected():
    rows = [_obs(i, ar=0.01) for i in range(12)]
    rows.append(_obs(12, ar=0.90, date="2016-06-15"))
    report = skill.walk(rows, matchers=("kind",), clean=True)
    kind = report["matchers"]["kind"]
    assert kind["n"] >= 1
    # The last event is the only one with a huge move; priors of earlier
    # scored events must not include 0.90, so MAE stays near the 0.01 cluster.
    assert kind["mae"] < 0.2


def test_bretton_woods_cannot_enter_a_fiat_question():
    old = [
        _obs(i, date=f"1960-{(i % 12) + 1:02d}-15", ar=0.99, event_id=f"event:old{i}")
        for i in range(12)
    ]
    now = [
        _obs(i, date=f"2016-{(i % 12) + 1:02d}-15", ar=0.02, event_id=f"event:new{i}")
        for i in range(12)
    ]
    report = skill.walk(old + now, matchers=("kind",), clean=True)
    # Fiat events can only see other fiat events, whose AR is 0.02.
    kind = report["matchers"]["kind"]
    assert kind["n"] >= 1
    assert kind["mae"] is not None and kind["mae"] < 0.05


def test_overlapping_rows_are_priors_only_when_dirty():
    clean = [_obs(i, ar=0.02) for i in range(12)]
    dirty = [
        _obs(i + 20, ar=0.50, overlapping=True, date=f"2016-{(i % 12) + 1:02d}-15")
        for i in range(12)
    ]
    held_out = [_obs(40, ar=0.02, date="2017-01-15")]
    rows = clean + dirty + held_out
    closed = skill.walk(rows, matchers=("kind",), clean=True)
    open_ = skill.walk(rows, matchers=("kind",), clean=False, p_gate=None)
    assert closed["matchers"]["kind"]["mae"] < open_["matchers"]["kind"]["mae"]


def test_a_thin_cell_is_none_not_zero():
    rows = [_obs(i) for i in range(5)]
    report = skill.walk(rows, matchers=("kind",), clean=True)
    assert report["matchers"]["kind"]["n"] == 0
    assert report["matchers"]["kind"]["coverage"] == 0.0


def test_gdelt_same_day_same_ar_is_one_happening():
    rows = [_obs(i, ar=0.02) for i in range(10)]
    rows.append(_obs(
        10, event_id="event:gdelt-aaaa", date="2016-03-15", ar=0.03,
    ))
    rows.append(_obs(
        11, event_id="event:gdelt-bbbb", date="2016-03-15", ar=0.03,
    ))
    report = skill.walk(rows, matchers=("kind",), clean=True)
    assert report["gdelt_dupes_skipped"] == 1
    assert report["universe"] == 11


def test_kind_matched_cells_beat_naive_when_the_signal_is_real():
    up = [_obs(i, ar=0.04, kind="sharp_escalation") for i in range(16)]
    down = [
        _obs(
            i + 50, ar=-0.04, kind="de-escalation",
            direction="de-escalating", magnitude=2.0,
            quad_class="verbal_cooperation", date=f"2016-{(i % 12) + 1:02d}-01",
        )
        for i in range(16)
    ]
    report = skill.walk(up + down, matchers=("kind",), clean=True)
    kind = report["matchers"]["kind"]
    assert kind["n"] >= 8
    assert kind["sign_hit"] is not None and kind["sign_hit"] >= 0.8
    assert kind["beats_naive"] is True


def test_oracle_class_is_quad_band_and_game_class_can_be_worse():
    conflict = [
        _obs(i, ar=0.03, quad_class="material_conflict") for i in range(16)
    ]
    coop = [
        _obs(
            i + 50, ar=-0.03, quad_class="verbal_cooperation",
            kind="de-escalation", direction="de-escalating",
            date=f"2016-{(i % 12) + 1:02d}-01",
        )
        for i in range(16)
    ]
    steps = {
        str(row["event_id"]): {"quad": "verbal_cooperation", "intensity_band": 0}
        for row in conflict
    }
    report = skill.walk(
        conflict + coop,
        matchers=("quad_band", "game_band"),
        clean=True,
        game_first_steps=steps,
    )
    oracle = report["matchers"]["quad_band"]
    game = report["matchers"]["game_band"]
    assert oracle["n"] >= 1
    if game["n"] and oracle["mae"] is not None and game["mae"] is not None:
        assert game["mae"] >= oracle["mae"]


def test_compact_skill_breaks_out_market_type():
    rows = (
        [_obs(i, ar=0.02, market_type="commodity") for i in range(16)]
        + [_obs(
            i + 30, ticker="DGS10", market_id="market:dgs10",
            market_type="sovereign_yield", ar=-0.01,
            date=f"2016-{(i % 12) + 1:02d}-15",
        ) for i in range(16)]
        + [_obs(
            i + 60, ticker="^GSPC", market_id="market:gspc",
            market_type="equity_index", ar=0.005,
            date=f"2016-{(i % 12) + 1:02d}-20",
        ) for i in range(16)]
    )
    compact = skill.compact_skill(skill.walk(rows, matchers=("kind",), clean=True))
    assert "commodity" in compact["by_market_type"]
    assert "sovereign_yield" in compact["by_market_type"]
    assert compact["sovereign_yield"] is not None
    assert compact["gspc_control"] is not None
    assert compact["trust"]["trusted"] is True or compact["trust"]["bottleneck"] == "transmission"
    assert "quad_band" in compact


def test_trust_of_marks_sequencing_when_oracle_beats_and_game_does_not():
    sequencing = skill.trust_of({
        "matchers": {
            "quad_band": {"beats_naive": True},
            "game_band": {"beats_naive": False},
        }
    })
    assert sequencing["trusted"] is True
    assert sequencing["bottleneck"] == "sequencing"
    assert "sequencing" in sequencing["note"]

    trans = skill.trust_of({
        "matchers": {"quad_band": {"beats_naive": False}}
    })
    assert trans["trusted"] is False
    assert trans["bottleneck"] == "transmission"

    ok = skill.trust_of({
        "matchers": {
            "quad_band": {"beats_naive": True},
            "game_band": {"beats_naive": True},
        }
    })
    assert ok["trusted"] is True
    assert ok["bottleneck"] is None


def test_duration_ordering_is_rank_not_a_fit():
    implied = [
        {"dyad_id": "dyad:a", "implied_persistence": 0.8},
        {"dyad_id": "dyad:b", "implied_persistence": 0.2},
        {"dyad_id": "dyad:c", "implied_persistence": 0.5},
    ]
    simulated = [
        {"dyad_id": "dyad:a", "simulated_persistence": 6.0},
        {"dyad_id": "dyad:b", "simulated_persistence": 1.0},
        {"dyad_id": "dyad:c", "simulated_persistence": 3.0},
    ]
    out = skill.duration_ordering(implied, simulated)
    assert out["n"] == 3
    assert out["spearman"] == pytest.approx(1.0)
    assert "not established" in out["note"]


def test_belly_comparison_does_not_change_the_production_statistic():
    events = [
        {"dyad_id": "dyad:a", "front": 0.001, "belly": 0.002, "long": 0.010},
        {"dyad_id": "dyad:b", "front": 0.010, "belly": 0.008, "long": 0.001},
    ]
    simulated = [
        {"dyad_id": "dyad:a", "simulated_persistence": 5.0},
        {"dyad_id": "dyad:b", "simulated_persistence": 1.0},
    ]
    out = skill.belly_adds_ordering(events, simulated)
    assert "front_vs_long" in out
    assert "production statistic stays front vs long" in out["note"]


def test_remeasure_is_refused_when_hygiene_still_has_work():
    rows = [_obs(i, ar=0.0) for i in range(4)]
    report = skill.walk(rows, matchers=("kind",), clean=True)
    gate = skill.remeasure_justified(report)
    assert gate["justified"] is False


def test_impact_gate_marks_this_event_not_the_paper_book():
    rows = [_obs(i, ar=0.05, market_type="commodity") for i in range(20)]
    out = impact_backtest.walk(rows, spine_only=True)
    assert out["trades"] >= 1
    assert out["hit_rate"] == 1.0
    assert "paper book is untouched" in out["note"]
    assert "commodity" in out["by_market_type"]


def test_gdelt_events_are_skipped_when_spine_only():
    rows = [
        _obs(i, event_id=f"event:gdelt-{i}", ar=0.05) for i in range(20)
    ]
    out = impact_backtest.walk(rows, spine_only=True)
    assert out["events"] == 0
    assert out["trades"] == 0


def test_the_score_script_writes_nothing():
    import inspect
    from pathlib import Path

    text = (
        Path(__file__).resolve().parents[1] / "scripts" / "score_transmission.py"
    ).read_text(encoding="utf-8")
    assert "INSERT" not in text
    assert "record_market_story" not in text
    assert "Read-only" in text
    src = inspect.getsource(skill.observations_from_panel)
    assert "ticker=ticker" in src
    assert "computed_runs(panel)" not in src.replace(
        "computed_runs(panel, ticker", ""
    )
