"""GDELT — the modern tier's event firehose, from the FREE RAW FILES.

Build-spec section 5.2 names BigQuery as the transport, but BigQuery bills
query execution to a Google Cloud project — the data is free, the meter is
not credential-less. GDELT publishes THE SAME event tables as plain HTTP
downloads (data.gdeltproject.org, no account, no key): yearly files
1979–2005, monthly 2006–2013, daily thereafter. This module reads those; the
BigQuery path remains a drop-in alternative whenever a project exists.

GDELT 1.0 export format: 57 tab-separated fields, no header. The firehose is
TRUSTED DIRECTLY (section 10) — it arrives CAMEO-coded and Goldstein-scored,
so no classification happens here; `source_scale='goldstein'` carries GDELT's
own score. Rows are FILTERED, not re-coded:

  - both actors resolve to pack-roster states (via the roster's iso3),
  - at least one side is REGIONAL (a USA–RUS wire story is not a MENA event
    however many members the roster has),
  - root events only (IsRootEvent) — GDELT's own de-duplication signal,
  - NumMentions at or above a threshold: one mention is noise, ten is an
    event the world noticed. The threshold is a parameter and every drop is
    counted.

Head B is NOT run here — escalation folds across the whole archive once,
after loading (core/classifier/rescore.py).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

from core.classifier import typing as event_typing
from core.graph import kuzu_store
from core.ingestion.cow import LoadResult

SOURCE_GDELT = "source:gdelt"

_SOURCE_META = {
    "node_id": SOURCE_GDELT,
    "name": "GDELT 1.0 events (raw export files)",
    "kind": "dataset",
    "url": "http://data.gdeltproject.org/events/index.html",
    "citation": (
        "Leetaru & Schrodt (2013), GDELT: Global Data on Events, Location and "
        "Tone, 1979-2012. ISA Annual Convention."
    ),
}

#: GDELT 1.0 export column positions (57 fields, tab-separated, no header).
_GLOBALEVENTID = 0
_SQLDATE = 1
_A1_NAME = 6
_A1_COUNTRY = 7
_A2_NAME = 16
_A2_COUNTRY = 17
_IS_ROOT = 25
_EVENT_CODE = 26
_QUAD = 29
_GOLDSTEIN = 30
_MENTIONS = 31
#: ActionGeo_CountryCode — WHERE the event happened, FIPS 10-4. The one column
#: that can say two allies coded in material conflict with each other were in
#: fact fighting side by side on a third country's soil (see `parse_lines`).
_ACTION_GEO_COUNTRY = 51
#: THE QUALITY COLUMNS, read since 2026-08-17. GDELT fills an actor's COUNTRY
#: code for anyone with a nationality, so a newspaper, a company or a dissident
#: arrives wearing its country's name — "THE ASSOCIATED PRESS" (USAMED) is the
#: United States, "LECH WALESA" (POLELI) is Poland, and a story about either
#: became a material conflict between two states. The TYPE columns are what
#: separate the state from its nationals, and NumSources is what separates a
#: corroborated event from one article. Both ride on the corpus row (like
#: `action_geo`) for `classifier.coercion` to read; neither is a graph
#: property.
_A1_CODE = 5
_A2_CODE = 15
_A1_TYPE1 = 12
_A2_TYPE1 = 22
#: Actor?Geo_Type: 1 = COUNTRY. Anything else (2 US state, 3 US city, 4 world
#: city, 5 world state) is a PLACE INSIDE a country, which is how Birmingham
#: and Alabama came to commit interstate material conflict.
_A1_GEO_TYPE = 35
_A2_GEO_TYPE = 42
_SOURCES = 32

#: CAMEO role suffixes that mean the actor IS the state acting. A bare country
#: code (no suffix) is the state itself.
_STATE_ROLES = frozenset({"GOV", "MIL", "COP", "JUD", "LEG", "SPY"})

_FIPS_CROSSWALK = (
    Path(__file__).resolve().parent.parent / "ontology" / "crosswalks" / "fips_iso3.yaml"
)


@functools.lru_cache(maxsize=1)
def fips_to_iso3() -> dict[str, str]:
    """FIPS 10-4 → ISO3, from the crosswalk. A code absent from it maps to
    nothing — the parser never guesses a country."""
    import yaml

    with open(_FIPS_CROSSWALK, encoding="utf-8") as fh:
        table = yaml.safe_load(fh) or {}
    return {str(k): str(v) for k, v in (table.get("fips_to_iso3") or {}).items()}

#: GDELT QuadClass integers → the ontology's enum.
_QUAD_CLASS = {
    "1": "verbal_cooperation",
    "2": "material_cooperation",
    "3": "verbal_conflict",
    "4": "material_conflict",
}

#: Roster members that do not make an event REGIONAL by themselves — the
#: external powers whose mutual wire traffic would otherwise flood the pack.
#: THE DEFAULT, not the rule: a pack declares its own set in actors.yaml
#: (`external_powers`), because externality is a property of the lens — the
#: USA–RUS dyad is noise to MENA and the spine of Eurasia.
EXTERNAL_POWERS = frozenset({"USA", "RUS"})


#: GDELT 1.0 begins in 1979 by construction; anything earlier is a coding
#: artefact wearing a date.
GDELT_FIRST_YEAR = 1979


def _latest_plausible_year() -> int:
    """Next year, so a row filed just over a new year is not thrown away."""
    import datetime as _dt

    return _dt.date.today().year + 1


def _is_state_actor(
    fields: list[str], code_at: int, name_at: int,
    type_at: int, geo_type_at: int, iso3: str,
) -> bool:
    """Is this actor slot the STATE, rather than a place or body inside it?

    Three ways to qualify, in order of strength:

      1. the actor CODE is the bare country code, or the country code plus a
         state role (USAGOV, RUSMIL) — GDELT's own statement that the actor is
         the government, the military or the police;
      2. the actor is geocoded at COUNTRY level (Geo_Type 1);
      3. there is no geocode at all AND no non-state type — the ordinary case
         for a wire sentence about "Russia" with no place attached.

    A named type that is not a state role disqualifies outright: a newspaper
    with a nationality is not the nation.
    """
    code = (fields[code_at] if len(fields) > code_at else "").strip().upper()
    actor_type = (fields[type_at] if len(fields) > type_at else "").strip().upper()
    geo_type = (fields[geo_type_at] if len(fields) > geo_type_at else "").strip()

    if actor_type and actor_type not in _STATE_ROLES:
        return False
    if code == iso3:
        return True
    if code.startswith(iso3):
        suffix = code[len(iso3):]
        if not suffix or suffix in _STATE_ROLES:
            return True
        # A compound role (USAGOVELI) counts when its FIRST role is a state
        # organ; anything else is a person or body wearing the flag.
        return suffix[:3] in _STATE_ROLES
    # The code did not name this country at all — GDELT matched the actor to
    # the roster by something other than its code, which is exactly the
    # homograph case. Require a country-level geocode to believe it.
    return geo_type == "1"


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_lines(
    lines: Any,
    *,
    actors_by_iso3: dict[str, dict[str, Any]],
    region_pack: str,
    min_mentions: int = 10,
    keep_lines: Any = None,
    external_powers: frozenset[str] = EXTERNAL_POWERS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], LoadResult]:
    """Filter and shape raw export lines → (event rows, edge rows, result).

    Pure and streaming-friendly: `lines` is any iterable of strings, so a
    zip member feeds it without extraction and a test feeds it a list.
    """
    result = LoadResult()
    events: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for line in lines:
        fields = line.rstrip("\n").split("\t")
        if len(fields) < 35:
            result.drop("malformed line")
            continue
        if fields[_IS_ROOT] != "1":
            result.drop("not a root event")
            continue
        try:
            if int(fields[_MENTIONS]) < min_mentions:
                result.drop(f"fewer than {min_mentions} mentions")
                continue
        except ValueError:
            result.drop("malformed line")
            continue
        a1, a2 = fields[_A1_COUNTRY], fields[_A2_COUNTRY]
        if a1 not in actors_by_iso3 or a2 not in actors_by_iso3 or a1 == a2:
            result.drop("actors outside the pack roster")
            continue
        # AND BOTH SIDES MUST ACTUALLY BE THE STATE. A country code is filled
        # in for anyone with a nationality, and GDELT resolves an actor's name
        # by string match, so the roster's flags get pinned to towns, firms and
        # given names: "POLE" arrives as Poland (1,739 actor slots), "ANTIOCH"
        # as Turkey (836 slots, 762 of them geolocated in California),
        # "CANTON" as China (477, 290 in Ohio), "HASSAN" as Jordan (5,619).
        # Reuters, Pfizer, Boeing, Birmingham and Alabama arrive as the United
        # States or the United Kingdom. Only 40.7% of the US–UK
        # material-conflict rows had BOTH actors named at national level.
        #
        # That is where "use of military force between the United States and
        # Poland, 2011" came from, and no filter on the TYPE columns can reach
        # it — three quarters of these rows carry no type at all. The GEO TYPE
        # is what separates a state from a place inside it.
        if not _is_state_actor(fields, _A1_CODE, _A1_NAME, _A1_TYPE1, _A1_GEO_TYPE, a1):
            result.drop("initiator is not the state itself")
            continue
        if not _is_state_actor(fields, _A2_CODE, _A2_NAME, _A2_TYPE1, _A2_GEO_TYPE, a2):
            result.drop("target is not the state itself")
            continue
        if a1 in external_powers and a2 in external_powers:
            result.drop("external-power pair — not a regional event")
            continue
        code = fields[_EVENT_CODE]
        quad = _QUAD_CLASS.get(fields[_QUAD])
        try:
            goldstein = float(fields[_GOLDSTEIN])
            stamp = fields[_SQLDATE]
            event_time = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}"
        except (ValueError, IndexError):
            result.drop("malformed line")
            continue
        # A BAD DATE NEVER RAISED, so it was never dropped: the slice above
        # cannot fail on nonsense, only `float(goldstein)` can. 1,488 rows
        # across the shipped artifacts carry SQLDATE 19200101 — 1,158 of them
        # in mena-2020, and their content is unmistakably the January 2020
        # Soleimani week. That matters more than the count suggests, because
        # `corpus.score` folds Head B in time order and A DYAD'S FIRST EVENT IS
        # ITS BASELINE: the United States–Iran pair opened on 428 mis-dated
        # strike events, so every magnitude, band and feature on the region's
        # defining pair was computed against a corrupted zero.
        if not (stamp.isdigit() and len(stamp) == 8 and
                GDELT_FIRST_YEAR <= int(stamp[:4]) <= _latest_plausible_year()):
            result.drop("event date outside the archive's window")
            continue
        if quad is None:
            result.drop("unknown quad class")
            continue
        try:
            label = event_typing.label_for(code)
        except (KeyError, ValueError):
            result.drop("CAMEO code the archive cannot score")
            continue

        if keep_lines is not None:
            keep_lines.write(line if line.endswith("\n") else line + "\n")
        initiator = actors_by_iso3[a1]
        target = actors_by_iso3[a2]
        event_id = f"event:gdelt-{fields[_GLOBALEVENTID]}"
        # THE ACTION'S COUNTRY, as ISO3 — '' when the row has none or the
        # crosswalk cannot place it. Not a graph property: it rides on the
        # corpus row for the co-participation reading and nowhere else.
        geo = fields[_ACTION_GEO_COUNTRY] if len(fields) > _ACTION_GEO_COUNTRY else ""
        events.append({
            "node_id": event_id,
            "name": f"{label}: {initiator['name']} → {target['name']}",
            "event_time": event_time,
            "action_cameo_code": code,
            "goldstein": goldstein,
            "quad_class": quad,
            "region_pack": region_pack,
            "fidelity_tier": "modern_coded",
            "temporal_resolution": "day",
            "source_scale": "goldstein",
            "action_geo": fips_to_iso3().get(geo, "") if geo else "",
            "initiator_iso3": a1,
            "target_iso3": a2,
            # Corpus-only, like action_geo: what KIND of actor each side is,
            # and how many distinct sources saw it. `classifier.coercion` reads
            # them to decide whether a material-conflict row is one state
            # coercing another or a country's own police blotter.
            "actor1_type": fields[_A1_TYPE1].strip() if len(fields) > _A1_TYPE1 else "",
            "actor2_type": fields[_A2_TYPE1].strip() if len(fields) > _A2_TYPE1 else "",
            "num_sources": _int_or_none(fields[_SOURCES]) if len(fields) > _SOURCES else None,
        })
        edges.append({"kind": "INITIATED_BY", "src": event_id,
                      "dst": initiator["node_id"], "source_id": SOURCE_GDELT})
        edges.append({"kind": "DIRECTED_AT", "src": event_id,
                      "dst": target["node_id"], "source_id": SOURCE_GDELT})
        edges.append({"kind": "DERIVED_FROM", "src": event_id, "dst": SOURCE_GDELT})
    result.written = len(events)
    return events, edges, result


#: Row keys the parser adds for the CORPUS reading that are not Event
#: properties. `merge_nodes` only SETs ontology properties, so they would be
#: ignored anyway; stripped explicitly so the graph write says what it writes.
_CORPUS_ONLY = ("action_geo", "initiator_iso3", "target_iso3")


def write_events(
    conn: Any, events: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> None:
    """Merge parsed GDELT rows: source first (the provenance ordering), then
    events, then edges grouped per relationship table."""
    kuzu_store.merge_nodes(conn, "Source", [_SOURCE_META])
    if not events:
        return
    kuzu_store.merge_nodes(
        conn, "Event",
        [{k: v for k, v in e.items() if k not in _CORPUS_ONLY} for e in events],
    )
    for rel in ("INITIATED_BY", "DIRECTED_AT", "DERIVED_FROM"):
        rows = [
            {k: v for k, v in e.items() if k != "kind"}
            for e in edges
            if e["kind"] == rel
        ]
        kuzu_store.merge_edges(conn, rel, rows)


def backfill(*, region_pack: str, start: str, end: str) -> int:
    """Placeholder signature kept for the BigQuery path; the raw-file loader
    is scripts/backfill_gdelt.py."""
    raise NotImplementedError(
        "use scripts/backfill_gdelt.py (raw files, no credentials); the "
        "BigQuery transport needs BIGQUERY_PROJECT"
    )
