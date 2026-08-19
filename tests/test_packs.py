"""The pack contract, enforced: MENA satisfies it, and the core reads a pack
without knowing its name."""

from __future__ import annotations

from pathlib import Path

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


def test_a_lens_does_not_claim_another_lens_exclusive_markets():
    """Shared instruments (Brent, the S&P, gold, Treasuries) appear in every
    pack. Exclusive sensors do not: Hang Seng is Asia's, the DAX is Eurasia's,
    Tadawul is the Gulf's. Game pricing that ignored this priced US–Russia
    to TAIEX because a deep-tier war had been measured against every ticker.
    """
    by_pack = {
        name: {m["id"] for m in packs.load(name).markets}
        for name in packs.available()
    }
    asia_only = {
        "market:hsi", "market:twii", "market:n225", "market:kospi", "market:sse",
    }
    eurasia_only = {
        "market:gdaxi", "market:ftse", "market:fchi", "market:imoex",
        "market:wheat", "market:natgas",
    }
    gulf_only = {"market:tasi", "market:dfmgi", "market:adx"}
    shared = {"market:gspc", "market:brent", "market:gold", "market:dgs10", "market:dgs3mo"}
    for mid in asia_only:
        assert mid in by_pack["china"], mid
        assert mid not in by_pack["eurasia"] | by_pack["mena"], mid
    for mid in eurasia_only:
        assert mid in by_pack["eurasia"], mid
        assert mid not in by_pack["china"] | by_pack["mena"], mid
    for mid in gulf_only:
        assert mid in by_pack["mena"], mid
        assert mid not in by_pack["china"] | by_pack["eurasia"], mid
    for mid in shared:
        for name in by_pack:
            assert mid in by_pack[name], f"{mid} missing from {name}"


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


# ── the key is not the caption ───────────────────────────────────────────────


def test_a_pack_is_called_what_it_declares_and_keyed_by_its_directory():
    # The label is presentation and may change freely; the NAME is written into
    # every record's region_pack and into the deployed volume, so it must not
    # drift with the caption. `packs/china` reads as Asia — the lens covers
    # Taiwan, Japan and Korea — while staying keyed `china`.
    pack = packs.load("china")
    assert pack.label == "Asia"
    assert pack.name == "china"


@pytest.mark.parametrize("name", packs.available())
def test_every_pack_has_a_label_even_when_it_declares_none(name):
    # A pack whose name already reads as a region declares nothing and gets its
    # name back — no pack can end up captionless.
    assert packs.load(name).label


def test_a_key_is_never_shown_as_a_caption():
    # The markets story renders the region into prose, and it rendered the KEY:
    # "when mena's coded record shows a sharp escalation". `mena` is an
    # internal acronym and `eurasia` is a lower-cased id; both now declare what
    # a reader should be shown, and neither key moved (they are written into
    # every record's region_pack and into the deployed volume).
    mena, eurasia = packs.load("mena"), packs.load("eurasia")
    assert (mena.name, mena.label) == ("mena", "the Middle East")
    assert (eurasia.name, eurasia.label) == ("eurasia", "Eurasia")


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


def test_no_pack_relation_collides_with_a_cow_alliance():
    """A curated declaration must not be silently overwritten by a loader.

    The edge identity for RELATES_TO is (relation_type, valid_from), read off
    the ontology. A pack row and a COW row on the same pair, same type, same
    START DATE are therefore THE SAME EDGE — and the deep tier runs after the
    pack seed, so COW wins and the curator's intent disappears without a word.

    Found on 2026-08-16: packs/mena declared US-Israel an alliance from
    1981-11-30, the date of the strategic-cooperation MoU. COW carries that
    same MoU and ENDS it 1991-12-26, so the merged edge expired and the pair
    read "under no relation the archive has declared" — the exact defect the
    declaration had been added to fix. It is dated at the 1987 Major
    Non-NATO Ally designation now, which is both non-colliding and the
    standing actually still in force.

    Skipped where the COW file is not present (it is a large raw download, not
    committed); it runs in CI wherever the deep tier can run at all.
    """
    import csv

    path = (Path(__file__).resolve().parent.parent
            / "data" / "raw" / "alliance_v4.1_by_directed.csv")
    if not path.exists():
        pytest.skip("COW alliance file not present")

    cow: dict[tuple[str, ...], str] = {}
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                a, b = int(row["ccode1"]), int(row["ccode2"])
            except (KeyError, ValueError):
                continue
            if a >= b:
                continue
            year = row["dyad_st_year"].strip()
            month = row["dyad_st_month"].strip()
            day = row["dyad_st_day"].strip()
            if not year or year == "-9":
                continue
            if not month or month == "-9":
                iso = year
            elif not day or day == "-9":
                iso = f"{year}-{int(month):02d}"
            else:
                iso = f"{year}-{int(month):02d}-{int(day):02d}"
            end = row["dyad_end_year"].strip()
            censored = row.get("right_censor", "").strip() == "1"
            ends = "" if (censored or not end or end == "-9") else end
            key = (f"actor:cow-{a}", f"actor:cow-{b}", "alliance", iso)
            # Keep the WIDEST window COW gives this identity: a spell it
            # leaves open is the one that would survive the merge.
            if key not in cow or not ends:
                cow[key] = ends

    collisions = []
    for name in packs.available():
        for rel in packs.load(name).relations:
            key = (*sorted((rel["a"], rel["b"])), rel["relation_type"],
                   str(rel.get("valid_from")))
            if key not in cow:
                continue
            # A collision only MATTERS when the two disagree about the window.
            # COW carries Russia-Kazakhstan from the same 1992 date and leaves
            # it open, which is exactly what the pack declares — the merge is
            # a no-op and forcing a different date would be busywork. What
            # must never pass silently is COW closing a window the curator
            # meant to leave open, which is what happened to US-Israel.
            cow_end = cow[key]
            pack_end = str(rel.get("valid_to") or "")
            if cow_end != pack_end:
                collisions.append(
                    f"{name}: {key} — the pack says valid_to "
                    f"{pack_end or '(open)'}, COW says {cow_end or '(open)'}"
                )
    assert not collisions, (
        "these curated relations share an edge identity with a COW alliance "
        "AND disagree with it about the window, so the deep-tier load (which "
        "runs after the pack seed) will overwrite the curator silently — give "
        "them a different, defensible valid_from, or drop them and let COW "
        f"carry the pair: {collisions}"
    )
