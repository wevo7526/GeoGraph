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
