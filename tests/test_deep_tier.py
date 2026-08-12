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


@pytest.fixture()
def conn(tmp_path):
    connection = kuzu_store.connect(tmp_path / "deep.kuzu")
    kuzu_store.apply_schema(connection)
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
"""


def test_state_system_windows_and_the_censor(conn, tmp_path):
    result = cow.load_state_system(conn, _write(tmp_path, "states.csv", _STATES))
    assert result.written == 3  # USA, Austria-Hungary, Estonia
    assert result.reasons == {"left the system before the archive opens": 1}  # Baden

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
    assert rows["actor:cow-300"]["state_to"] == "1918-11-03"
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
10,-9,7,1911,1,11,1911,6,1,0,0,120,100,17,4,1,1,1,0,5
11,5,10,1962,20,11,1962,6,1,0,0,46,46,7,3,0,1,1,0,5
12,1,1,1880,1,2,1880,6,1,0,0,31,31,17,4,0,1,1,0,5
13,1,6,1990,3,6,1990,6,1,0,0,2,2,1,1,0,1,1,0,5
"""

_MIDB = """
dispnum,stabb,ccode,stday,stmon,styear,endday,endmon,endyear,sidea,revstate,revtype1,revtype2,fatality,fatalpre,hiact,hostlev,orig,version
10,GMY,255,-9,7,1911,1,11,1911,1,1,1,-9,0,0,17,4,1,5
10,FRN,220,-9,7,1911,1,11,1911,0,0,-9,-9,0,0,7,3,1,5
11,USA,2,5,10,1962,20,11,1962,1,1,3,-9,0,0,7,3,1,5
11,CUB,40,5,10,1962,20,11,1962,0,0,-9,-9,0,0,7,3,1,5
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
    assert result.written == 2  # Agadir 1911, Cuba 1962
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
    agadir = rows["event:cow-mid-10"]
    # Day unknown (-9) → month resolution, truncated ISO date; hostility 4 →
    # CAMEO 190 via the crosswalk, harmonized Goldstein-equivalent -9.0.
    assert agadir["event_time"] == "1911-07"
    assert agadir["resolution"] == "month"
    assert agadir["cameo"] == "190"
    assert agadir["goldstein"] == -9.0
    assert agadir["source_scale"] == "cow_hostility"
    assert agadir["fidelity_tier"] == "deep_structured"
    assert rows["event:cow-mid-11"]["event_time"] == "1962-10-05"

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
2,200,"United Kingdom",220,"France",8,4,1904,,8,1912,0,0,0,0,0,1,4.1
3,2,"United States",40,"Cuba",1,1,1830,1,1,1860,0,0,1,0,0,0,4.1
"""


def test_alliances_dedupe_and_keep_their_windows(conn, tmp_path):
    cow.load_state_system(conn, _write(tmp_path, "states.csv", _STATES + """
FRN,220,France,1816,1,1,2016,12,31,2016
CUB,40,Cuba,1902,5,20,2016,12,31,2016
UKG,200,United Kingdom,1816,1,1,2016,12,31,2016
"""))
    result = cow.load_alliances(conn, _write(tmp_path, "alliances.csv", _ALLIANCES))
    # NATO once (mirror row dropped), Entente Cordiale once; the 1830-1860
    # alliance ended before the archive opens.
    assert result.written == 2
    assert result.reasons == {"ended before the archive opens": 1}

    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor)-[r:RELATES_TO]->(b:Actor) "
        "RETURN a.node_id AS a, b.node_id AS b, r.valid_from AS valid_from, "
        "r.valid_to AS valid_to ORDER BY valid_from",
    )
    entente, nato = rows[0], rows[1]
    assert (entente["a"], entente["b"]) == ("actor:cow-200", "actor:cow-220")
    assert entente["valid_from"] == "1904-04-08"
    assert entente["valid_to"] == "1912-08"  # end day missing → month truncation
    assert nato["valid_to"] == ""  # right-censored: still in force


_CINC = """
statenme,stateabb,ccode,year,milex,milexsource,milexnote,milper,milpersource,milpernote,irst,irstsource,irstnote,irstqualitycode,irstanomalycode,pec,pecsource,pecnote,pecqualitycode,pecanomalycode,tpop,tpopsource,tpopnote,tpopqualitycode,tpopanomalycode,upop,upopsource,upopnote,upopqualitycode,upopanomalycode,upopgrowth,upopgrowthsource,cinc,version
United States of America,USA,2,1913,244,,,164,,,31300,,,A,,541698,,,A,,97227,,,A,,28453,,,A,,2.1,,0.2223,7
United States of America,USA,2,1900,191,,,125,,,10188,,,A,,244535,,,A,,76391,,,A,,19016,,,A,,3.5,,0.1867,7
Ruritania,RUR,999,1913,1,,,1,,,1,,,A,,1,,,A,,1,,,A,,1,,,A,,0.1,,0.0001,7
"""


def test_cinc_seeds_clout_estimates_for_states_the_graph_knows(conn, tmp_path):
    cow.load_state_system(conn, _write(tmp_path, "states.csv", _STATES))
    result = cow.load_cinc(conn, _write(tmp_path, "nmc.csv", _CINC))
    assert result.written == 1  # USA 1913
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
    assert row["as_of"] == "1913-12-31"
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
