"""The CAMEO codebook: Goldstein weights and quad classes, both DERIVED from
the code. These tests pin the two properties the archive depends on — that
every code the spine cites resolves, and that a code's implied quad class and
its curated one cannot disagree."""

from __future__ import annotations

import pytest
import yaml

from core import packs
from core.classifier import typing as event_typing
from core.ontology.kuzu_schema import SCHEMA_PATH


def _enum_values(name: str) -> set[str]:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = yaml.safe_load(fh)
    return set(schema["enums"][name]["permissible_values"])


def test_every_root_code_is_scored_and_classed():
    # All twenty CAMEO roots, so any sub-code in the vocabulary can fall back.
    for root in range(1, 21):
        code = f"{root:02d}"
        assert -10.0 <= event_typing.goldstein_for(code) <= 10.0
        assert event_typing.quad_class_for(code) in _enum_values("QuadClass")


def test_quad_classes_match_the_ontology_enum():
    quads = set(event_typing._codebook()["quad_class_by_root"])
    assert quads == _enum_values("QuadClass")


def test_the_root_partition_is_complete_and_disjoint():
    roots = [
        root
        for group in event_typing._codebook()["quad_class_by_root"].values()
        for root in group
    ]
    assert sorted(roots) == [f"{n:02d}" for n in range(1, 21)]


def test_lookup_falls_back_along_the_hierarchy():
    # 1451 is in the codebook; 1452 is not, and resolves through 145 → 14.
    assert event_typing.goldstein_for("1451") == event_typing.goldstein_for("145")
    assert event_typing.goldstein_for("1452") == event_typing.goldstein_for("145")
    # A sub-code with no 3-digit entry either lands on its root.
    assert event_typing.goldstein_for("0499") == event_typing.goldstein_for("04")
    assert event_typing.quad_class_for("1452") == "material_conflict"


def test_leading_zeros_are_preserved():
    # "057" is signing a formal agreement; 57 as an integer is meaningless, and
    # str(57) would resolve as root "57" — which does not exist.
    assert event_typing.goldstein_for("057") == 8.0
    with pytest.raises(KeyError):
        event_typing.goldstein_for("57")


def test_conflict_is_negative_and_cooperation_positive():
    # The sign convention the whole escalation head rests on.
    assert event_typing.goldstein_for("190") < 0  # use of military force
    assert event_typing.goldstein_for("195") < 0  # aerial weapons
    assert event_typing.goldstein_for("186") < 0  # assassinate
    assert event_typing.goldstein_for("057") > 0  # sign formal agreement
    assert event_typing.goldstein_for("07") > 0  # provide aid


def test_war_codes_sit_at_the_floor():
    for code in ("19", "190", "193", "194", "195", "20", "202", "204"):
        assert event_typing.goldstein_for(code) == -10.0


def test_the_deep_tier_agrees_with_the_modern_tier_on_a_threat():
    # cow_to_cameo maps MID hostility 2 → CAMEO 138, and
    # escalation_scale_map maps that same hostility level to a Goldstein
    # equivalent. If the two disagree, a 1912 threat and a 2012 threat are not
    # comparable and every cross-era analogy is measuring two different things.
    from core.classifier import escalation

    entry = event_typing.map_deep_event("cow_mid_hostility", 2)
    assert entry is not None
    assert event_typing.goldstein_for(entry["cameo"]) == escalation.harmonize(
        "cow_hostility", 2
    )


def test_every_deep_tier_crosswalk_target_is_scored():
    codebook = event_typing._codebook()["codes"]
    crosswalk = event_typing._crosswalk()
    targets: set[str] = set()
    for table in crosswalk.values():
        if "cameo" in table:  # a flat entry like cow_war_onset
            if table["cameo"]:
                targets.add(str(table["cameo"]))
            continue
        for entry in table.values():
            if isinstance(entry, dict) and entry.get("cameo"):
                targets.add(str(entry["cameo"]))
    assert targets, "the deep-tier crosswalk names no CAMEO codes at all"
    for code in targets:
        assert event_typing.goldstein_for(code) <= 0.0, (
            f"deep-tier target {code} is a dispute or crisis and cannot score "
            "as cooperation"
        )
        assert str(code) in codebook, (
            f"CAMEO {code} is a deep-tier crosswalk target and should be listed "
            "explicitly in cameo_goldstein.yaml rather than resolved by fallback"
        )


def test_non_cameo_input_raises_rather_than_guessing():
    for bad in ("", "1", "12345", "abc", "19x"):
        with pytest.raises((ValueError, KeyError)):
            event_typing.goldstein_for(bad)


def test_the_spine_is_fully_scorable():
    # Every marquee event must resolve to a score and a class — this is the
    # gate on Phase 0's "the spine is coded".
    for event in packs.load("mena").marquee_events:
        code = event["cameo"]
        assert -10.0 <= event_typing.goldstein_for(code) <= 10.0
        assert event_typing.quad_class_for(code) in _enum_values("QuadClass")


def test_the_spine_quad_classes_follow_their_codes():
    for event in packs.load("mena").marquee_events:
        assert event["quad_class"] == event_typing.quad_class_for(event["cameo"]), (
            f"{event['id']} declares {event['quad_class']} but its CAMEO code "
            "implies otherwise"
        )


def test_a_pack_whose_quad_class_contradicts_its_code_is_refused():
    # The enforcement itself, exercised: 190 (use of military force) can never
    # be cooperation, and packs.load must say so rather than seed it.
    pack = packs.load("mena")
    bad = dict(pack.marquee_events[0], cameo="190", quad_class="material_cooperation")
    broken = packs.Pack(
        name=pack.name,
        path=pack.path,
        data={**pack.data, "marquee_events": {"events": [bad]}},
    )
    with pytest.raises(packs.PackError, match="quad class follows the code"):
        packs._validate(broken)


def test_the_twelve_day_war_is_scored_as_the_rupture_it_was():
    # The Phase 0 episode: both events must land at the conflict floor, since
    # the case study's escalation reading depends on them doing so.
    events = {e["id"]: e for e in packs.load("mena").marquee_events}
    for event_id in ("event:mena-2025-rising-lion", "event:mena-2025-midnight-hammer"):
        assert event_typing.goldstein_for(events[event_id]["cameo"]) == -10.0
        assert events[event_id]["quad_class"] == "material_conflict"
