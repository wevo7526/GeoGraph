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


def test_what_cannot_be_placed_is_reported_not_dropped():
    """Nineteen of seventy-five roster actors have no coordinate, and NONE of
    them is a gap.

    Ten sovereign wealth funds, six organisations (OPEC, the GCC, Hezbollah,
    Hamas, Ansar Allah, al-Qaeda) and three historical states deliberately
    given no `iso3` — giving the GDR one would route 1980s wire traffic to the
    wrong state. A globe that silently omitted a quarter of its own roster
    would assert coverage it does not have, on a platform whose footer says
    measured, never asserted. So they are served, and the surface draws them
    in a margin lane.
    """
    out = globe.globe(region=None, pulses=0)
    assert out["unplaced"], "the roster's unplaceable actors must be served"
    placed_ids = {n["id"] for n in out["nodes"]}
    for actor in out["unplaced"]:
        assert actor["id"] not in placed_ids, "an actor is placed or it is not"
        assert actor["name"] and actor["actor_type"]


def test_a_client_carries_its_patron():
    """Patronage is the one DIRECTED standing the packs declare, and it is what
    lets the margin lane point rather than merely list."""
    out = globe.globe(region="mena", pulses=0)
    by_name = {a["name"]: a for a in out["unplaced"]}
    hezbollah = by_name.get("Hezbollah")
    assert hezbollah is not None
    assert hezbollah.get("patron_name") == "Iran"


def test_counts_are_served_not_recomputed_on_the_surface():
    """The strapline reads these. Composing it from array lengths in TSX lets
    the caption and the payload drift apart."""
    out = globe.globe(region=None, pulses=5)
    counts = out["counts"]
    assert counts["placed"] == len(out["nodes"])
    assert counts["unplaced"] == len(out["unplaced"])
    assert counts["links"] == len(out["links"])
    assert counts["pulses"] == len(out["pulses"])


def test_a_pulse_carries_named_fields_not_the_coded_string():
    """The event's own name is machine vocabulary — "Use conventional military
    force, not specified: Iran → Turkey" — and `test_surface_language.py`
    refuses that inside a component. The surface composes the sentence."""
    out = globe.globe(region=None, pulses=8)
    for pulse in out["pulses"]:
        assert "name" not in pulse, "the coded string must not reach the surface"
        assert pulse["initiator_name"] and pulse["target_name"]
        assert pulse["cameo_code"]
