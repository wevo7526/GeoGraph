"""Classifier Head B — escalation is relational, and these tests pin the
behaviours that make it so."""

from __future__ import annotations

import pytest

from core.classifier import escalation


def test_first_event_is_its_own_baseline():
    result = escalation.classify(-5.0, None)
    assert result["escalation_baseline"] == -5.0
    assert result["escalation_direction"] == "stable"
    assert result["escalation_magnitude"] == 0.0


def test_drop_below_baseline_escalates():
    result = escalation.classify(-8.0, -3.0)
    assert result["escalation_direction"] == "escalating"
    assert result["escalation_magnitude"] == 5.0


def test_rise_above_baseline_deescalates():
    result = escalation.classify(1.0, -6.0)
    assert result["escalation_direction"] == "deescalating"
    assert result["escalation_magnitude"] == 7.0


def test_noise_band_is_stable():
    assert escalation.classify(-3.3, -3.0)["escalation_direction"] == "stable"


def test_ewma_moves_toward_the_score():
    baseline = escalation.update_baseline(-2.0, -10.0, alpha=0.25)
    assert baseline == pytest.approx(-4.0)
    assert escalation.update_baseline(None, -7.0) == -7.0


def test_same_score_different_dyads_different_meaning():
    # The point of the design: -6 is business as usual for a rivalry and a
    # rupture for an alliance.
    tracker = escalation.DyadTracker()
    for _ in range(6):
        tracker.observe("dyad:us-iran", -6.0)
    for _ in range(6):
        tracker.observe("dyad:us-uk", 4.0)
    rivalry = tracker.observe("dyad:us-iran", -6.0)
    alliance = tracker.observe("dyad:us-uk", -6.0)
    assert rivalry["escalation_direction"] == "stable"
    assert alliance["escalation_direction"] == "escalating"
    assert alliance["escalation_magnitude"] > 9.0


def test_tracker_folds_events_in_order():
    tracker = escalation.DyadTracker(alpha=0.5)
    tracker.observe("d", -2.0)
    tracker.observe("d", -6.0)
    assert tracker.baseline("d") == pytest.approx(-4.0)


# ── the stream pass (code_events) ────────────────────────────────────────────


def _event(
    node_id: str, time: str, goldstein: float, a: str, b: str | None = None
) -> dict[str, object]:
    return {"node_id": node_id, "event_time": time, "goldstein": goldstein,
            "actor_a": a, "actor_b": b or a}


def test_a_dyad_is_the_pair_not_the_direction():
    # One rivalry, whichever side acted. Splitting Iran→Israel from
    # Israel→Iran would halve each baseline's history.
    assert escalation.dyad_id("actor:cow-630", "actor:cow-666") == escalation.dyad_id(
        "actor:cow-666", "actor:cow-630"
    )
    assert escalation.dyad_id("actor:cow-630", "actor:cow-666") == "dyad:cow-630--cow-666"


def test_the_dyad_prefix_matches_the_ontology():
    # escalation.py builds the id without importing the ontology; if the
    # schema's id_prefix ever changes, this is what catches the drift.
    from core.ontology import kuzu_schema

    assert escalation.dyad_id("actor:a", "actor:b").split(":")[0] == (
        kuzu_schema.id_prefixes()["Dyad"]
    )


def test_the_pass_folds_events_chronologically_regardless_of_input_order():
    events = [
        _event("event:c", "2020-01-03", -10.0, "actor:us", "actor:ir"),
        _event("event:a", "2015-07-14", 8.0, "actor:us", "actor:ir"),
        _event("event:b", "2018-05-08", -4.0, "actor:us", "actor:ir"),
    ]
    coded = {row["node_id"]: row for row in escalation.code_events(events).events}
    # The 2015 agreement is the dyad's first observation, so it sets its own
    # baseline; the 2018 rupture is measured against the cooperative memory.
    assert coded["event:a"]["escalation_direction"] == "stable"
    assert coded["event:b"]["escalation_baseline"] == 8.0
    assert coded["event:b"]["escalation_direction"] == "escalating"
    assert coded["event:c"]["escalation_direction"] == "escalating"


def test_the_pass_is_deterministic_on_same_day_events():
    # Two events on one date must fold in a fixed order or the baselines
    # differ run to run.
    same_day = [
        _event("event:z", "2024-04-13", -10.0, "actor:a", "actor:b"),
        _event("event:y", "2024-04-13", 2.0, "actor:a", "actor:b"),
    ]
    first = escalation.code_events(same_day)
    second = escalation.code_events(list(reversed(same_day)))
    assert first.events == second.events
    assert first.dyads == second.dyads


def test_an_internal_event_is_a_dyad_with_itself():
    coding = escalation.code_events([_event("event:rev", "1979-01-16", -7.5, "actor:cow-630")])
    assert coding.events[0]["dyad_id"] == "dyad:cow-630--cow-630"
    assert coding.dyads[0]["name"].endswith("(internal)")


def test_dyad_nodes_carry_the_baseline_as_of_their_last_event():
    coding = escalation.code_events(
        [
            _event("event:1", "2015-07-14", 8.0, "actor:us", "actor:ir"),
            _event("event:2", "2020-01-03", -10.0, "actor:us", "actor:ir"),
        ],
        names={"actor:us": "United States", "actor:ir": "Iran"},
    )
    dyad = coding.dyads[0]
    assert dyad["ewma_as_of"] == "2020-01-03"
    assert dyad["name"] == "Iran – United States"  # sorted by id, not by role
    assert dyad["ewma_baseline"] == pytest.approx(escalation.update_baseline(8.0, -10.0))


def test_an_unscored_event_cannot_be_coded():
    # Head B measures; it never invents the number it measures against.
    with pytest.raises(ValueError, match="goldstein"):
        escalation.code_events([
            {"node_id": "event:x", "event_time": "2020-01-01", "goldstein": None,
             "actor_a": "actor:a", "actor_b": "actor:b"},
        ])


def test_the_twelve_day_war_escalates_in_the_us_iran_dyad():
    # The Phase 0 reading, pinned: Israel–Iran was already at the floor from
    # the 2024 strike, so Rising Lion is not a departure from that dyad's
    # normal. The escalation is in US–Iran, which still carried JCPOA-era
    # cooperative memory — a real finding the case study narrates.
    from core import packs
    from core.classifier import typing as event_typing

    pack = packs.load("mena")
    stream = [
        _event(e["id"], e["date"], event_typing.goldstein_for(e["cameo"]),
               e["initiator"], e["target"])
        for e in pack.marquee_events
    ]
    coded = {row["node_id"]: row for row in escalation.code_events(stream).events}

    rising_lion = coded["event:mena-2025-rising-lion"]
    midnight_hammer = coded["event:mena-2025-midnight-hammer"]
    assert rising_lion["dyad_id"] == "dyad:cow-630--cow-666"
    assert rising_lion["escalation_magnitude"] == 0.0
    assert midnight_hammer["dyad_id"] == "dyad:cow-2--cow-630"
    assert midnight_hammer["escalation_direction"] == "escalating"
    assert midnight_hammer["escalation_magnitude"] > 10.0
