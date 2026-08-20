"""Live GDELT 2.0 overlay on a frozen snapshot.

The snapshot is weights. A 15-minute file is scored against those weights
and never written as a graph edge. These tests pin that split without
hitting the network.
"""

from __future__ import annotations

from core.games import family as family_module
from core.models import panel as panel_module
from core.reasoning import markets as markets_module
from core.wire import live as live_overlay


def test_live_scoring_uses_the_snapshot_baseline_not_the_first_event_rule():
    """A −8 against a −2 EWMA is a 6-point departure, not 'this is the baseline'."""
    live_overlay.clear()
    rows = live_overlay.score(
        [{
            "dyad_id": "dyad:cow-2--cow-630",
            "node_id": "event:live-1",
            "event_time": "2026-08-17T12:00:00",
            "goldstein": -8.0,
            "quad_class": "verbal_cooperation",
        }],
        baselines={"dyad:cow-2--cow-630": -2.0},
    )
    assert rows[0]["escalation_direction"] == "escalating"
    assert rows[0]["escalation_magnitude"] == 6.0
    assert rows[0]["direction"] == "escalating"


def test_kind_of_accepts_head_b_deescalating():
    assert markets_module.kind_of("deescalating", 3.0) == "de-escalation"
    assert markets_module.kind_of("de-escalating", 3.0) == "de-escalation"
    assert markets_module.kind_of("escalating", 4.0) == "sharp_escalation"


def test_overlay_raises_current_quarter_intensity_without_mutating_the_snapshot():
    q = panel_module.quarter_index("2026-08-01")
    own = [{
        "dyad_id": "dyad:cow-2--cow-630",
        "dyad_name": "United States – Iran",
        "q": q,
        "date": panel_module.quarter_label(q),
        "intensity": 1.0,
        "signed_intensity": 1.0,
        "events": 10,
        "conflict": 1,
        "tone": 0.0,
    }]
    snapshot = list(own)
    overlaid = live_overlay.apply_to_own(own, [{
        "dyad_id": "dyad:cow-2--cow-630",
        "event_time": "2026-08-17T12:00:00",
        "direction": "escalating",
        "magnitude": 5.0,
        "goldstein": -8.0,
        "coercion": True,
    }])
    assert overlaid[0]["intensity"] == 5.0
    assert overlaid[0]["events"] == 11
    assert overlaid[0]["conflict"] == 2
    assert own[0]["intensity"] == 1.0
    assert snapshot[0]["intensity"] == 1.0


def test_live_joints_use_the_archive_proxy_and_do_not_touch_the_kernel():
    """Events are not decisions. The overlay names the same proxy the kernel
    was counted with; it does not recount the kernel."""
    space = family_module.ADVERSARY
    joints = live_overlay.joints([{
        "dyad_id": "dyad:cow-2--cow-630",
        "event_time": "2026-08-17T12:00:00",
        "initiator_id": "actor:cow-2",
        "quad_class": "material_conflict",
    }], space)
    assert joints
    key = next(iter(joints))
    assert key[0] == "dyad:cow-2--cow-630"
    action_a, action_b = joints[key]
    assert action_a in space.actions
    assert action_b in space.actions


def test_the_study_does_not_grow_the_wire_while_the_snapshot_is_frozen():
    import inspect

    from core.api import work

    src = inspect.getsource(work._pack_study_events)
    assert "snapshot.frozen" in src
    assert "add_corpus_wire" in src
    study = inspect.getsource(work.study)
    assert "snapshot.frozen" in study
    assert "live intake is GDELT 2.0" in study


def test_the_live_module_never_imports_a_graph_writer():
    import inspect

    source = inspect.getsource(live_overlay)
    assert "merge_edges" not in source
    assert "merge_nodes" not in source
    assert "write_edges" not in source
    assert "write_effects" not in source
    assert "record_runs" not in source


def test_attach_measured_stamps_the_overlay_without_writing(monkeypatch):
    """This-event CARs live on the overlay. The frozen map does not move."""
    from core.panel import pg_store
    from core.transmission import event_study
    from core.transmission.event_study import EffectResult

    live_overlay.clear()

    class _Pack:
        name = "mena"
        markets = [{
            "ticker": "^GSPC", "id": "market:spx", "name": "S&P 500",
            "inception_date": "1950-01-01",
            "native_frequency": '{"1972": "day"}',
        }]

    row = {
        "node_id": "event:gdelt-live-1",
        "event_time": "2026-08-17T12:00:00",
        "escalation_magnitude": 6.0,
    }
    live_overlay._PACK["mena"] = {"rows": [row]}

    class _Panel:
        def close(self) -> None:
            return None

    monkeypatch.setattr(pg_store, "connect", lambda settings: _Panel())
    monkeypatch.setattr(pg_store, "series", lambda *a, **k: [])
    monkeypatch.setattr(pg_store, "series_intraday", lambda *a, **k: [])

    def _compute(event, markets, **kwargs):
        del markets, kwargs
        return ([EffectResult(
            event_node_id=event["node_id"], market_ticker="^GSPC",
            window="car_0_1", resolution="day",
            raw_return=0.01, expected_return=0.0, abnormal_return=0.01,
            t_stat=1.0, p_value=0.3, first_mover=True, overlapping=False,
            method="test",
        )], [])

    monkeypatch.setattr(event_study, "compute_effects", _compute)
    stamped = live_overlay.attach_measured(_Pack())
    assert stamped == 1
    assert row["measured"][0]["abnormal_return"] == 0.01
    assert row["measured"][0]["ticker"] == "^GSPC"
    assert live_overlay.row_by_id("event:gdelt-live-1") is row


def test_refresh_pack_keeps_this_event_measurements(monkeypatch):
    from core.ingestion import stream

    live_overlay.clear()

    class _Pack:
        name = "mena"
        actors = []

    live_overlay._PACK["mena"] = {"rows": [{
        "node_id": "event:gdelt-1",
        "measured": [{"ticker": "^GSPC", "window": "car_0_1", "abnormal_return": 0.01}],
    }]}
    monkeypatch.setattr(stream, "poll", lambda pack, roster: {
        "published": "20260817181500",
        "rows": [{
            "node_id": "event:gdelt-1",
            "event_time": "2026-08-17T12:00:00",
            "goldstein": -8.0,
            "dyad_id": "dyad:cow-2--cow-630",
            "quad_class": "material_conflict",
        }],
    })
    monkeypatch.setattr(live_overlay, "snapshot_baselines", lambda pack: {})
    out = live_overlay.refresh_pack(_Pack())
    assert out["rows"][0]["measured"][0]["abnormal_return"] == 0.01


def test_the_live_feed_passes_action_geo_for_display_not_as_a_retarget():
    """Live rows already carry action_geo. The endpoint must pass it through
    so the surface can headline a third-country fight as location, without
    rewriting the stored pair."""
    import inspect

    from core.api.routers import events as events_router

    source = inspect.getsource(events_router.wire_live)
    assert "display_fields" in source
    assert "action_geo" in source
    assert "geo_names" in source


def test_the_live_feed_does_not_attach_a_strategy_contract():
    """The live page shows historical cells as analogy. Stamping a trade
    action onto those cells is how the blotter leaked back in."""
    import inspect

    from core.api.routers import events as events_router

    source = inspect.getsource(events_router.wire_live)
    assert "strategy_contract" not in source
    assert "assess_cell" not in source
    assert "Head B" not in source
    assert "measured" in source
    assert "transmission map" in source
    assert "ensure_pack" in source


def test_case_and_impact_read_the_live_overlay_after_graph_and_corpus_miss():
    import inspect

    from core.api.routers import case_studies
    from core.api.routers import events as events_router
    from core.reasoning import impact as impact_module

    assert "row_by_id" in inspect.getsource(case_studies._episode)
    assert "row_by_id" in inspect.getsource(impact_module._event_context)
    assert "_live_event_detail" in inspect.getsource(events_router.get_event)
    assert "row_by_id" in inspect.getsource(events_router._live_event_detail)
    assert "_effects_from_live" in inspect.getsource(events_router._effects_from_panel)
