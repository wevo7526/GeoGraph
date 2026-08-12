"""The pack contract, enforced: MENA satisfies it, and the core reads a pack
without knowing its name."""

from __future__ import annotations

import pytest

from core import packs


def test_mena_satisfies_the_contract():
    pack = packs.load("mena")
    assert set(pack.data) == {f.removesuffix(".yaml") for f in packs.PACK_FILES}


@pytest.mark.parametrize("name", packs.available())
def test_every_listed_pack_satisfies_the_contract(name):
    # The P6 exit test, generalized past the pack that first proved it: the
    # core reads EVERY listed pack through the same contract with no
    # special-casing, so a new region is covered by existing.
    pack = packs.load(name)
    assert set(pack.data) == {f.removesuffix(".yaml") for f in packs.PACK_FILES}


def test_the_regions_the_archive_claims_are_all_present():
    assert {"china", "eurasia", "mena"} <= set(packs.available())


def test_an_incomplete_pack_is_absent_not_broken(tmp_path, monkeypatch):
    # The original china condition, preserved as the general rule: a directory
    # missing contract files must not be listed, and loading it names what's
    # missing instead of half-working.
    (tmp_path / "packs" / "partial").mkdir(parents=True)
    (tmp_path / "packs" / "partial" / "sources.yaml").write_text("sources: []\n")
    monkeypatch.setattr(packs, "PACKS_DIR", tmp_path / "packs")
    assert packs.available() == []
    with pytest.raises(packs.PackError, match="marquee_events.yaml|actors.yaml"):
        packs.load("partial")


def test_malformed_paper_books_are_refused():
    pack = packs.load("mena")
    data = dict(pack.data)
    data["assets"] = dict(data["assets"])
    data["assets"]["paper_books"] = {"escalation": {"BZ=F": 0.4}}  # no reversion
    with pytest.raises(packs.PackError, match="reversion"):
        packs._validate(packs.Pack(name="mena", path=pack.path, data=data))
    data["assets"]["paper_books"] = {
        "escalation": {"BZ=F": "heavy"},                # a word, not a weight
        "reversion": {"BZ=F": 0.4},
    }
    with pytest.raises(packs.PackError, match="not a number"):
        packs._validate(packs.Pack(name="mena", path=pack.path, data=data))


@pytest.mark.parametrize("name", packs.available())
def test_every_market_carries_calendar_and_inception(name):
    for market in packs.load(name).markets:
        assert market["inception_date"]
        assert market["trading_calendar"] in {"us", "gulf", "uae", "global_futures"}


def test_shared_market_nodes_agree_across_packs():
    # One market, one node — but two packs can each describe market:gspc, and
    # packs seed in alphabetical order, so a disagreement about an inception
    # date or a frequency era would be silently resolved by whichever ran
    # last. The transmission engine reads BOTH of those fields, so a silent
    # winner is a silently different measurement.
    seen: dict[str, dict[str, str]] = {}
    for name in packs.available():
        for market in packs.load(name).markets:
            claim = {
                key: str(market.get(key))
                for key in ("ticker", "inception_date", "native_frequency",
                            "trading_calendar", "market_type")
            }
            first = seen.setdefault(market["id"], {"pack": name, **claim})
            assert claim == {k: v for k, v in first.items() if k != "pack"}, (
                f"{market['id']} is described differently by {first['pack']} and "
                f"{name}; the node is shared so the description must be too"
            )


def test_gulf_markets_have_no_pre_founding_data():
    markets = {m["ticker"]: m for m in packs.load("mena").markets}
    assert markets["^TASI.SR"]["inception_date"] >= "1985-01-01"
    assert markets["DFMGI.AE"]["inception_date"] >= "2000-01-01"
    assert markets["FADX15.FGI"]["inception_date"] >= "2000-01-01"


@pytest.mark.parametrize("name", packs.available())
def test_marquee_events_reference_roster_actors(name):
    pack = packs.load(name)
    roster = {a["id"] for a in pack.actors}
    for event in pack.marquee_events:
        for role in ("initiator", "target"):
            if event.get(role):
                assert event[role] in roster, (
                    f"{event['id']}.{role} = {event[role]} is not in actors.yaml — "
                    "a marquee event pointing at a ghost actor would seed a "
                    "dangling edge"
                )


def test_the_phase0_episode_is_the_twelve_day_war():
    # One episode, two spine events: Rising Lion opens it, the US strike on
    # the nuclear sites closes it. The overlapping event windows are the
    # point (build-spec §19: flag, never average), not an accident.
    pack = packs.load("mena")
    candidates = [e for e in pack.marquee_events if e.get("phase0_candidate")]
    assert [e["date"] for e in candidates] == ["2025-06-13", "2025-06-22"]


def test_unknown_pack_names_the_available_ones():
    with pytest.raises(packs.PackError, match="mena"):
        packs.load("atlantis")


# ── externality is a property of the lens, not of the world ──────────────────


def test_a_pack_that_says_nothing_keeps_the_default_external_powers():
    # MENA and China predate the key; their wire traffic must not change
    # because a later pack needed to declare its own.
    assert packs.load("mena").external_powers == frozenset({"USA", "RUS"})
    assert packs.load("china").external_powers == frozenset({"USA", "RUS"})


def test_eurasia_declares_the_washington_moscow_dyad_internal():
    # The dyad that is flood noise to the Gulf lens is this lens's spine.
    assert packs.load("eurasia").external_powers == frozenset()


def test_the_declared_set_is_what_the_gdelt_filter_applies():
    from core.ingestion import gdelt

    roster = {
        "USA": {"node_id": "actor:cow-2", "name": "United States"},
        "RUS": {"node_id": "actor:cow-365", "name": "Russia"},
    }
    # A well-formed US→Russia row: 35 tab-separated fields, root event, 40
    # mentions, CAMEO 163 (material conflict), 2014-07-29.
    fields = [""] * 35
    fields[gdelt._GLOBALEVENTID] = "424242"
    fields[gdelt._SQLDATE] = "20140729"
    fields[gdelt._A1_COUNTRY] = "USA"
    fields[gdelt._A2_COUNTRY] = "RUS"
    fields[gdelt._IS_ROOT] = "1"
    fields[gdelt._EVENT_CODE] = "163"
    fields[gdelt._QUAD] = "4"
    fields[gdelt._GOLDSTEIN] = "-8.0"
    fields[gdelt._MENTIONS] = "40"
    line = "\t".join(fields)

    _, _, dropped = gdelt.parse_lines(
        [line], actors_by_iso3=roster, region_pack="mena"
    )
    assert dropped.written == 0, "the default set drops the external-power pair"

    events, _, kept = gdelt.parse_lines(
        [line], actors_by_iso3=roster, region_pack="eurasia",
        external_powers=frozenset(),
    )
    assert kept.written == 1
    assert events[0]["node_id"] == "event:gdelt-424242"
