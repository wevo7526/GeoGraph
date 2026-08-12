"""Load the deep tier: COW state system, CINC, MIDs, alliances → the graph.

  python scripts/load_deep_tier.py               # fetch if missing, load, rescore
  python scripts/load_deep_tier.py --no-fetch    # local files only

Build-spec section 5.1 / section 18 Phase 3. Deterministic end to end: the
loaders drop-and-count what they cannot map, and classifier Head B then
rescores escalation over the WHOLE archive in time order — deep MIDs and the
modern marquee spine folding through the same per-dyad EWMA baselines, which
is what "one axis across 120 years" means operationally.

Stop the API first: loading writes, and Kuzu is single-writer.
"""

from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core import settings as settings_module
from core.classifier import escalation
from core.graph import kuzu_store
from core.ingestion import cow

_RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

#: What to fetch and what lands where. The zips carry more than the one file
#: each loader needs; only the named members are extracted.
_FETCH: list[tuple[str, str | None, str]] = [
    # (url, member-inside-zip or None for a bare file, local filename)
    ("https://correlatesofwar.org/wp-content/uploads/states2016.csv",
     None, "states2016.csv"),
    ("https://correlatesofwar.org/wp-content/uploads/NMCv7.zip",
     "NMC-70-wsupplementary.csv", "NMC-70-wsupplementary.csv"),
    ("https://correlatesofwar.org/wp-content/uploads/MID-5-Data-and-Supporting-Materials.zip",
     "MIDA 5.0.csv", "MIDA 5.0.csv"),
    ("https://correlatesofwar.org/wp-content/uploads/MID-5-Data-and-Supporting-Materials.zip",
     "MIDB 5.0.csv", "MIDB 5.0.csv"),
    ("https://correlatesofwar.org/wp-content/uploads/version4.1_csv.zip",
     "version4.1_csv/alliance_v4.1_by_directed.csv", "alliance_v4.1_by_directed.csv"),
]


def fetch_missing() -> None:
    _RAW.mkdir(parents=True, exist_ok=True)
    downloaded: dict[str, bytes] = {}
    for url, member, filename in _FETCH:
        target = _RAW / filename
        if target.exists():
            continue
        if url not in downloaded:
            print(f"fetching {url}")
            with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
                downloaded[url] = response.read()
        payload = downloaded[url]
        if member is None:
            target.write_bytes(payload)
        else:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                # Zip members keep their internal paths; extract flat, and a
                # member the archive renamed FAILS LOUDLY instead of loading
                # a stale local copy forever.
                target.write_bytes(archive.read(member))
        print(f"  -> {target.name}")


#: The Event slots the rescore reads and writes back. Escalation properties
#: ride along and are REPLACED by Head B's result.
_EVENT_PROPS = (
    "name", "event_time", "action_cameo_code", "goldstein", "quad_class",
    "region_pack", "fidelity_tier", "temporal_resolution", "source_scale",
)


def rescore_escalation(conn: Any) -> dict[str, int]:
    """Head B over the whole archive: every event with actors, in time order.

    Deep-tier baselines change how modern spine events read (a US–Iran event
    in 2025 departs from a baseline the 1980s built), so a partial rescore
    would be WRONG by construction — this always folds everything.
    """
    names = {
        row["node_id"]: row["name"]
        for row in kuzu_store.query(
            conn, "MATCH (a:Actor) RETURN a.node_id AS node_id, a.name AS name"
        )
    }
    columns = ", ".join(f"e.{p} AS {p}" for p in _EVENT_PROPS)
    rows = kuzu_store.query(
        conn,
        f"MATCH (e:Event) RETURN e.node_id AS node_id, {columns} "
        "ORDER BY e.event_time, e.node_id",
    )
    by_id = {row["node_id"]: row for row in rows}

    endpoints = {
        row["node_id"]: row
        for row in kuzu_store.query(
            conn,
            "MATCH (e:Event) "
            "OPTIONAL MATCH (e)-[:INITIATED_BY]->(i:Actor) "
            "OPTIONAL MATCH (e)-[:DIRECTED_AT]->(t:Actor) "
            "RETURN e.node_id AS node_id, i.node_id AS initiator, t.node_id AS target",
        )
    }
    stream: list[dict[str, Any]] = []
    for row in rows:
        ends = endpoints.get(row["node_id"], {})
        actor_a = ends.get("initiator") or ends.get("target")
        if not actor_a or row["goldstein"] is None:
            continue
        stream.append({
            "node_id": row["node_id"], "event_time": row["event_time"],
            "goldstein": row["goldstein"],
            "actor_a": actor_a, "actor_b": ends.get("target") or actor_a,
        })

    coding = escalation.code_events(stream, names=names)
    updates: list[dict[str, Any]] = []
    of_dyad: list[dict[str, Any]] = []
    for coded in coding.events:
        base = dict(by_id[coded["node_id"]])
        for key in ("name", "region_pack", "fidelity_tier",
                    "temporal_resolution", "source_scale", "quad_class"):
            base[key] = base.get(key) or ""
        base.update({k: v for k, v in coded.items() if k.startswith("escalation_")})
        updates.append(base)
        of_dyad.append({"src": coded["node_id"], "dst": coded["dyad_id"]})

    kuzu_store.merge_nodes(conn, "Dyad", coding.dyads)
    kuzu_store.merge_nodes(conn, "Event", updates)
    kuzu_store.merge_edges(conn, "OF_DYAD", of_dyad)
    return {"events_rescored": len(updates), "dyads": len(coding.dyads)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-fetch", action="store_true", help="local files only")
    args = parser.parse_args()

    if not args.no_fetch:
        fetch_missing()
    for _, _, filename in _FETCH:
        if not (_RAW / filename).exists():
            sys.exit(f"missing {_RAW / filename} — run without --no-fetch")

    settings = settings_module.load()
    settings.kuzu_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = kuzu_store.connect(settings.kuzu_db_path)
    try:
        kuzu_store.apply_schema(conn)
        steps: list[tuple[str, Callable[[], cow.LoadResult]]] = [
            ("state system", lambda: cow.load_state_system(conn, _RAW / "states2016.csv")),
            ("cinc clout", lambda: cow.load_cinc(conn, _RAW / "NMC-70-wsupplementary.csv")),
            ("mids", lambda: cow.load_mids(conn, _RAW / "MIDA 5.0.csv",
                                           _RAW / "MIDB 5.0.csv")),
            ("alliances", lambda: cow.load_alliances(
                conn, _RAW / "alliance_v4.1_by_directed.csv")),
        ]
        for label, step in steps:
            result = step()
            print(f"{label}: {result.written} written, {result.dropped} dropped")
            for reason, count in sorted(result.reasons.items()):
                print(f"  - {reason}: {count}")
        for key, value in rescore_escalation(conn).items():
            print(f"{key}: {value}")
        violations = kuzu_store.check_provenance(conn)
        if violations:
            sys.exit("PROVENANCE VIOLATIONS:\n" + "\n".join(violations))
        print("provenance: ok")
    finally:
        kuzu_store.close(conn)


if __name__ == "__main__":
    main()
