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
    "source:cow-igo": {
        "name": "COW Intergovernmental Organizations v3",
        "url": "https://correlatesofwar.org/data-sets/IGOs/",
        "citation": "Pevehouse et al. (2020), IGO Version 3.0.",
    },
}


@dataclass
class LoadResult:
    """What a loader did, and what it refused to invent."""

    written: int = 0
    dropped: int = 0
    reasons: dict[str, int] = field(default_factory=dict)
    #: Prior edges a loader cleared before writing, where it owns its source
    #: outright (see `load_alliances`). Reported, never silent.
    replaced: int = 0

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


def prune_off_roster_actors(conn: Any, roster: set[str]) -> dict[str, int]:
    """Remove every Actor no pack names, and everything that hangs off it.

    THE OTHER HALF of `load_state_system`'s scope rule. The loader used to
    invent an Actor for every COW state — 754 against a pack union of 75 — and
    stopping it inventing new ones leaves the old ones on the volume: 21,614
    RELATES_TO edges (COW alliances among states nobody models), 13,984 CINC
    estimates, 37,930 NetworkMetric rows, the militarised disputes between
    them, and Colombia–Venezuela as the first row /api/relations served.

    `roster` is the union of every pack's actor node_ids — the source of
    truth, not `region_pack` on the node (a pack actor could carry an empty
    one). Order matters and every step goes through kuzu_store's delete
    paths, batched under the lock:

      1. Events whose initiator OR target is off-roster — deep-tier MIDs among
         (or against) states the platform does not model. Removing the actor
         alone would DETACH the event's INITIATED_BY / DIRECTED_AT edge and
         leave an event with one side, which breaks the provenance shape every
         reader relies on; the event goes whole, with its AFFECTED edges
         (measurements of an event no surface can reach).
      2. AttributeEstimate nodes attached to off-roster actors — the CINC
         clout that arrived with them.
      3. NetworkMetric rows whose subject is an off-roster actor.
      4. Dyad nodes naming an off-roster actor.
      5. The actors themselves — DETACH DELETE takes their RELATES_TO web.

    Returns what was removed, by table. Idempotent: on a pruned graph every
    count is zero.
    """
    def _ids(query: str) -> list[str]:
        return [str(r["id"]) for r in kuzu_store.query(conn, query, {"roster": sorted(roster)})]

    removed: dict[str, int] = {}
    off = _ids("MATCH (a:Actor) WHERE NOT a.node_id IN $roster RETURN a.node_id AS id")
    if not off:
        return {"Actor": 0}
    params = {"off": off}

    def _ids_off(query: str) -> list[str]:
        return [str(r["id"]) for r in kuzu_store.query(conn, query, params)]

    events = sorted(set(
        _ids_off("MATCH (e:Event)-[:INITIATED_BY]->(a:Actor) WHERE a.node_id IN $off "
                 "RETURN DISTINCT e.node_id AS id")
        + _ids_off("MATCH (e:Event)-[:DIRECTED_AT]->(a:Actor) WHERE a.node_id IN $off "
                   "RETURN DISTINCT e.node_id AS id")
    ))
    removed["Event"] = kuzu_store.delete_nodes(conn, "Event", events)
    estimates = _ids_off(
        "MATCH (a:Actor)-[:HAS_ESTIMATE]->(s:AttributeEstimate) WHERE a.node_id IN $off "
        "RETURN DISTINCT s.node_id AS id")
    removed["AttributeEstimate"] = kuzu_store.delete_nodes(conn, "AttributeEstimate", estimates)
    metrics = _ids_off(
        "MATCH (m:NetworkMetric) WHERE m.subject_id IN $off RETURN m.node_id AS id")
    removed["NetworkMetric"] = kuzu_store.delete_nodes(conn, "NetworkMetric", metrics)
    dyads = _ids_off(
        "MATCH (d:Dyad) WHERE d.actor_a_id IN $off OR d.actor_b_id IN $off "
        "RETURN d.node_id AS id")
    removed["Dyad"] = kuzu_store.delete_nodes(conn, "Dyad", dyads)
    removed["Actor"] = kuzu_store.delete_nodes(conn, "Actor", off)
    return removed


def load_state_system(conn: Any, csv_path: Path) -> LoadResult:
    """COW state-system membership → Actor nodes with membership windows.

    A ccode can hold several membership SPELLS (Estonia 1918-1940 and
    1991-); the Actor window is the envelope — first entry to last exit,
    open when the last spell is right-censored. The gap inside is a fidelity
    loss the single-window model accepts and this docstring records.

    PACK CURATION WINS ON NAMES: a pack that says "United States" is not
    overwritten with "United States of America" — this loader only teaches
    an existing actor its dates.

    AND ONLY AN EXISTING ONE. It used to create an Actor for every state in
    the COW system: 754 of them against a pack roster union of 75, so nine in
    ten actors in the graph belonged to no region the platform models. That is
    not free — they carried 21,614 RELATES_TO edges, 13,984 CINC estimates and
    37,930 NetworkMetric rows between them, and they crowded the relations
    endpoint and the explorer's cast with pairs nobody asked about
    (Colombia-Venezuela was the first row of /api/relations). The packs define
    the scope; this loader dates the actors that scope names, and drops the
    rest with a count.
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
        if prior is None:
            result.drop("not named by any pack roster")
            continue
        nodes.append({
            "node_id": node_id,
            "name": prior["name"],
            "actor_type": prior["actor_type"],
            "cow_ccode": ccode,
            "iso3": prior.get("iso3") or "",
            "region_pack": prior.get("region_pack") or "",
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


def load_igo_memberships(conn: Any, csv_path: Path) -> LoadResult:
    """IGO membership → an org Actor per IGO plus membership RELATES_TO edges.

    The state_year format: one row per state-year, one COLUMN per IGO, value
    1 for full membership (associate/observer 2/3 and missing codes are NOT
    membership and are excluded on purpose). Consecutive years fold into
    SPELLS; a spell reaching the dataset's last year is right-censored and
    stays open. Bipartite by design — states connect THROUGH the IGO node,
    which keeps the network sparse where pairwise membership edges would
    make any two members one hop apart.

    STREAMED, not DictReader'd: 15k rows x 537 columns as dicts is most of a
    gigabyte; as lists it is nothing.
    """
    result = LoadResult()
    _merge_source(conn, "source:cow-igo")
    existing = _existing_actors(conn)

    years_by_pair: dict[tuple[int, int], list[int]] = {}
    last_year = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        igo_names = header[3:]
        for row in reader:
            try:
                ccode, year = int(row[0]), int(row[1])
            except (IndexError, ValueError):
                result.drop("unparseable ccode/year")
                continue
            last_year = max(last_year, year)
            for offset, value in enumerate(row[3:]):
                if value == "1":
                    years_by_pair.setdefault((ccode, offset), []).append(year)

    igos_used: dict[int, str] = {}
    edges: list[dict[str, Any]] = []
    for (ccode, offset), years in sorted(years_by_pair.items()):
        state_id = f"actor:cow-{ccode}"
        if state_id not in existing:
            result.drop("member state not in the graph")
            continue
        years.sort()
        spells: list[tuple[int, int]] = []
        start = prev = years[0]
        for year in years[1:]:
            if year > prev + 1:
                spells.append((start, prev))
                start = year
            prev = year
        spells.append((start, prev))
        for begin, end in spells:
            censored = end >= last_year
            if not censored and end < ARCHIVE_START_YEAR:
                result.drop("membership ended before the archive opens")
                continue
            igos_used[offset] = igo_names[offset]
            edges.append({
                "src": state_id,
                "dst": f"actor:igo-{igo_names[offset].lower()}",
                "relation_type": "membership",
                "valid_from": str(begin),
                "valid_to": "" if censored else str(end),
                "source_id": "source:cow-igo",
            })

    kuzu_store.merge_nodes(conn, "Actor", [
        {
            # The state_year format carries acronyms only; the acronym IS the
            # working name until a fuller registry lands.
            "node_id": f"actor:igo-{name.lower()}", "name": name,
            "actor_type": "org", "iso3": "", "region_pack": "",
        }
        for _, name in sorted(igos_used.items())
    ])
    result.written = kuzu_store.merge_edges(conn, "RELATES_TO", edges)
    return result


def alliance_relation(row: dict[str, str]) -> str | None:
    """COW's four obligation columns → the relation this archive records.

    THE FILE HAS FOUR BOOLEANS AND THE LOADER READ NONE OF THEM. Every row of
    `alliance_v4.1_by_directed.csv` became `relation_type: "alliance"`, so a
    non-aggression treaty and a defence pact arrived indistinguishable — and
    the game layer, which asks "are these two allies?" to decide whether it is
    solving a burden-sharing problem or a contest, believed it. Measured on
    2026-08-17:

        US–Germany   1990-10  defense=1                       → alliance
        US–Poland    1999-03  defense=1                       → alliance
        France–Russia 1990-10 defense=0 nonagg=1 entente=1    → non_aggression
        Germany–Russia 1990-11 defense=0 neutrality=1 …       → non_aggression
        France–Russia 1992-02 defense=0 entente=1             → entente

    France–Russia and Germany–Russia were solved as ALLIES on the eurasia
    board and captioned "formal allies since 1992" on the surface.

    The strongest obligation in the row decides, because a row can carry
    several: a defence pact that also pledges non-aggression is a defence
    pact.
    """
    def on(field: str) -> bool:
        return str(row.get(field, "")).strip() == "1"

    if on("defense"):
        return "alliance"
    if on("neutrality") or on("nonaggression"):
        return "non_aggression"
    if on("entente"):
        return "entente"
    return None


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
    # AUTHORITATIVE FOR ITS OWN SOURCE. Edge identity is
    # (relation_type, valid_from), so a row that changes KIND — which is what
    # reading COW's obligation columns does to every non-defence pact — writes
    # a second edge and leaves the first in place. France–Russia would have
    # kept its "alliance since 1992" beside the new "entente since 1992" and
    # the surface would have gone on calling them formal allies. Clearing this
    # source's edges first makes the loader converge on a re-run instead of
    # accumulating; it is safe because nothing else writes them and the
    # rewrite happens in the same call.
    cleared = kuzu_store.query(
        conn,
        "MATCH (a:Actor)-[r:RELATES_TO]->(b:Actor) "
        "WHERE r.source_id = $source DELETE r RETURN count(*) AS n",
        {"source": "source:cow-alliances"},
    )
    result.replaced = int(cleared[0]["n"]) if cleared else 0

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
            relation = alliance_relation(row)
            if relation is None:
                result.drop("alliance row with no obligation set")
                continue
            edges[(src, dst, valid_from)] = {
                "src": src, "dst": dst, "relation_type": relation,
                "valid_from": valid_from, "valid_to": valid_to,
                "source_id": "source:cow-alliances",
            }
    result.written = kuzu_store.merge_edges(conn, "RELATES_TO", list(edges.values()))
    return result
