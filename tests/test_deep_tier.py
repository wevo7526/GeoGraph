"""The COW loaders (Phase 3): deterministic parsing, drop-and-count honesty,
membership windows, and the archive-wide Head B rescore. Small synthetic
fixtures in COW's exact column layout — no network, no real downloads."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from core import packs
from core.graph import kuzu_store
from core.ingestion import cow

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_pack = _load("seed_pack")
load_deep_tier = _load("load_deep_tier")


#: The roster a pack would have seeded before the deep tier runs. THE DEEP
#: TIER NO LONGER CREATES ACTORS — it dates and enriches the ones a pack
#: names — so an empty graph is not the state these loaders meet in
#: production, and testing against one measured the wrong contract. COW's
#: state system carries 754 states against a pack roster union of 75, and
#: every one it invented arrived with alliances, CINC estimates and network
#: metrics attached.
_ROSTER = [
    {"node_id": "actor:cow-2", "name": "United States", "actor_type": "state",
     "cow_ccode": 2, "iso3": "USA"},
    {"node_id": "actor:cow-300", "name": "Austria-Hungary",
     "actor_type": "state", "cow_ccode": 300, "iso3": "AUH"},
    {"node_id": "actor:cow-366", "name": "Estonia", "actor_type": "state",
     "cow_ccode": 366, "iso3": "EST"},
    {"node_id": "actor:cow-220", "name": "France", "actor_type": "state",
     "cow_ccode": 220, "iso3": "FRA"},
    {"node_id": "actor:cow-255", "name": "Germany", "actor_type": "state",
     "cow_ccode": 255, "iso3": "DEU"},
    {"node_id": "actor:cow-365", "name": "Russia", "actor_type": "state",
     "cow_ccode": 365, "iso3": "RUS"},
    {"node_id": "actor:cow-40", "name": "Cuba", "actor_type": "state",
     "cow_ccode": 40, "iso3": "CUB"},
    {"node_id": "actor:cow-200", "name": "United Kingdom",
     "actor_type": "state", "cow_ccode": 200, "iso3": "GBR"},
    {"node_id": "actor:cow-710", "name": "China", "actor_type": "state",
     "cow_ccode": 710, "iso3": "CHN"},
    {"node_id": "actor:cow-630", "name": "Iran", "actor_type": "state",
     "cow_ccode": 630, "iso3": "IRN"},
    # A state whose window CLOSES inside the 1972 archive, which is what the
    # censor tests need now that Austria-Hungary sits entirely before the floor.
    {"node_id": "actor:cow-817", "name": "Republic of Vietnam",
     "actor_type": "state", "cow_ccode": 817, "iso3": "VNM"},
]


@pytest.fixture()
def conn(tmp_path):
    connection = kuzu_store.connect(tmp_path / "deep.kuzu")
    kuzu_store.apply_schema(connection)
    kuzu_store.merge_nodes(connection, "Actor", _ROSTER)
    yield connection
    kuzu_store.close(connection)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


_STATES = """
stateabb,ccode,statenme,styear,stmonth,stday,endyear,endmonth,endday,version
USA,2,United States of America,1816,1,1,2016,12,31,2016
AUH,300,Austria-Hungary,1816,1,1,1918,11,3,2016
BAD,267,Baden,1816,1,1,1871,1,18,2016
EST,366,Estonia,1918,11,11,1940,6,16,2016
EST,366,Estonia,1991,9,6,2016,12,31,2016
RVN,817,Republic of Vietnam,1954,6,4,1975,4,30,2016
"""


def test_state_system_windows_and_the_censor(conn, tmp_path):
    result = cow.load_state_system(conn, _write(tmp_path, "states.csv", _STATES))
    assert result.written == 3  # USA, Estonia, South Vietnam
    # Baden (1871) and Austria-Hungary (1918) both left the system before the
    # 1972 floor, so the archive has no window for either.
    assert result.reasons == {"left the system before the archive opens": 2}

    rows = {
        r["node_id"]: r
        for r in kuzu_store.query(
            conn,
            "MATCH (a:Actor) RETURN a.node_id AS node_id, a.name AS name, "
            "a.state_from AS state_from, a.state_to AS state_to",
        )
    }
    # Right-censored membership stays OPEN — the US did not leave the system
    # in 2016; the dataset merely stops there.
    assert rows["actor:cow-2"]["state_to"] == ""
    # A window that closes INSIDE the archive keeps the date it closed on.
    assert rows["actor:cow-817"]["state_to"] == "1975-04-30"
    # An empire that dissolved before the floor is never dated at all.
    assert not rows["actor:cow-300"]["state_from"]
    # Two spells collapse to the envelope; the docstring records the loss.
    assert rows["actor:cow-366"]["state_from"] == "1918-11-11"
    assert rows["actor:cow-366"]["state_to"] == ""


def test_pack_curation_survives_the_state_loader(conn, tmp_path):
    seed_pack.seed(conn, packs.load("mena"))
    cow.load_state_system(conn, _write(tmp_path, "states.csv", _STATES))
    row = kuzu_store.query(
        conn,
        "MATCH (a:Actor {node_id: 'actor:cow-2'}) RETURN a.name AS name, "
        "a.region_pack AS region_pack, a.state_from AS state_from",
    )[0]
    # The pack's curated name and pack tag survive; the loader adds dates.
    assert row["name"] == "United States"
    assert row["region_pack"] == "mena"
    assert row["state_from"] == "1816-01-01"


_MIDA = """
dispnum,stday,stmon,styear,endday,endmon,endyear,outcome,settle,fatality,fatalpre,maxdur,mindur,hiact,hostlev,recip,numa,numb,ongo2014,version
10,-9,7,1980,1,11,1980,6,1,0,0,120,100,17,4,1,1,1,0,5
11,5,10,1998,20,11,1998,6,1,0,0,46,46,7,3,0,1,1,0,5
12,1,1,1880,1,2,1880,6,1,0,0,31,31,17,4,0,1,1,0,5
13,1,6,1990,3,6,1990,6,1,0,0,2,2,1,1,0,1,1,0,5
"""

_MIDB = """
dispnum,stabb,ccode,stday,stmon,styear,endday,endmon,endyear,sidea,revstate,revtype1,revtype2,fatality,fatalpre,hiact,hostlev,orig,version
10,GMY,255,-9,7,1980,1,11,1980,1,1,1,-9,0,0,17,4,1,5
10,FRN,220,-9,7,1980,1,11,1980,0,0,-9,-9,0,0,7,3,1,5
11,USA,2,5,10,1998,20,11,1998,1,1,3,-9,0,0,7,3,1,5
11,CUB,40,5,10,1998,20,11,1998,0,0,-9,-9,0,0,7,3,1,5
12,UKG,200,1,1,1880,1,2,1880,1,1,1,-9,0,0,17,4,1,5
12,FRN,220,1,1,1880,1,2,1880,0,0,-9,-9,0,0,7,3,1,5
13,USA,2,1,6,1990,3,6,1990,1,1,1,-9,0,0,1,1,1,5
13,CUB,40,1,6,1990,3,6,1990,0,0,-9,-9,0,0,1,1,1,5
"""


def test_mids_map_through_the_crosswalk_at_the_dates_known(conn, tmp_path):
    cow.load_state_system(conn, _write(tmp_path, "states.csv", _STATES + """
GMY,255,Germany,1816,1,1,1945,5,8,2016
FRN,220,France,1816,1,1,2016,12,31,2016
CUB,40,Cuba,1902,5,20,2016,12,31,2016
UKG,200,United Kingdom,1816,1,1,2016,12,31,2016
"""))
    result = cow.load_mids(
        conn,
        _write(tmp_path, "mida.csv", _MIDA),
        _write(tmp_path, "midb.csv", _MIDB),
    )
    assert result.written == 2  # Germany–France 1980, US–Cuba 1998
    assert result.reasons == {
        "before the archive opens": 1,             # 1880
        "hostility 1 — no militarized action, no event": 1,  # 1990
    }

    rows = {
        r["node_id"]: r
        for r in kuzu_store.query(
            conn,
            "MATCH (e:Event) RETURN e.node_id AS node_id, e.event_time AS event_time, "
            "e.temporal_resolution AS resolution, e.action_cameo_code AS cameo, "
            "e.goldstein AS goldstein, e.source_scale AS source_scale, "
            "e.fidelity_tier AS fidelity_tier",
        )
    }
    month_only = rows["event:cow-mid-10"]
    # Day unknown (-9) → month resolution, truncated ISO date; hostility 4 →
    # CAMEO 190 via the crosswalk, harmonized Goldstein-equivalent -9.0.
    assert month_only["event_time"] == "1980-07"
    assert month_only["resolution"] == "month"
    assert month_only["cameo"] == "190"
    assert month_only["goldstein"] == -9.0
    assert month_only["source_scale"] == "cow_hostility"
    assert month_only["fidelity_tier"] == "deep_structured"
    assert rows["event:cow-mid-11"]["event_time"] == "1998-10-05"

    initiator = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: 'event:cow-mid-10'})-[:INITIATED_BY]->(a:Actor) "
        "RETURN a.node_id AS id",
    )
    assert initiator[0]["id"] == "actor:cow-255"  # side A originator


_ALLIANCES = """
version4id,ccode1,state_name1,ccode2,state_name2,dyad_st_day,dyad_st_month,dyad_st_year,dyad_end_day,dyad_end_month,dyad_end_year,left_censor,right_censor,defense,neutrality,nonaggression,entente,version
1,2,"United States",200,"United Kingdom",4,4,1949,,,,0,1,1,0,0,0,4.1
1,200,"United Kingdom",2,"United States",4,4,1949,,,,0,1,1,0,0,0,4.1
2,200,"United Kingdom",220,"France",8,4,1974,,8,1992,0,0,0,0,0,1,4.1
3,2,"United States",40,"Cuba",1,1,1830,1,1,1860,0,0,1,0,0,0,4.1
"""


def test_alliances_dedupe_and_keep_their_windows(conn, tmp_path):
    cow.load_state_system(conn, _write(tmp_path, "states.csv", _STATES + """
FRN,220,France,1816,1,1,2016,12,31,2016
CUB,40,Cuba,1902,5,20,2016,12,31,2016
UKG,200,United Kingdom,1816,1,1,2016,12,31,2016
"""))
    result = cow.load_alliances(conn, _write(tmp_path, "alliances.csv", _ALLIANCES))
    # NATO once (mirror row dropped), the UK–France entente once; the 1830-1860
    # alliance ended before the archive opens.
    assert result.written == 2
    assert result.reasons == {"ended before the archive opens": 1}

    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor)-[r:RELATES_TO]->(b:Actor) "
        "RETURN a.node_id AS a, b.node_id AS b, r.valid_from AS valid_from, "
        "r.valid_to AS valid_to ORDER BY valid_from",
    )
    # NATO's 1949 start STRADDLES the floor and is kept whole (core/archive.py).
    nato, entente = rows[0], rows[1]
    assert (entente["a"], entente["b"]) == ("actor:cow-200", "actor:cow-220")
    assert entente["valid_from"] == "1974-04-08"
    assert entente["valid_to"] == "1992-08"  # end day missing → month truncation
    assert nato["valid_to"] == ""  # right-censored: still in force


_CINC = """
statenme,stateabb,ccode,year,milex,milexsource,milexnote,milper,milpersource,milpernote,irst,irstsource,irstnote,irstqualitycode,irstanomalycode,pec,pecsource,pecnote,pecqualitycode,pecanomalycode,tpop,tpopsource,tpopnote,tpopqualitycode,tpopanomalycode,upop,upopsource,upopnote,upopqualitycode,upopanomalycode,upopgrowth,upopgrowthsource,cinc,version
United States of America,USA,2,1990,244,,,164,,,31300,,,A,,541698,,,A,,97227,,,A,,28453,,,A,,2.1,,0.2223,7
United States of America,USA,2,1900,191,,,125,,,10188,,,A,,244535,,,A,,76391,,,A,,19016,,,A,,3.5,,0.1867,7
Ruritania,RUR,999,1990,1,,,1,,,1,,,A,,1,,,A,,1,,,A,,1,,,A,,0.1,,0.0001,7
"""


def test_cinc_seeds_clout_estimates_for_states_the_graph_knows(conn, tmp_path):
    cow.load_state_system(conn, _write(tmp_path, "states.csv", _STATES))
    result = cow.load_cinc(conn, _write(tmp_path, "nmc.csv", _CINC))
    assert result.written == 1  # USA 1990
    assert result.reasons == {
        "before the archive opens": 1,      # 1900
        "state not in the graph": 1,        # Ruritania
    }
    row = kuzu_store.query(
        conn,
        "MATCH (a:Actor {node_id: 'actor:cow-2'})-[:HAS_ESTIMATE]->(s:AttributeEstimate) "
        "RETURN s.attribute AS attribute, s.value_mean AS mean, s.as_of AS as_of, "
        "s.method AS method",
    )[0]
    assert row["attribute"] == "clout"
    assert row["mean"] == pytest.approx(0.2223)
    assert row["as_of"] == "1990-12-31"
    assert row["method"] == "cinc_seed"


def test_the_rescore_folds_deep_and_modern_through_one_baseline(conn, tmp_path):
    """A deep-tier dispute creates the dyad's history; the modern spine then
    departs from a baseline the deep past built — the whole point of one
    harmonized axis."""
    seed_pack.seed(conn, packs.load("mena"))
    cow.load_state_system(conn, _write(tmp_path, "states.csv", _STATES + """
GMY,255,Germany,1816,1,1,1945,5,8,2016
FRN,220,France,1816,1,1,2016,12,31,2016
CUB,40,Cuba,1902,5,20,2016,12,31,2016
UKG,200,United Kingdom,1816,1,1,2016,12,31,2016
"""))
    cow.load_mids(
        conn,
        _write(tmp_path, "mida.csv", _MIDA),
        _write(tmp_path, "midb.csv", _MIDB),
    )
    counts = load_deep_tier.rescore_escalation(conn)
    assert counts["events_rescored"] >= 20  # spine + the two deep disputes
    assert counts["dyads"] >= 15

    deep = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: 'event:cow-mid-10'}) "
        "RETURN e.escalation_direction AS direction, e.escalation_baseline AS baseline",
    )[0]
    assert deep["direction"] in {"escalating", "stable", "deescalating"}
    assert deep["baseline"] is not None
    # The modern spine survives the rescore intact and stays classified.
    modern = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: 'event:mena-2025-midnight-hammer'}) "
        "RETURN e.escalation_direction AS direction, e.goldstein AS goldstein",
    )[0]
    assert modern["direction"] == "escalating"
    assert modern["goldstein"] is not None
    assert kuzu_store.check_provenance(conn) == []


# ── Shiller monthly (the 1871 era of ^GSPC and DGS10) ────────────────────────


def test_shiller_parse_survives_the_float_date_trap():
    from core.ingestion import shiller

    rows, dropped = shiller.parse_monthly([
        {"Date": 1880.01, "P": 5.11, "Rate GS10": 4.02},   # January
        {"Date": 1880.1, "P": 5.35, "Rate GS10": 3.79},    # OCTOBER, not January
        {"Date": None, "P": "Sept price is Sept 1st close", "Rate GS10": "prose"},
    ])
    assert dropped == 1  # the footnote row, once
    by_key = {(r["market_ticker"], r["obs_date"]): r["value"] for r in rows}
    assert by_key[("^GSPC", "1880-01-01")] == 5.11
    assert by_key[("^GSPC", "1880-10-01")] == 5.35  # .1 read as month 10
    assert by_key[("DGS10", "1880-10-01")] == 3.79
    for r in rows:
        assert r["frequency"] == "monthly"
        assert r["source_ref"] == shiller.SOURCE_SHILLER


def test_shiller_a_missing_series_cell_drops_only_that_cell():
    from core.ingestion import shiller

    rows, dropped = shiller.parse_monthly([{"Date": 1990.05, "P": 330.0, "Rate GS10": None}])
    assert dropped == 1
    assert len(rows) == 1
    assert rows[0]["market_ticker"] == "^GSPC"


_IGO_STATE_YEAR = """
ccode,year,state,NATO,OPEC,LON
2,1960,"usa",1,-1,0
2,1961,"usa",0,-1,0
2,1988,"usa",0,-1,1
2,1989,"usa",1,-1,1
2,1990,"usa",1,-1,1
2,1991,"usa",1,-1,0
300,1918,"auh",0,-1,-9
"""


def test_igo_membership_spells_fold_and_censor(conn, tmp_path):
    cow.load_state_system(conn, _write(tmp_path, "states.csv", _STATES))
    result = cow.load_igo_memberships(
        conn, _write(tmp_path, "igo.csv", _IGO_STATE_YEAR)
    )
    # USA-NATO 1989-1991 (censored open: 1991 is the file's last year),
    # USA-LON 1988-1990; USA-NATO 1960-1960 ended before the archive;
    # Austria-Hungary's row is not membership (value 0/-1) so nothing writes.
    assert result.written == 2
    assert result.reasons == {"membership ended before the archive opens": 1}

    rows = {
        (r["a"], r["b"]): r
        for r in kuzu_store.query(
            conn,
            "MATCH (a:Actor)-[r:RELATES_TO {relation_type: 'membership'}]->(b:Actor) "
            "RETURN a.node_id AS a, b.node_id AS b, r.valid_from AS valid_from, "
            "r.valid_to AS valid_to",
        )
    }
    nato = rows[("actor:cow-2", "actor:igo-nato")]
    assert nato["valid_from"] == "1989"
    assert nato["valid_to"] == ""  # reaches the dataset's last year: open
    lon = rows[("actor:cow-2", "actor:igo-lon")]
    assert (lon["valid_from"], lon["valid_to"]) == ("1988", "1990")
    # The IGO node itself is an org actor the graph can traverse through.
    igo = kuzu_store.query(
        conn,
        "MATCH (a:Actor {node_id: 'actor:igo-nato'}) RETURN a.actor_type AS t, a.name AS n",
    )[0]
    assert igo["t"] == "org"
    assert igo["n"] == "NATO"
    assert kuzu_store.check_provenance(conn) == []


# ── 13F flows (P4's credential-free half) ────────────────────────────────────


_INFOTABLE = """<?xml version="1.0"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>ACME CORP</nameOfIssuer>
    <value>1500</value>
    <shrsOrPrnAmt><sshPrnamt>10</sshPrnamt></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>OTHER INC</nameOfIssuer>
    <value>2500</value>
    <shrsOrPrnAmt><sshPrnamt>20</sshPrnamt></shrsOrPrnAmt>
  </infoTable>
</informationTable>
"""


def test_13f_values_normalize_by_the_reporting_unit_transition():
    from core.ingestion import edgar_13f

    # Before 2023 the table reports THOUSANDS; after, dollars. Same XML,
    # different report period, different honest total.
    old = edgar_13f.parse_information_table(
        _INFOTABLE.encode(), report_date="2021-12-31"
    )
    new = edgar_13f.parse_information_table(
        _INFOTABLE.encode(), report_date="2024-12-31"
    )
    assert old == 4_000_000  # (1500 + 2500) thousands
    assert new == 4_000      # dollars as filed


def test_13f_refuses_xml_that_is_not_an_information_table():
    from core.ingestion import edgar_13f

    with pytest.raises(ValueError, match="not a 13F information table"):
        edgar_13f.parse_information_table(
            b"<?xml version='1.0'?><coverPage><name>x</name></coverPage>",
            report_date="2024-12-31",
        )


def test_13f_flows_land_as_quarterly_edges(conn, monkeypatch):
    from core import packs as packs_module
    from core.ingestion import edgar_13f

    seed_pack.seed(conn, packs_module.load("mena"))
    monkeypatch.setattr(
        edgar_13f, "fetch_filings",
        lambda cik, limit=8: [
            {"report_date": "2025-03-31", "accession": "a1", "value_usd": 5_000_000},
            {"report_date": "2025-06-30", "accession": "a2", "value_usd": 6_000_000},
        ],
    )
    written = edgar_13f.load_flows(
        conn,
        [{"actor": "actor:swf-pif", "cik": 1767640, "name": "PIF"}],
        market_node_id="market:gspc",
    )
    assert written == 2  # as_of is identity: two quarters, two edges
    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor)-[f:FLOW]->(m:Market) RETURN a.node_id AS actor, "
        "f.as_of AS as_of, f.value_usd AS value_usd, f.source_id AS source_id "
        "ORDER BY as_of",
    )
    assert [r["as_of"] for r in rows] == ["2025-03-31", "2025-06-30"]
    assert rows[0]["value_usd"] == 5_000_000
    assert all(r["source_id"] == "source:edgar-13f" for r in rows)
    assert kuzu_store.check_provenance(conn) == []


# ── GDELT raw-file parsing (the credential-free modern tier) ─────────────────


def _gdelt_line(**kw: str) -> str:
    fields = [""] * 57
    fields[0] = kw.get("id", "12345")
    fields[1] = kw.get("date", "19980215")
    fields[6] = kw.get("a1name", "IRAN")
    fields[7] = kw.get("a1", "IRN")
    fields[16] = kw.get("a2name", "ISRAEL")
    fields[17] = kw.get("a2", "ISR")
    fields[25] = kw.get("root", "1")
    fields[26] = kw.get("code", "138")
    fields[29] = kw.get("quad", "3")
    fields[30] = kw.get("goldstein", "-5.8")
    fields[31] = kw.get("mentions", "12")
    return "\t".join(fields)


_ACTORS = {
    "IRN": {"node_id": "actor:cow-630", "name": "Iran"},
    "ISR": {"node_id": "actor:cow-666", "name": "Israel"},
    "USA": {"node_id": "actor:cow-2", "name": "United States"},
    "RUS": {"node_id": "actor:cow-365", "name": "Russia"},
}


def test_gdelt_keeps_the_significant_regional_root_events():
    from core.ingestion import gdelt

    events, edges, result = gdelt.parse_lines(
        [
            _gdelt_line(),                                   # kept
            _gdelt_line(id="2", mentions="3"),               # below threshold
            _gdelt_line(id="3", root="0"),                   # not a root event
            _gdelt_line(id="4", a1="USA", a2="RUS"),         # external-power pair
            _gdelt_line(id="5", a2="ZWE"),                   # outside the roster
            "too\tshort",                                    # malformed
        ],
        actors_by_iso3=_ACTORS,
        region_pack="mena",
        min_mentions=10,
    )
    assert result.written == 1
    assert result.dropped == 5
    event = events[0]
    assert event["node_id"] == "event:gdelt-12345"
    assert event["event_time"] == "1998-02-15"
    assert event["goldstein"] == -5.8            # GDELT's own score, trusted
    assert event["quad_class"] == "verbal_conflict"
    assert event["fidelity_tier"] == "modern_coded"
    assert "Iran" in event["name"] and "Israel" in event["name"]
    kinds = [e["kind"] for e in edges]
    assert kinds == ["INITIATED_BY", "DIRECTED_AT", "DERIVED_FROM"]


def test_gdelt_sourceurl_stays_on_the_corpus_row_not_the_graph() -> None:
    """V2 SOURCEURL is a mention, often the wrong article. Keep it off the
    graph (and off any citation href) even when it is a well-formed URL."""
    from core.ingestion import gdelt

    fields = _gdelt_line().split("\t")
    fields.extend([""] * (61 - len(fields)))
    fields[59] = "20260818120000"
    fields[60] = "https://www.mlb.com/news/unrelated-baseball-story"
    events, _, result = gdelt.parse_lines(
        ["\t".join(fields)],
        actors_by_iso3=_ACTORS,
        region_pack="mena",
        min_mentions=10,
    )
    assert result.written == 1
    assert events[0]["source_url"] == "https://www.mlb.com/news/unrelated-baseball-story"
    assert "source_url" in gdelt._CORPUS_ONLY


def test_gdelt_events_land_sourced_and_rescorable(conn):
    from core.classifier.rescore import rescore_escalation
    from core.ingestion import gdelt

    seed_pack.seed(conn, packs.load("mena"))
    events, edges, _ = gdelt.parse_lines(
        [_gdelt_line(), _gdelt_line(id="99", date="19990301", goldstein="-9.0",
                                    code="190", quad="4")],
        actors_by_iso3=_ACTORS,
        region_pack="mena",
    )
    gdelt.write_events(conn, events, edges)
    counts = rescore_escalation(conn)
    assert counts["events_rescored"] >= 26  # spine + the two GDELT rows
    row = kuzu_store.query(
        conn,
        "MATCH (e:Event {node_id: 'event:gdelt-99'}) "
        "RETURN e.escalation_direction AS d, e.escalation_baseline AS b",
    )[0]
    assert row["d"] in {"escalating", "stable", "deescalating"}
    assert row["b"] is not None
    assert kuzu_store.check_provenance(conn) == []


def test_the_deep_tier_dates_pack_actors_and_invents_none(conn, tmp_path):
    """THE SCOPE IS THE PACKS', and the deep tier used to ignore that.

    `load_state_system` wrote an Actor for every state in the COW system —
    754 of them against a pack roster union of 75 — so nine in ten actors in
    the graph belonged to no region the platform models. They were not inert:
    they arrived with alliances, CINC estimates and NetworkMetric rows, and
    Colombia-Venezuela was the first row /api/relations served.

    The loader's own docstring already said it "only teaches an existing actor
    its dates". Now it does.
    """
    # An off-roster state appended to the fixture: Colombia is exactly the
    # kind of actor that used to arrive unasked and bring alliances with it.
    states = _write(
        tmp_path, "states.csv",
        _STATES + chr(10) + "COL,100,Colombia,1831,11,21,2016,12,31,2016",
    )
    before = {
        str(r["node_id"])
        for r in kuzu_store.query(conn, "MATCH (a:Actor) RETURN a.node_id AS node_id")
    }
    result = cow.load_state_system(conn, states)
    after = {
        str(r["node_id"])
        for r in kuzu_store.query(conn, "MATCH (a:Actor) RETURN a.node_id AS node_id")
    }

    assert after == before, f"the deep tier invented actors: {after - before}"
    assert result.written > 0, "it must still date the actors the packs named"
    assert "actor:cow-100" not in after, "Colombia arrived unasked"
    assert result.reasons.get("not named by any pack roster"), result.reasons

    # And the dating actually happened — the point of the loader.
    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor) WHERE a.node_id = 'actor:cow-2' "
        "RETURN a.state_from AS f, a.name AS name",
    )
    assert rows[0]["f"], "the US should have learned its membership window"
    assert rows[0]["name"] == "United States", (
        "pack curation still wins on names — not 'United States of America'"
    )


def test_the_prune_removes_off_roster_actors_and_everything_hanging_off_them(conn, tmp_path):
    """THE OTHER HALF OF THE SCOPE RULE. Stopping the loader inventing actors
    leaves the ones already on the volume — with their alliances, estimates,
    metrics and disputes. The prune takes them, and only them, and leaves no
    one-sided event behind.
    """
    # Colombia arrives off-roster the way the old loader would have created it,
    # with a CINC estimate, a metric, an alliance to a roster actor, a MID
    # against one, and a dyad — every kind of thing that hung off the 754.
    kuzu_store.merge_nodes(conn, "Actor", [
        {"node_id": "actor:cow-100", "name": "Colombia", "actor_type": "state",
         "cow_ccode": 100, "iso3": "COL"},
    ])
    kuzu_store.merge_nodes(conn, "Source", [
        {"node_id": "source:test", "name": "Test", "kind": "dataset", "url": "", "citation": ""},
    ])
    kuzu_store.merge_nodes(conn, "AttributeEstimate", [
        {"node_id": "estimate:col-clout-1913", "attribute": "clout", "value_mean": 0.001,
         "value_std": 0.0, "method": "cinc", "as_of": "1913-12-31"},
        {"node_id": "estimate:usa-clout-1913", "attribute": "clout", "value_mean": 0.22,
         "value_std": 0.0, "method": "cinc", "as_of": "1913-12-31"},
    ])
    kuzu_store.merge_edges(conn, "HAS_ESTIMATE", [
        {"src": "actor:cow-100", "dst": "estimate:col-clout-1913"},
        {"src": "actor:cow-2", "dst": "estimate:usa-clout-1913"},
    ])
    kuzu_store.merge_nodes(conn, "NetworkMetric", [
        {"node_id": "metric:col-1", "subject_id": "actor:cow-100", "metric_name": "degree",
         "value": 1.0, "window_start": "1990-01-01", "window_end": "1999-12-31",
         "method": "test"},
        {"node_id": "metric:usa-1", "subject_id": "actor:cow-2", "metric_name": "degree",
         "value": 9.0, "window_start": "1990-01-01", "window_end": "1999-12-31",
         "method": "test"},
    ])
    kuzu_store.merge_edges(conn, "RELATES_TO", [
        {"src": "actor:cow-100", "dst": "actor:cow-2", "relation_type": "alliance",
         "valid_from": "1947-09-02", "source_id": "source:test"},
        {"src": "actor:cow-2", "dst": "actor:cow-200", "relation_type": "alliance",
         "valid_from": "1949-04-04", "source_id": "source:test"},
    ])
    kuzu_store.merge_nodes(conn, "Event", [
        {"node_id": "event:cow-mid-col", "name": "MID", "event_time": "1990-06-01",
         "action_cameo_code": "190", "goldstein": -9.0, "quad_class": "material_conflict",
         "fidelity_tier": "deep_structured", "temporal_resolution": "day",
         "source_scale": "cow_hostility", "region_pack": ""},
        {"node_id": "event:cow-mid-usuk", "name": "MID", "event_time": "1991-06-01",
         "action_cameo_code": "190", "goldstein": -9.0, "quad_class": "material_conflict",
         "fidelity_tier": "deep_structured", "temporal_resolution": "day",
         "source_scale": "cow_hostility", "region_pack": ""},
    ])
    kuzu_store.merge_edges(conn, "INITIATED_BY", [
        {"src": "event:cow-mid-col", "dst": "actor:cow-2", "source_id": "source:test"},
        {"src": "event:cow-mid-usuk", "dst": "actor:cow-2", "source_id": "source:test"},
    ])
    kuzu_store.merge_edges(conn, "DIRECTED_AT", [
        {"src": "event:cow-mid-col", "dst": "actor:cow-100", "source_id": "source:test"},
        {"src": "event:cow-mid-usuk", "dst": "actor:cow-200", "source_id": "source:test"},
    ])
    kuzu_store.merge_nodes(conn, "Dyad", [
        {"node_id": "dyad:cow-100--cow-2", "name": "Colombia–United States",
         "actor_a_id": "actor:cow-100", "actor_b_id": "actor:cow-2"},
        {"node_id": "dyad:cow-2--cow-200", "name": "United States–United Kingdom",
         "actor_a_id": "actor:cow-2", "actor_b_id": "actor:cow-200"},
    ])

    roster = {str(a["node_id"]) for a in _ROSTER}
    removed = cow.prune_off_roster_actors(conn, roster)
    assert removed == {"Event": 1, "AttributeEstimate": 1, "NetworkMetric": 1,
                       "Dyad": 1, "Actor": 1}, removed

    def _count(query: str) -> int:
        return int(kuzu_store.query(conn, query)[0]["n"])

    assert _count("MATCH (a:Actor) RETURN count(a) AS n") == len(_ROSTER)
    assert _count("MATCH (a:Actor {node_id: 'actor:cow-100'}) RETURN count(a) AS n") == 0
    # The roster's own things are untouched, one of each.
    assert _count("MATCH (s:AttributeEstimate) RETURN count(s) AS n") == 1
    assert _count("MATCH (m:NetworkMetric) RETURN count(m) AS n") == 1
    assert _count("MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS n") == 1
    assert _count("MATCH (e:Event) RETURN count(e) AS n") == 1
    assert _count("MATCH (d:Dyad) RETURN count(d) AS n") == 1
    # NO ONE-SIDED EVENT: what remains carries both actor edges.
    assert _count(
        "MATCH (e:Event)-[:INITIATED_BY]->(:Actor) WITH e "
        "MATCH (e)-[:DIRECTED_AT]->(:Actor) RETURN count(e) AS n"
    ) == 1
    assert kuzu_store.check_provenance(conn) == []
    # Idempotent.
    assert cow.prune_off_roster_actors(conn, roster) == {"Actor": 0}


def test_cow_alliance_obligations_are_read_not_flattened(tmp_path):
    """COW FILES FOUR OBLIGATIONS UNDER ONE NAME, and the loader read none.

    `alliance_v4.1_by_directed.csv` carries `defense`, `neutrality`,
    `nonaggression` and `entente` as separate booleans. Every row became
    `relation_type: "alliance"`, so a non-aggression treaty arrived
    indistinguishable from a defence pact — and the game layer, which asks "are
    these two allies?" to decide whether it is solving a burden-sharing problem
    or a contest, believed it. On 2026-08-17 France–Russia (a 1992 entente) and
    Germany–Russia (a 1990 non-aggression treaty) were solved as ALLIES on the
    eurasia board and captioned "formal allies" on the surface.

    The strongest obligation in the row decides, because a row can carry
    several: a defence pact that also pledges non-aggression is a defence pact.
    """
    from core.ingestion.cow import alliance_relation

    assert alliance_relation({"defense": "1"}) == "alliance"
    assert alliance_relation(
        {"defense": "1", "nonaggression": "1", "entente": "1"}
    ) == "alliance", "the strongest obligation wins"
    assert alliance_relation({"defense": "0", "nonaggression": "1"}) == "non_aggression"
    assert alliance_relation({"defense": "0", "neutrality": "1"}) == "non_aggression"
    assert alliance_relation({"defense": "0", "entente": "1"}) == "entente"
    assert alliance_relation({"defense": "0"}) is None, "no obligation is not a relation"

    # The rows that actually caused it, from the shipped file.
    from pathlib import Path
    raw = Path(__file__).resolve().parents[1] / "data" / "raw" / "alliance_v4.1_by_directed.csv"
    if not raw.exists():
        return
    import csv
    seen: dict[tuple[int, int], set[str | None]] = {}
    with open(raw, encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            try:
                a, b = int(row["ccode1"]), int(row["ccode2"])
            except (KeyError, ValueError):
                continue
            if a >= b or int(row.get("dyad_st_year") or 0) < 1985:
                continue
            seen.setdefault((a, b), set()).add(alliance_relation(row))
    # 220 France, 365 Russia, 255 Germany, 2 United States, 290 Poland.
    assert "alliance" not in seen.get((220, 365), set()), "France–Russia is not a defence pact"
    assert "alliance" not in seen.get((255, 365), set()), "Germany–Russia is not a defence pact"
    assert "alliance" in seen.get((2, 290), set()), "US–Poland (NATO, 1999) is one"


def test_shared_membership_does_not_make_two_states_allies():
    """`family.classify` read `kinds & {"alliance", "membership"}` as ally,
    which says "both are in the same IGO" means "these two would fight for
    each other" — the United Nations would make every pair on earth allies."""
    from core.games import family as family_module

    membership_only = family_module.classify(
        {"relations": [{"relation_type": "membership", "since": "1945-10-24"}]},
        {"share": 0.05, "events": 400, "coercive": 20, "thin": False},
    )
    assert membership_only["family"] != "ally"

    pact = family_module.classify(
        {"relations": [{"relation_type": "alliance", "since": "1949-04-04"}]},
        {"share": 0.05, "events": 400, "coercive": 20, "thin": False},
    )
    assert pact["family"] == "ally", "a defence pact still does"
