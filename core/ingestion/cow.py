"""Correlates of War — the deep tier's backbone (build-spec section 5.1).

Four COW products, four loaders, all DETERMINISTIC — never the LLM
(build-spec section 10). Every loader:

  - parses the official CSV exactly as shipped,
  - DROPS AND COUNTS what it cannot map (never infers a fact to tidy a
    parse failure — the invariant in CLAUDE.md),
  - writes through kuzu_store.merge_nodes / merge_edges only, and
  - merges its own Source node FIRST, so the provenance ordering holds no
    matter which packs are seeded.

Dates are ISO strings truncated to what is known: COW marks an unknown day
as -9, so "1902-07" is the honest event_time and `temporal_resolution` says
month. The actor set is TIME-VARYING: states2016.csv is why the graph knows
Austria-Hungary ends.

Flat CSV files, no credentials. PHASE 3.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.classifier import escalation
from core.classifier import typing as event_typing
from core.graph import kuzu_store

#: The archive opens in 1905 (build-spec section 3). Deep records that END
#: before it never touch the graph; windows that straddle it are kept whole.
ARCHIVE_START_YEAR = 1905

#: states2016.csv right-censors at this date — a state "ending" here is a
#: state still in the system, and its window stays open.
_STATES_CENSOR = ("2016", "12", "31")

SOURCES: dict[str, dict[str, str]] = {
    "source:cow-states": {
        "name": "COW State System Membership",
        "url": "https://correlatesofwar.org/data-sets/state-system-membership/",
        "citation": "Correlates of War Project, State System Membership List, v2016.",
    },
    "source:cow-mid": {
        "name": "COW Militarized Interstate Disputes v5.0",
        "url": "https://correlatesofwar.org/data-sets/mids/",
        "citation": 'Palmer et al., "The MID5 Dataset, 2011-2014", CMPS 39(4).',
    },
    "source:cow-nmc": {
        "name": "COW National Material Capabilities v7 (CINC)",
        "url": "https://correlatesofwar.org/data-sets/national-material-capabilities/",
        "citation": "Singer, Bremer & Stuckey (1972), updated NMC 7.0.",
    },
    "source:cow-alliances": {
        "name": "COW Formal Alliances v4.1",
        "url": "https://correlatesofwar.org/data-sets/formal-alliances/",
        "citation": "Gibler (2009), International Military Alliances 1648-2008.",
    },
}


@dataclass
class LoadResult:
    """What a loader did, and what it refused to invent."""

    written: int = 0
    dropped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def drop(self, reason: str) -> None:
        self.dropped += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


def _read_rows(csv_path: Path) -> list[dict[str, str]]:
    """DictReader rows, tolerant of COW's mixed encodings.

    The numeric columns every loader reads are pure ASCII; the free-text
    source-note columns mix UTF-8 and cp1252 across files (NMC v7 does both
    in one file), so the fallback affects only prose nobody parses.
    """
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with open(csv_path, encoding=encoding, newline="") as fh:
                return list(csv.DictReader(fh))
        except UnicodeDecodeError:
            continue
    with open(csv_path, encoding="latin-1", newline="") as fh:
        return list(csv.DictReader(fh))


def _merge_source(conn: Any, source_id: str) -> None:
    meta = SOURCES[source_id]
    kuzu_store.merge_nodes(conn, "Source", [{
        "node_id": source_id, "name": meta["name"], "kind": "dataset",
        "url": meta["url"], "citation": meta["citation"],
    }])


def _iso(year: str, month: str, day: str) -> tuple[str, str]:
    """(ISO date truncated to what is known, temporal resolution).

    COW writes -9 for unknown components; ISO-8601 truncation is the honest
    representation and sorts correctly against full dates (CLAUDE.md: dates
    are strings by design).
    """
    y, m, d = year.strip(), month.strip(), day.strip()
    if not y or y == "-9":
        raise ValueError("year unknown")
    if not m or m == "-9":
        return y, "year"
    if not d or d == "-9":
        return f"{y}-{int(m):02d}", "month"
    return f"{y}-{int(m):02d}-{int(d):02d}", "day"


def _existing_actors(conn: Any) -> dict[str, dict[str, Any]]:
    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor) RETURN a.node_id AS node_id, a.name AS name, "
        "a.actor_type AS actor_type, a.cow_ccode AS cow_ccode, a.iso3 AS iso3, "
        "a.region_pack AS region_pack",
    )
    return {row["node_id"]: row for row in rows}


def load_state_system(conn: Any, csv_path: Path) -> LoadResult:
    """COW state-system membership → Actor nodes with membership windows.

    A ccode can hold several membership SPELLS (Estonia 1918-1940 and
    1991-); the Actor window is the envelope — first entry to last exit,
    open when the last spell is right-censored. The gap inside is a fidelity
    loss the single-window model accepts and this docstring records.

    PACK CURATION WINS ON NAMES: a pack that says "United States" is not
    overwritten with "United States of America" — this loader only teaches
    an existing actor its dates.
    """
    result = LoadResult()
    _merge_source(conn, "source:cow-states")

    spells: dict[int, list[dict[str, str]]] = {}
    for row in _read_rows(csv_path):
        try:
            ccode = int(row["ccode"])
        except (KeyError, ValueError):
            result.drop("unparseable ccode")
            continue
        spells.setdefault(ccode, []).append(row)

    existing = _existing_actors(conn)
    nodes: list[dict[str, Any]] = []
    for ccode, rows in sorted(spells.items()):
        rows.sort(key=lambda r: r["styear"])
        last = rows[-1]
        censored = (last["endyear"], last["endmonth"], last["endday"]) == _STATES_CENSOR
        try:
            state_from, _ = _iso(rows[0]["styear"], rows[0]["stmonth"], rows[0]["stday"])
            state_to = "" if censored else _iso(last["endyear"], last["endmonth"],
                                               last["endday"])[0]
        except ValueError:
            result.drop("unparseable membership dates")
            continue
        if state_to and state_to < str(ARCHIVE_START_YEAR):
            result.drop("left the system before the archive opens")
            continue
        node_id = f"actor:cow-{ccode}"
        prior = existing.get(node_id)
        nodes.append({
            "node_id": node_id,
            "name": prior["name"] if prior else rows[0]["statenme"],
            "actor_type": prior["actor_type"] if prior else "state",
            "cow_ccode": ccode,
            "iso3": (prior or {}).get("iso3") or "",
            "region_pack": (prior or {}).get("region_pack") or "",
            "state_from": state_from,
            "state_to": state_to,
        })
    result.written = kuzu_store.merge_nodes(conn, "Actor", nodes)
    return result


def load_cinc(conn: Any, csv_path: Path) -> LoadResult:
    """NMC → per-state per-year `clout` AttributeEstimates (method=cinc_seed).

    CINC is an index, not a distribution — value_std is 0.0 and the method
    string says the number is a seed; the market-as-sensor loop (Phase 5) is
    what turns these into honest distributions.
    """
    result = LoadResult()
    _merge_source(conn, "source:cow-nmc")
    existing = _existing_actors(conn)

    estimates: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for row in _read_rows(csv_path):
            year = row.get("year", "").strip()
            cinc = row.get("cinc", "").strip()
            try:
                ccode = int(row["ccode"])
                if int(year) < ARCHIVE_START_YEAR:
                    result.drop("before the archive opens")
                    continue
                value = float(cinc)
            except (KeyError, ValueError):
                result.drop("unparseable ccode/year/cinc")
                continue
            actor_id = f"actor:cow-{ccode}"
            if actor_id not in existing:
                result.drop("state not in the graph")
                continue
            estimate_id = f"estimate:clout:cow-{ccode}:{year}"
            estimates.append({
                "node_id": estimate_id, "attribute": "clout",
                "value_mean": value, "value_std": 0.0,
                "as_of": f"{year}-12-31", "method": "cinc_seed",
            })
            edges.append({"src": actor_id, "dst": estimate_id})
    result.written = kuzu_store.merge_nodes(conn, "AttributeEstimate", estimates)
    kuzu_store.merge_edges(conn, "HAS_ESTIMATE", edges)
    return result


def _primary(rows: list[dict[str, str]]) -> dict[str, str] | None:
    """The side's primary participant: originators first, then lowest ccode —
    a deterministic tie-break, documented as such."""
    if not rows:
        return None
    originators = [r for r in rows if r.get("orig", "").strip() == "1"] or rows
    return min(originators, key=lambda r: int(r["ccode"]))


def load_mids(conn: Any, mida_csv: Path, midb_csv: Path) -> LoadResult:
    """MIDs → deep-tier Events via the crosswalks, with actor edges.

    One Event per dispute (MIDA), initiator and target the primary
    participants of sides A and B (MIDB). Hostility level maps through
    cow_to_cameo.yaml to a CAMEO code and through the escalation scale map
    to a Goldstein-equivalent; hostility 1 ("no militarized action") maps to
    no event AT ALL, which the crosswalk says explicitly.

    Head B is NOT run here: escalation folds per dyad across the WHOLE
    archive in time order, so scoring happens once, after every loader, in
    scripts/load_deep_tier.py.
    """
    result = LoadResult()
    _merge_source(conn, "source:cow-mid")
    existing = _existing_actors(conn)

    participants: dict[str, list[dict[str, str]]] = {}
    for row in _read_rows(midb_csv):
        participants.setdefault(row["dispnum"].strip(), []).append(row)

    events: list[dict[str, Any]] = []
    initiated: list[dict[str, Any]] = []
    directed: list[dict[str, Any]] = []
    derived: list[dict[str, Any]] = []
    for row in _read_rows(mida_csv):
            dispnum = row["dispnum"].strip()
            try:
                if int(row["styear"]) < ARCHIVE_START_YEAR:
                    result.drop("before the archive opens")
                    continue
            except (KeyError, ValueError):
                result.drop("unparseable start year")
                continue

            mapped = event_typing.map_deep_event("cow_mid_hostility", row["hostlev"].strip())
            if mapped is None:
                result.drop("hostility 1 — no militarized action, no event")
                continue

            try:
                event_time, resolution = _iso(row["styear"], row["stmon"], row["stday"])
            except ValueError:
                result.drop("unparseable dispute date")
                continue

            side = participants.get(dispnum, [])
            side_a = _primary([r for r in side if r.get("sidea", "").strip() == "1"])
            side_b = _primary([r for r in side if r.get("sidea", "").strip() == "0"])
            if side_a is None or side_b is None:
                result.drop("no participant on one side")
                continue
            initiator = f"actor:cow-{int(side_a['ccode'])}"
            target = f"actor:cow-{int(side_b['ccode'])}"
            if initiator not in existing or target not in existing:
                result.drop("participant state not in the graph")
                continue

            cameo = str(mapped["cameo"])
            event_id = f"event:cow-mid-{dispnum}"
            events.append({
                "node_id": event_id,
                "name": (
                    f"Militarized dispute: {existing[initiator]['name']} – "
                    f"{existing[target]['name']} ({row['styear']})"
                ),
                "event_time": event_time,
                "action_cameo_code": cameo,
                # The HARMONIZED equivalent, not the CAMEO table's score:
                # source_scale says exactly which scale produced the number.
                "goldstein": escalation.harmonize("cow_hostility", row["hostlev"].strip()),
                "quad_class": event_typing.quad_class_for(cameo),
                "region_pack": "",
                "fidelity_tier": "deep_structured",
                "temporal_resolution": resolution,
                "source_scale": "cow_hostility",
            })
            initiated.append({"src": event_id, "dst": initiator, "source_id": "source:cow-mid"})
            directed.append({"src": event_id, "dst": target, "source_id": "source:cow-mid"})
            derived.append({"src": event_id, "dst": "source:cow-mid"})

    result.written = kuzu_store.merge_nodes(conn, "Event", events)
    kuzu_store.merge_edges(conn, "INITIATED_BY", initiated)
    kuzu_store.merge_edges(conn, "DIRECTED_AT", directed)
    kuzu_store.merge_edges(conn, "DERIVED_FROM", derived)
    return result


def load_alliances(conn: Any, csv_path: Path) -> LoadResult:
    """Formal Alliances → RELATES_TO edges with validity windows.

    The by_directed file carries each dyad twice; the ccode1 < ccode2 half is
    kept so an alliance is ONE undirected edge. Multiple spells between the
    same pair are separate edges — (relation_type, valid_from) is the edge
    identity, the key_slots design doing its job.
    """
    result = LoadResult()
    _merge_source(conn, "source:cow-alliances")
    existing = _existing_actors(conn)

    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in _read_rows(csv_path):
            try:
                a, b = int(row["ccode1"]), int(row["ccode2"])
            except (KeyError, ValueError):
                result.drop("unparseable ccodes")
                continue
            if a >= b:
                continue  # the mirror row carries the same alliance
            try:
                valid_from, _ = _iso(row["dyad_st_year"], row["dyad_st_month"],
                                     row["dyad_st_day"])
            except ValueError:
                result.drop("unparseable start date")
                continue
            end_year = row.get("dyad_end_year", "").strip()
            right_censored = row.get("right_censor", "").strip() == "1"
            if right_censored or not end_year:
                valid_to = ""
            else:
                try:
                    valid_to = _iso(end_year, row.get("dyad_end_month", ""),
                                    row.get("dyad_end_day", ""))[0]
                except ValueError:
                    result.drop("unparseable end date")
                    continue
            if valid_to and valid_to < str(ARCHIVE_START_YEAR):
                result.drop("ended before the archive opens")
                continue
            src, dst = f"actor:cow-{a}", f"actor:cow-{b}"
            if src not in existing or dst not in existing:
                result.drop("ally state not in the graph")
                continue
            edges[(src, dst, valid_from)] = {
                "src": src, "dst": dst, "relation_type": "alliance",
                "valid_from": valid_from, "valid_to": valid_to,
                "source_id": "source:cow-alliances",
            }
    result.written = kuzu_store.merge_edges(conn, "RELATES_TO", list(edges.values()))
    return result
