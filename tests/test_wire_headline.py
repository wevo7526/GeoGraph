"""Wire display headlines: CAMEO-root verbs, third-country force as location.

The composer the reader sees lives in `web/src/lib/story.ts`. This file pins
the Python twin so a coding defect (GDELT naming the United States on a
fight in Syria) cannot ship as "Israel used force toward the United States"
without the test noticing — and so we never "fix" it by rewriting the stored
dyad.
"""

from __future__ import annotations

from core.classifier import coercion
from core.wire import headline as wire_headline

_MENA = {
    "USA": "United States",
    "ISR": "Israel",
    "SYR": "Syria",
    "IRN": "Iran",
    "UKR": "Ukraine",
    "RUS": "Russia",
}


def _row(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "initiator_name": "Israel",
        "target_name": "United States",
        "initiator_iso3": "ISR",
        "target_iso3": "USA",
        "action_geo": "SYR",
        "action_cameo_code": "190",
        "quad_class": "material_conflict",
        "num_sources": 4,
        "actor1_type": "MIL",
        "actor2_type": "GOV",
    }
    base.update(kw)
    return base


def test_quad_four_is_not_always_used_force_toward() -> None:
    assert wire_headline.act_phrase(_row(action_cameo_code="190")) == "fought"
    assert wire_headline.act_phrase(_row(action_cameo_code="150")) == "exhibited force toward"
    assert wire_headline.act_phrase(_row(action_cameo_code="173")) == "coerced"
    assert "used force toward" not in {
        wire_headline.act_phrase(_row(action_cameo_code=code))
        for code in ("150", "163", "173", "190", "194")
    }


def test_a_fight_on_third_roster_soil_is_headlined_in_that_country() -> None:
    """Israel–United States with action_geo Syria is not an A–B fight on the
    surface. The stored pair is left alone — this is not a retarget."""
    row = _row()
    stored = (row["initiator_iso3"], row["target_iso3"], row["action_geo"])
    text = wire_headline.headline(row, geo_names=_MENA)
    assert text == "Israel used force in Syria"
    assert "United States" not in text
    assert stored == ("ISR", "USA", "SYR"), "the helper must not rewrite the row"
    fields = wire_headline.display_fields(row, geo_names=_MENA)
    assert fields["third_country_force"] is True
    assert fields["pair_fight"] is False
    assert fields["action_geo"] == "SYR"
    assert fields["action_geo_name"] == "Syria"
    assert row["target_iso3"] == "USA"


def test_russia_ukraine_on_ukrainian_soil_stays_a_pair_fight() -> None:
    """Do not drop third-country fights by dropping home-soil wars. The war
    is fought on UKR soil; that is one of the pair, not a third country."""
    row = _row(
        initiator_name="Russia",
        target_name="Ukraine",
        initiator_iso3="RUS",
        target_iso3="UKR",
        action_geo="UKR",
        action_cameo_code="190",
    )
    text = wire_headline.headline(row, geo_names=_MENA)
    assert text == "Russia fought Ukraine"
    assert wire_headline.third_country_force(row, set(_MENA)) is False
    assert wire_headline.pair_fight(row, set(_MENA)) is True
    assert coercion.counts_as_coercion(row, allied=False)


def test_us_iran_in_syria_is_force_in_syria_not_a_dropped_row() -> None:
    """A real third-country fight still displays; it is not deleted."""
    row = _row(
        initiator_name="United States",
        target_name="Iran",
        initiator_iso3="USA",
        target_iso3="IRN",
        action_geo="SYR",
        action_cameo_code="194",
    )
    assert wire_headline.headline(row, geo_names=_MENA) == "United States used force in Syria"
    assert wire_headline.pair_fight(row, set(_MENA)) is False


def test_a_geo_off_the_roster_is_still_a_third_country() -> None:
    """A code we cannot name is still not an A–B fight. We do not invent
    a place-name; we do not keep 'Israel fought United States' either."""
    row = _row(action_geo="ATA")  # Antarctica is on no pack
    text = wire_headline.headline(row, geo_names=_MENA)
    assert text == "Israel used force in a third country"
    assert wire_headline.third_country_force(row, set(_MENA)) is True
    assert wire_headline.pair_fight(row, set(_MENA)) is False


def test_us_uk_assault_on_british_soil_is_not_a_pair_war() -> None:
    """The relationship page's defect: CAMEO 18/19 between NATO partners
    is not 'the United States assaulted the United Kingdom'."""
    names = {**_MENA, "GBR": "United Kingdom"}
    row = _row(
        initiator_name="United States",
        target_name="United Kingdom",
        initiator_iso3="USA",
        target_iso3="GBR",
        action_geo="GBR",
        action_cameo_code="182",
        allied=True,
    )
    text = wire_headline.headline(row, geo_names=names)
    assert "assaulted" not in text
    assert "fought" not in text
    assert "not a fight between them" in text
    assert wire_headline.allied_presence(row) is True
    assert wire_headline.pair_fight(row, set(names)) is False
    fields = wire_headline.display_fields(row, geo_names=names)
    assert fields["allied_presence"] is True
    assert fields["pair_fight"] is False
    assert "assaulted" not in fields["headline"]


def test_coded_title_is_not_the_headline() -> None:
    row = _row(
        name="Use conventional military force: United States → United Kingdom",
        initiator_name="",
        target_name="",
        initiator_iso3="USA",
        target_iso3="GBR",
        action_geo="IRQ",
        action_cameo_code="190",
        allied=True,
    )
    names = {**_MENA, "GBR": "United Kingdom", "IRQ": "Iraq"}
    text = wire_headline.headline(row, geo_names=names)
    assert text == "United States used force in Iraq"
    parsed = wire_headline.names_from_coded_title(str(row["name"]))
    assert parsed == ("United States", "United Kingdom")


def test_live_material_conflict_is_not_a_pair_fight_unless_coercion() -> None:
    """Optional live gate: one source is not interstate coercion, so the
    surface must not offer the row as an A–B fight. The row is not dropped."""
    thin = _row(num_sources=1, action_geo="ISR", coercion=False)
    thin["coercion"] = coercion.counts_as_coercion(thin)
    assert thin["coercion"] is False
    assert wire_headline.pair_fight(thin, set(_MENA)) is False
    # Same coding, corroborated, still a pair fight (home soil of one side).
    thick = _row(num_sources=4, action_geo="ISR")
    thick["coercion"] = coercion.counts_as_coercion(thick)
    assert thick["coercion"] is True
    assert wire_headline.pair_fight(thick, set(_MENA)) is True


def test_exhibit_force_is_not_used_force_toward() -> None:
    row = _row(action_cameo_code="150", action_geo="ISR")
    assert wire_headline.headline(row, geo_names=_MENA) == (
        "Israel exhibited force toward United States"
    )
