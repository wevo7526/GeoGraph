"""The crosswalks are approximations — these tests keep them COHERENT
approximations: complete over their source scales, inside the Goldstein
range, and monotone where the source scale is ordered."""

from __future__ import annotations

import yaml

from core.classifier import escalation
from core.classifier import typing as event_typing
from core.ontology.kuzu_schema import SCHEMA_PATH


def _enum_values(name: str) -> set[str]:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = yaml.safe_load(fh)
    return set(schema["enums"][name]["permissible_values"])


def test_cow_hostility_fully_mapped_and_in_range():
    for level in range(1, 6):
        value = escalation.harmonize("cow_hostility", level)
        assert -10.0 <= value <= 10.0


def test_icb_severity_fully_mapped_and_in_range():
    for level in range(1, 5):
        value = escalation.harmonize("icb_severity", level)
        assert -10.0 <= value <= 10.0


def test_escalation_maps_are_monotone():
    # More hostile category → more negative Goldstein-equivalent, weakly.
    cow = [escalation.harmonize("cow_hostility", level) for level in range(1, 6)]
    icb = [escalation.harmonize("icb_severity", level) for level in range(1, 5)]
    assert cow == sorted(cow, reverse=True)
    assert icb == sorted(icb, reverse=True)


def test_war_maps_to_the_floor():
    assert escalation.harmonize("cow_hostility", 5) == -10.0
    assert escalation.harmonize("icb_severity", 4) == -10.0


def test_goldstein_passes_through():
    assert escalation.harmonize("goldstein", "-6.5") == -6.5


def test_unmapped_values_raise_rather_than_guess():
    import pytest

    with pytest.raises(KeyError):
        escalation.harmonize("cow_hostility", 9)
    with pytest.raises(KeyError):
        escalation.harmonize("made_up_scale", 1)


def test_cow_to_cameo_hostility_one_emits_no_event():
    assert event_typing.map_deep_event("cow_mid_hostility", 1) is None


def test_cow_to_cameo_codes_are_cameo_shaped():
    for level in range(2, 6):
        entry = event_typing.map_deep_event("cow_mid_hostility", level)
        assert entry is not None
        assert entry["cameo"].isdigit() and 2 <= len(entry["cameo"]) <= 4


def test_source_scales_match_the_ontology_enum():
    # The crosswalk file's scales and the SourceScale enum must be the same
    # closed set — a scale one side knows and the other does not is a silent
    # drop waiting to happen.
    with open(escalation._CROSSWALK, encoding="utf-8") as fh:
        mapped = set(yaml.safe_load(fh))
    assert mapped | {"goldstein"} == _enum_values("SourceScale")
