"""Event → market prices: the contract and the honesty rules.

Pins that the measured actual round-trips, the expected base rate aggregates
ONLY regime-comparable precedents, an absent base rate is null (not zero), and
every expected number is backed by real precedent rows (the provenance
invariant, at the read side).
"""

from __future__ import annotations

from typing import Any

import pytest

from core.classifier import escalation
from core.graph import kuzu_store
from core.reasoning import impact


def _event(node_id: str, date: str, magnitude: float) -> dict[str, Any]:
    return {
        "node_id": node_id, "name": node_id, "event_time": date,
        "action_cameo_code": "190", "goldstein": -9.0,
        "quad_class": "material_conflict", "region_pack": "mena",
        "fidelity_tier": "modern_coded", "temporal_resolution": "day",
        "source_scale": "goldstein", "escalation_direction": "escalating",
        "escalation_magnitude": magnitude, "escalation_baseline": -6.0,
    }


@pytest.fixture()
def conn(tmp_path):
    connection = kuzu_store.connect(tmp_path / "impact.kuzu")
    kuzu_store.apply_schema(connection)
    kuzu_store.merge_nodes(connection, "Source", [
        {"node_id": "source:test", "name": "Test", "kind": "dataset", "url": "", "citation": ""},
    ])
    kuzu_store.merge_nodes(connection, "Actor", [
        {"node_id": "actor:a", "name": "Alpha", "actor_type": "state"},
        {"node_id": "actor:b", "name": "Beta", "actor_type": "state"},
        {"node_id": "actor:c", "name": "Gamma", "actor_type": "state"},
    ])
    kuzu_store.merge_nodes(connection, "Market", [{
        "node_id": "market:x", "name": "Market X", "ticker": "X",
        "market_type": "equity_index", "trading_calendar": "us", "calendar_eras": "",
        "inception_date": "1900-01-01", "native_frequency": "", "region_pack": "mena",
    }])
    # target (2015) + one comparable precedent (2016, same fiat regime) + one
    # NON-comparable precedent (1950, Bretton Woods) that must be excluded.
    kuzu_store.merge_nodes(connection, "Event", [
        _event("event:target", "2015-06-01", 4.0),
        _event("event:prec1", "2016-06-01", 3.0),
        _event("event:prec2", "1950-06-01", 3.0),
    ])
    edges_by_event = {
        "event:target": ("actor:a", "actor:b"),
        "event:prec1": ("actor:a", "actor:b"),
        "event:prec2": ("actor:a", "actor:b"),
    }
    kuzu_store.merge_edges(connection, "INITIATED_BY", [
        {"src": e, "dst": ini, "source_id": "source:test"}
        for e, (ini, _tgt) in edges_by_event.items()
    ])
    kuzu_store.merge_edges(connection, "DIRECTED_AT", [
        {"src": e, "dst": tgt, "source_id": "source:test"}
        for e, (_ini, tgt) in edges_by_event.items()
    ])
    kuzu_store.merge_edges(connection, "AFFECTED", [
        {"src": "event:target", "dst": "market:x", "window": "car_0_1", "resolution": "day",
         "abnormal_return": 0.05, "first_mover": True,
         "method": "test", "source_id": "source:test"},
        {"src": "event:prec1", "dst": "market:x", "window": "car_0_1", "resolution": "day",
         "abnormal_return": 0.02, "first_mover": False,
         "method": "test", "source_id": "source:test"},
        {"src": "event:prec2", "dst": "market:x", "window": "car_0_1", "resolution": "day",
         "abnormal_return": 0.99, "first_mover": False,
         "method": "test", "source_id": "source:test"},
    ])
    yield connection
    kuzu_store.close(connection)


def test_measured_actual_round_trips(conn):
    result = impact.event_impact(conn, "event:target")
    assert result is not None
    assert result["mode"] == "historical"
    assert result["event"]["dyad"] == escalation.dyad_id("actor:a", "actor:b")
    assert result["event"]["date"] == "2015-06-01"
    x = next(m for m in result["markets"] if m["market_id"] == "market:x")
    assert x["measured"]["car"] == pytest.approx(0.05)
    assert x["measured"]["first_mover"] is True


def test_expected_uses_only_regime_comparable_precedents(conn):
    result = impact.event_impact(conn, "event:target")
    assert result is not None
    x = next(m for m in result["markets"] if m["market_id"] == "market:x")
    # prec1 (2016) counts; prec2 (1950, Bretton Woods) and the event itself do not.
    assert x["expected"]["n_precedents"] == 1
    assert x["expected"]["mean_car"] == pytest.approx(0.02)
    # surprise = measured - expected.
    assert x["surprise"] == pytest.approx(0.03)


def test_a_missing_event_is_none(conn):
    assert impact.event_impact(conn, "event:does-not-exist") is None


def test_no_admissible_precedent_is_empty_not_zero(conn):
    # Dyad a<>c has no events at all — the honest answer is an empty market
    # list, never a fabricated or zero price.
    dyad_ac = escalation.dyad_id("actor:a", "actor:c")
    result = impact.hypothetical_impact(conn, dyad_id=dyad_ac, as_of="2015-06-01")
    assert result["mode"] == "hypothetical"
    assert result["markets"] == []
    assert result["precedents"]["n"] == 0


def test_every_expected_number_is_backed_by_precedents(conn):
    # The provenance invariant at the read side: no expected figure exists
    # without a positive precedent count standing behind it.
    result = impact.event_impact(conn, "event:target")
    assert result is not None
    for market in result["markets"]:
        if market["expected"] is not None:
            assert market["expected"]["n_precedents"] > 0


def test_dyad_timeline_groups_by_event_most_recent_first(conn):
    dyad = escalation.dyad_id("actor:a", "actor:b")
    timeline = impact.dyad_timeline(conn, dyad)
    assert timeline["total"] == 3
    dates = [e["date"] for e in timeline["events"]]
    assert dates == ["2016-06-01", "2015-06-01", "1950-06-01"]
    # Each event carries its measured market moves, grouped under it.
    first = timeline["events"][0]
    assert first["markets"] and first["markets"][0]["market_id"] == "market:x"
    assert "car" in first["markets"][0]


def test_overlapping_precedents_are_dropped_when_asked(conn):
    kuzu_store.merge_nodes(conn, "Event", [_event("event:overlap", "2014-06-01", 3.0)])
    kuzu_store.merge_edges(conn, "INITIATED_BY", [
        {"src": "event:overlap", "dst": "actor:a", "source_id": "source:test"},
    ])
    kuzu_store.merge_edges(conn, "DIRECTED_AT", [
        {"src": "event:overlap", "dst": "actor:b", "source_id": "source:test"},
    ])
    kuzu_store.merge_edges(conn, "AFFECTED", [{
        "src": "event:overlap", "dst": "market:x", "window": "car_0_1",
        "resolution": "day", "abnormal_return": 0.99, "first_mover": False,
        "overlapping": True, "method": "test", "source_id": "source:test",
    }])
    dyad = escalation.dyad_id("actor:a", "actor:b")
    _, n_all = impact._expected_for_dyad(
        conn, dyad, "2015-06-01", exclude_event_id="event:target",
    )
    _, n_clean = impact._expected_for_dyad(
        conn, dyad, "2015-06-01", exclude_event_id="event:target",
        exclude_overlapping=True,
    )
    assert n_all == n_clean + 1


def test_event_impact_always_excludes_the_event_itself(conn):
    result = impact.event_impact(conn, "event:target")
    assert result is not None
    # The event's own 0.05 must not be in its expected mean (0.02 from prec1).
    x = next(m for m in result["markets"] if m["market_id"] == "market:x")
    assert x["expected"]["mean_car"] == pytest.approx(0.02)


def test_hypothetical_shares_the_object_shape(conn):
    dyad = escalation.dyad_id("actor:a", "actor:b")
    hist = impact.event_impact(conn, "event:target")
    assert hist is not None
    hypo = impact.hypothetical_impact(conn, dyad_id=dyad, as_of="2015-06-01")
    assert set(hist.keys()) == set(hypo.keys())
    assert hist["boundary_statement"] == hypo["boundary_statement"]


def test_effects_for_dyad_fills_from_the_panel(monkeypatch):
    """A wire pair with no Kuzu copy still has a measured case from Postgres."""
    from core.transmission import effects as effects_module

    effects_module.forget_dyad_effects()
    monkeypatch.setattr(effects_module.kuzu_store, "query", lambda *a, **k: [])
    monkeypatch.setattr(
        "core.wire.serving.events_for_dyad",
        lambda dyad_id, limit=400: [{
            "node_id": "event:gdelt-1",
            "event_time": "2024-01-02",
            "name": "wire",
            "goldstein": -8.0,
            "escalation_direction": "escalating",
            "escalation_magnitude": 4.0,
            "fidelity_tier": "wire",
            "region_pack": "mena",
            "initiator_id": "actor:a",
            "target_id": "actor:b",
            "dyad_id": dyad_id,
        }],
    )

    class _Panel:
        def close(self) -> None:
            return None

    monkeypatch.setattr("core.panel.pg_store.connect", lambda settings: _Panel())
    monkeypatch.setattr(
        "core.panel.pg_store.computed_runs",
        lambda panel, event_ids=None, **k: [
            {
                "event_node_id": "event:gdelt-1",
                "market_ticker": "BZ=F",
                "window": window,
                "abnormal_return": -0.02,
                "raw_return": -0.02,
                "first_mover": True,
                "status": "computed",
                "method": "test",
                "p_value": 0.01,
                "t_stat": -2.0,
                "resolution": "day",
            }
            for window in ("car_0_1", "car_0_3")
        ] if event_ids else [],
    )
    rows = effects_module.effects_for_dyad(object(), escalation.dyad_id("actor:a", "actor:b"))
    assert {r["window"] for r in rows} == {"car_0_1", "car_0_3"}
    assert all(r["event_id"] == "event:gdelt-1" for r in rows)
    assert all(r["abnormal_return"] == -0.02 for r in rows)
