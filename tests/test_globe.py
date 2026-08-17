"""The globe's data — three layers that answer three different questions.

The front door renders from this, so the tests are mostly about what it must
never do: go blank, place an actor it cannot place, or animate an event the
platform's own definitions would refuse.
"""

from __future__ import annotations

from core.api.routers import globe


def test_it_needs_no_graph():
    """DELIBERATELY GRAPH-FREE, and this is the assertion that keeps it so.

    The study runs as a child process, a child holds Kuzu's single write lock,
    and every graph endpoint answers 503 for its slice — roughly one sample in
    twelve. A hero that goes blank for eight percent of visits is a broken
    hero. Nothing here takes a connection, so nothing here can be locked out.
    """
    out = globe.globe(region=None, pulses=4)
    assert out["nodes"], "the globe must place the roster with no graph open"


def test_every_placed_node_carries_real_coordinates():
    out = globe.globe(region=None, pulses=0)
    for node in out["nodes"]:
        assert -90 <= node["lat"] <= 90, node
        assert -180 <= node["lng"] <= 180, node
        assert node["iso3"] and node["name"] and node["id"]


def test_links_only_join_placed_nodes():
    """An arc to an actor that was never placed draws to the origin — a line
    into the middle of the earth, which reads as a bug and is one."""
    out = globe.globe(region=None, pulses=0)
    placed = {n["id"] for n in out["nodes"]}
    for link in out["links"]:
        assert link["source"] in placed, link
        assert link["target"] in placed, link


def test_a_region_is_a_subset_of_everything():
    everything = globe.globe(region=None, pulses=0)
    one = globe.globe(region="mena", pulses=0)
    assert {n["id"] for n in one["nodes"]} <= {n["id"] for n in everything["nodes"]}
    assert len(one["nodes"]) < len(everything["nodes"])


def test_an_unknown_region_is_a_404_not_an_empty_globe():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        globe.globe(region="atlantis", pulses=0)
    assert caught.value.status_code == 404


def test_a_conflict_pulse_must_clear_the_coercion_test():
    """The homograph defect is open, and the front door is the worst place to
    show it.

    GDELT resolves an actor to a country by name, so a domestic crime story
    becomes an interstate event: the first draft of this endpoint lit the globe
    with "Assault: United States → Japan" — two allies, a glowing arc across
    the Pacific. `classifier.coercion` is the one definition every counter on
    this platform reads, and a material-conflict pulse now has to clear it.
    Cooperative departures are exempt: they make no claim about coercion.
    """
    out = globe.globe(region=None, pulses=30)
    for pulse in out["pulses"]:
        assert pulse["points_from_baseline"] >= out["departure_points"]
        assert pulse["direction"] in {"escalating", "deescalating", "stable"}


def test_a_pulse_is_a_departure_not_a_raw_score():
    """The bar is distance from the pair's OWN baseline. Animating raw scores
    would light the busiest pairs rather than the ones doing something
    unusual — and the same score is an ordinary week for a rivalry and a
    rupture for an alliance."""
    out = globe.globe(region=None, pulses=20)
    assert out["departure_points"] == globe.PULSE_DEPARTURE_POINTS
    assert "own running baseline" in out["method"]


def test_pulses_can_be_switched_off_entirely():
    """The structural layers must render without the live one — the corpus is
    warmed lazily, and a cold process should still draw a globe."""
    out = globe.globe(region=None, pulses=0)
    assert out["pulses"] == []
    assert out["nodes"] and out["links"]
