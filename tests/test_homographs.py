"""The curated homograph deny-list — the second half of the state-actor gate.

GDELT resolves an actor to a country by string match, so a town that shares a
name with one of a country's places arrives wearing its flag: "POLE" is
Poland, "BIRMINGHAM" (Alabama) is the United Kingdom. Four automatic
instruments were measured against all 66 artifacts and all four failed
(`gdelt._is_state_actor` carries the numbers), so the discriminator is a
curated file.

WHAT THESE TESTS ARE REALLY PROTECTING is the asymmetry that makes curation
safe. A deny-list's misses keep today's behaviour; an allowlist's misses
delete heads of state, demonyms and capitals. So the tests pin both arms AND
the cases that must survive — because the way this fix fails is not by missing
a homograph, it is by eating the archive. Two earlier filters did exactly
that: a home-soil rule deleted 79% of the war in Ukraine, and an actor-geocode
gate cut Russia-Ukraine from 19,902 rows to 2,229. Measured on the real
artifacts, this list drops 1.79% of roster material-conflict rows and 0.0% of
Russia-Ukraine.
"""

from __future__ import annotations

from core.ingestion import gdelt


def _fields(*, name: str, actor_type: str = "", geo_country: str = "") -> list[str]:
    """One export row, only the columns the gate reads."""
    fields = [""] * 58
    fields[gdelt._A1_NAME] = name
    fields[gdelt._A1_TYPE1] = actor_type
    fields[gdelt._A1_GEO_COUNTRY] = geo_country
    return fields


def _is_state(name: str, iso3: str, *, actor_type: str = "", geo: str = "") -> bool:
    return gdelt._is_state_actor(
        _fields(name=name, actor_type=actor_type, geo_country=geo),
        gdelt._A1_CODE, gdelt._A1_NAME, gdelt._A1_TYPE1, gdelt._A1_GEO_TYPE, iso3,
        gdelt._A1_GEO_COUNTRY,
    )


# ── the crosswalk's shape ────────────────────────────────────────────────────


def test_both_arms_load_as_iso3_name_pairs():
    never, ambiguous = gdelt.homographs()
    assert ("POL", "POLE") in never
    assert ("USA", "ALABAMA") in never
    assert ("USA", "BIRMINGHAM") in never
    assert ("GBR", "BIRMINGHAM") in ambiguous
    assert not (never & ambiguous), "a name belongs to one arm or the other"
    for code, name in never | ambiguous:
        assert code.isupper() and len(code) == 3, code
        assert name == name.upper(), name


def test_no_given_names_are_denied():
    """Deliberately absent, and it must stay that way.

    ABDULLAH scores 96% "located elsewhere" and is also a Saudi king and a
    Jordanian king; HASSAN is already handled by the type gate because it
    always carries JORELI. Denying a given name is how a deny-list acquires
    the allowlist's failure mode — deleting a head of state.
    """
    never, ambiguous = gdelt.homographs()
    denied = {name for _, name in never | ambiguous}
    assert not ({"ABDULLAH", "HASSAN", "OBAMA", "AMIT", "HUSSEIN"} & denied)


# ── never_the_state: dropped outright ────────────────────────────────────────


def test_a_pole_is_not_poland():
    assert not _is_state("POLE", "POL")
    # …with or without a geocode: this arm needs no evidence beyond the name.
    assert not _is_state("POLE", "POL", geo="PL")


def test_poland_is_poland():
    for name in ("POLAND", "POLISH", "WARSAW"):
        assert _is_state(name, "POL"), name


def test_alabama_is_not_the_united_states():
    """The US is UNITED STATES / THE US / WASHINGTON, never a member state."""
    assert not _is_state("ALABAMA", "USA")
    assert not _is_state("BIRMINGHAM", "USA")
    assert _is_state("WASHINGTON", "USA")
    assert _is_state("UNITED STATES", "USA")


# ── ambiguous: dropped only where the row's own geocode disagrees ────────────


def test_birmingham_alabama_is_not_the_united_kingdom():
    assert not _is_state("BIRMINGHAM", "GBR", geo="US")


def test_birmingham_england_is_the_united_kingdom():
    assert _is_state("BIRMINGHAM", "GBR", geo="UK")


def test_an_ambiguous_name_without_a_geocode_is_kept():
    """No geocode is not evidence. The arm cannot fire on what it lacks — and
    failing open is the whole reason this is a deny-list."""
    assert _is_state("BIRMINGHAM", "GBR")
    assert _is_state("BIRMINGHAM", "GBR", geo="")
    # An unresolvable FIPS code is equally not evidence.
    assert _is_state("BIRMINGHAM", "GBR", geo="ZZ")


# ── the cases that must survive ──────────────────────────────────────────────


def test_the_states_own_names_survive_wherever_they_act():
    """A state acting abroad is the archive's subject, not its noise.

    Every one of these scored like a homograph on the rejected per-name
    geocode statistic — NORWAY at 0% self-located, THE US at 11%, DPRK at 13%
    — which is why that instrument could not ship and this file is curated.
    """
    for name, iso3 in (
        ("UNITED STATES", "USA"), ("THE US", "USA"), ("WASHINGTON", "USA"),
        ("RUSSIA", "RUS"), ("MOSCOW", "RUS"), ("UKRAINE", "UKR"),
        ("NORWAY", "NOR"), ("OSLO", "NOR"), ("SLOVAKIA", "SVK"),
        ("DPRK", "PRK"), ("N. KOREA", "PRK"), ("EMIRATI", "ARE"),
        ("CHINA", "CHN"), ("BRITAIN", "GBR"), ("LONDON", "GBR"),
    ):
        assert _is_state(name, iso3, geo="RS"), f"{name} must survive acting abroad"


def test_the_type_gate_still_runs_first():
    """A non-state TYPE loses regardless of name — and it is doing more work
    than it looks: HASSAN's 5,619 slots are all JORELI."""
    assert not _is_state("REUTERS", "USA", actor_type="MED")
    assert not _is_state("UNITED STATES", "USA", actor_type="BUS")
    assert _is_state("UNITED STATES", "USA", actor_type="GOV")


def test_a_denied_name_under_another_country_is_untouched():
    """The list is keyed by (country, name). CANTON is denied for China; the
    same string assigned to the United States is a US place and not this
    file's business."""
    assert not _is_state("CANTON", "CHN", geo="US")
    assert _is_state("CANTON", "USA", geo="US")
