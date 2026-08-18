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


def test_the_live_module_never_imports_a_graph_writer():
    import inspect

    source = inspect.getsource(live_overlay)
    assert "merge_edges" not in source
    assert "merge_nodes" not in source
    assert "write_edges" not in source
    assert "write_effects" not in source
