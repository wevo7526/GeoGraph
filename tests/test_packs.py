"""The pack contract, enforced: MENA satisfies it, and the core reads a pack
without knowing its name."""

from __future__ import annotations

import pytest

from core import packs


def test_mena_satisfies_the_contract():
    pack = packs.load("mena")
    assert set(pack.data) == {f.removesuffix(".yaml") for f in packs.PACK_FILES}


def test_china_satisfies_the_contract():
    # Phase 6 landed the files; the P6 exit test is that the core reads them
    # through the same contract with no special-casing.
    assert packs.available() == ["china", "mena"]
    pack = packs.load("china")
    assert set(pack.data) == {f.removesuffix(".yaml") for f in packs.PACK_FILES}


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


def test_every_market_carries_calendar_and_inception():
    for market in packs.load("mena").markets:
        assert market["inception_date"]
        assert market["trading_calendar"] in {"us", "gulf", "global_futures"}


def test_gulf_markets_have_no_pre_founding_data():
    markets = {m["ticker"]: m for m in packs.load("mena").markets}
    assert markets["^TASI.SR"]["inception_date"] >= "1985-01-01"
    assert markets["DFMGI.AE"]["inception_date"] >= "2000-01-01"
    assert markets["FADX15.FGI"]["inception_date"] >= "2000-01-01"


def test_marquee_events_reference_roster_actors():
    pack = packs.load("mena")
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
