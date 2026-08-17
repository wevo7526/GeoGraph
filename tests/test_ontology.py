"""The ontology is the source of truth — these tests are the spec's section 8
made executable. If one fails, the YAML and the build spec have diverged."""

from __future__ import annotations

from core.ingestion import gdelt
from core.ontology import kuzu_schema as ontology

SPEC_NODE_TABLES = {
    "Actor", "Issue", "Event", "Market", "Source", "AttributeEstimate",
    "Regime", "Forecast", "Analogue", "NetworkMetric", "Dyad",
}
SPEC_RELS = {
    "INITIATED_BY", "DIRECTED_AT", "RELATES_TO", "AFFECTED", "HAS_ESTIMATE",
    "OCCURRED_IN", "DERIVED_FROM", "FLOW", "OF_DYAD",
}


def test_spec_node_tables_all_present():
    assert set(ontology.nodes()) >= SPEC_NODE_TABLES


def test_spec_rel_tables_all_present():
    assert set(ontology.edges()) >= SPEC_RELS


def test_ddl_generates_nodes_before_rels():
    statements = ontology.ddl()
    kinds = ["NODE" if "NODE TABLE" in s else "REL" for s in statements]
    assert kinds == sorted(kinds, key=lambda k: k != "NODE"), "rel DDL before node DDL"


def test_every_sourced_edge_requires_source_id():
    for rel in ontology.sourced_edges():
        assert "source_id" in ontology.edges()[rel].required_props, (
            f"{rel} is sourced but does not require source_id — the provenance "
            "invariant has a hole"
        )


def test_the_factual_edges_are_sourced():
    # The edges that assert facts about the world. A new factual edge class
    # must appear here AND carry sourced: true.
    assert set(ontology.sourced_edges()) == {
        "INITIATED_BY", "DIRECTED_AT", "RELATES_TO", "AFFECTED", "FLOW",
    }


def test_classification_edges_are_not_traversable():
    traversable = set(ontology.traversable_edges())
    assert "OCCURRED_IN" not in traversable, (
        "every Event points at the same few Regime nodes — leaving OCCURRED_IN "
        "traversable makes any two events two hops apart"
    )
    assert "DERIVED_FROM" not in traversable
    assert {"RELATES_TO", "AFFECTED", "INITIATED_BY"} <= traversable


def test_affected_carries_the_fidelity_gradient():
    affected = ontology.edges()["AFFECTED"]
    assert {"window", "resolution", "method", "source_id"} <= set(affected.required_props)
    assert affected.key_slots == ("window",), (
        "window must be edge identity: one event, several windows, several edges"
    )


def test_flow_two_quarters_are_two_edges():
    assert ontology.edges()["FLOW"].key_slots == ("as_of",)


def test_relates_to_windows_are_identity():
    assert set(ontology.edges()["RELATES_TO"].key_slots) == {"relation_type", "valid_from"}


def test_event_carries_fidelity_tags():
    props = {p.name for p in ontology.nodes()["Event"].props}
    assert {"fidelity_tier", "temporal_resolution", "source_scale"} <= props


def test_market_requires_inception_date():
    market = ontology.nodes()["Market"]
    required = {p.name for p in market.props if p.required}
    assert {"ticker", "market_type", "trading_calendar", "inception_date"} <= required


def test_escalation_slots_are_derived():
    event = ontology.nodes()["Event"]
    derived = {p.name for p in event.props if p.derived}
    assert {"escalation_baseline", "escalation_direction", "escalation_magnitude"} <= derived


def test_embedding_is_a_vector_column():
    event = ontology.nodes()["Event"]
    embedding = next(p for p in event.props if p.name == "embedding")
    assert embedding.kuzu_type == "FLOAT[1024]"


def test_validate_edge_refuses_missing_source():
    import pytest

    with pytest.raises(ontology.OntologyError, match="provenance"):
        ontology.validate_edge("INITIATED_BY", {})


def test_validate_edge_refuses_unknown_rel():
    import pytest

    with pytest.raises(ontology.OntologyError):
        ontology.validate_edge("MADE_UP", {"source_id": "source:x"})


def test_fips_crosswalk_keys_survive_yaml():
    """Every FIPS key is a two-letter STRING — the Norway problem.

    YAML 1.1 reads a bare `NO` as the boolean false, so `NO: NOR` loaded as
    {False: "NOR"}: the lookup missed, and every event geolocated in Norway
    came out of the parser with an empty `action_geo` — which is what the
    co-participation rule reads to decide whose soil an action happened on.
    It is silent, it is one key in a hundred and twenty-two, and the same
    coercion waits for any future ON/OFF/Y/N key, so the shape is asserted
    rather than the single entry.
    """
    table = gdelt.fips_to_iso3()
    coerced = [k for k in table if not isinstance(k, str)]
    assert not coerced, f"YAML coerced these FIPS keys — quote them: {coerced}"
    assert table.get("NO") == "NOR"
    assert all(len(k) == 2 and k.isupper() for k in table), "FIPS keys are two upper-case letters"


def test_every_roster_actor_has_a_coordinate():
    """The globe places actors, and a missing key places nothing — silently.

    The failure this guards is not a crash: an actor absent from the crosswalk
    simply does not appear on the globe, which looks like a design decision
    rather than a gap. Coverage is asserted in both directions so a new roster
    member fails here rather than going quietly missing, and a stale entry for
    a pruned actor is caught too.

    Coordinates are CENTROIDS, not capitals, and they are a drawing instruction
    rather than evidence — nothing downstream may measure with them.
    """
    import yaml
    from pathlib import Path

    from core import packs

    path = Path("core/ontology/crosswalks/actor_coordinates.yaml")
    table = yaml.safe_load(path.read_text(encoding="utf-8"))["actor_coordinates"]

    coerced = [k for k in table if not isinstance(k, str)]
    assert not coerced, f"YAML coerced these keys — quote them: {coerced}"

    roster = {
        str(actor["iso3"]).upper()
        for name in packs.available()
        for actor in packs.load(name).actors
        if actor.get("iso3")
    }
    assert not (roster - set(table)), f"roster members with no coordinate: {sorted(roster - set(table))}"
    assert not (set(table) - roster), f"coordinates for non-roster actors: {sorted(set(table) - roster)}"

    for iso3, pair in table.items():
        lat, lng = pair
        assert -90 <= lat <= 90, f"{iso3} latitude out of range: {lat}"
        assert -180 <= lng <= 180, f"{iso3} longitude out of range: {lng}"
