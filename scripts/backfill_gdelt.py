"""Backfill GDELT events from the free raw files — no credentials at all.

  python scripts/backfill_gdelt.py                       # 1979–2005 (yearly files)
  python scripts/backfill_gdelt.py --from 1979 --to 2013 # + monthly era
  python scripts/backfill_gdelt.py --min-mentions 5      # denser, noisier

The file eras (data.gdeltproject.org/events/): YEARLY zips through 2005,
MONTHLY 200601–201303, DAILY thereafter. The daily era is ~9 MB per day —
a full backfill of it is tens of gigabytes, so it is deliberately NOT the
default; extend --to only as far as the disk and the patience go. Downloads
cache under GEOGRAPH_RAW_DIR/gdelt (the Railway volume), and zip members are
parsed as streams — nothing is extracted to disk.

Stop the API first: writing events needs the Kuzu write lock. Ends with the
archive-wide Head B rescore and the provenance backstop.
"""

from __future__ import annotations

import argparse
import gzip
import io
import os
import sys
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from core import packs
from core import settings as settings_module
from core.classifier.rescore import rescore_escalation
from core.graph import kuzu_store
from core.ingestion import gdelt

_RAW = Path(
    os.getenv("GEOGRAPH_RAW_DIR")
    or Path(__file__).resolve().parent.parent / "data" / "raw"
) / "gdelt"

_BASE = "http://data.gdeltproject.org/events"

#: The eras and their file naming. Monthly begins 2006-01; daily 2013-04.
_YEARLY_THROUGH = 2005
_MONTHLY_THROUGH = (2013, 3)


def _archives(start_year: int, end_year: int) -> list[str]:
    names: list[str] = []
    for year in range(start_year, min(end_year, _YEARLY_THROUGH) + 1):
        names.append(f"{year}.zip")
    for year in range(max(start_year, 2006), end_year + 1):
        last_month = _MONTHLY_THROUGH[1] if year == _MONTHLY_THROUGH[0] else 12
        if year > _MONTHLY_THROUGH[0]:
            break
        for month in range(1, last_month + 1):
            names.append(f"{year}{month:02d}.zip")
    if end_year > _MONTHLY_THROUGH[0]:
        print(
            "note: the daily era (2013-04 onward) is not backfilled by default — "
            "~9 MB per day; extend deliberately."
        )
    return names


#: Events per write batch on the --from-filtered path. Large enough that the
#: per-batch overhead is noise against ~100k merges, small enough that a
#: killed process loses seconds of work rather than an hour of it.
_BATCH_LINES = 10_000


def _existing_event_ids(conn: Any, pack_name: str) -> set[str]:
    """GDELT node ids the graph already holds for this lens.

    One query and a set of ~100k short strings — a few MB — against the
    alternative of re-merging every event that is already there.
    """
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event) WHERE e.node_id STARTS WITH 'event:gdelt-' "
        "AND e.region_pack = $pack RETURN e.node_id AS node_id",
        {"pack": pack_name},
    )
    return {str(row["node_id"]) for row in rows}


def _fresh_lines(lines: Iterable[str], existing: set[str]) -> Iterator[str]:
    """Artifact lines whose event is NOT already in the graph.

    The id is field 0 of the export line, and `parse_lines` builds the node
    id from it the same way — so this filter and the writer agree by
    construction rather than by a second parse.
    """
    if not existing:
        yield from lines
        return
    for line in lines:
        if f"event:gdelt-{line.split(chr(9), 1)[0]}" in existing:
            continue
        yield line


def _batched(items: Iterable[str], size: int) -> Iterator[list[str]]:
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _fetch(name: str) -> Path:
    target = _RAW / name
    if target.exists():
        return target
    _RAW.mkdir(parents=True, exist_ok=True)
    url = f"{_BASE}/{name}"
    print(f"fetching {url}")
    with urllib.request.urlopen(url, timeout=300) as response:  # noqa: S310
        target.write_bytes(response.read())
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", nargs="?", default="mena")
    parser.add_argument("--from", dest="start", type=int, default=1979)
    parser.add_argument("--to", dest="end", type=int, default=2005)
    parser.add_argument("--min-mentions", type=int, default=10)
    parser.add_argument(
        "--export-filtered",
        help="ALSO write the kept raw lines to this gz file — a derived cache "
             "that lets a container load in seconds instead of re-parsing "
             "sixty million lines every boot",
    )
    parser.add_argument(
        "--from-filtered",
        help="load from a previously exported gz instead of the archives "
             "(no downloads, no full parse — the boot path)",
    )
    args = parser.parse_args()

    pack = packs.load(args.pack)
    actors_by_iso3 = {
        a["iso3"]: {"node_id": a["id"], "name": a["name"]}
        for a in pack.actors
        if a.get("iso3")
    }
    if not actors_by_iso3:
        sys.exit(f"packs/{pack.name} has no iso3-coded actors to match against.")

    settings = settings_module.load()
    conn = kuzu_store.connect(settings.kuzu_db_path)
    # Closed in the finally below; the write sites are two try branches deep
    # and a with-block could not reach them.
    exporter = (
        gzip.open(args.export_filtered, "wt", encoding="latin-1")  # noqa: SIM115
        if args.export_filtered
        else None
    )
    try:
        kuzu_store.apply_schema(conn)
        total_written = 0
        total_dropped = 0
        if args.from_filtered:
            # RESUMABLE AND BATCHED, because this step is the one that gets
            # killed. A single parse-everything-then-write-once pass loses all
            # of its work to a timeout and then redoes every merge on the next
            # attempt, so a load too slow to finish once can never finish at
            # all. Skipping ids the graph already holds makes a resumed load
            # cost only what is left, and writing per batch means an
            # interrupted run keeps the batches it completed.
            existing = _existing_event_ids(conn, pack.name)
            if existing:
                print(f"graph already holds {len(existing)} events for this lens")
            with gzip.open(args.from_filtered, "rt", encoding="latin-1") as fh:
                for batch in _batched(_fresh_lines(fh, existing), _BATCH_LINES):
                    events, edges, result = gdelt.parse_lines(
                        batch,
                        actors_by_iso3=actors_by_iso3,
                        region_pack=pack.name,
                        min_mentions=args.min_mentions,
                        external_powers=pack.external_powers,
                    )
                    gdelt.write_events(conn, events, edges)
                    total_written += result.written
                    total_dropped += result.dropped
                    print(f"  +{result.written} ({total_written} this run)", flush=True)
            print(f"{Path(args.from_filtered).name}: {total_written} events")
        else:
            for name in _archives(args.start, args.end):
                archive = _fetch(name)
                with zipfile.ZipFile(archive) as zf:
                    member = zf.namelist()[0]
                    with zf.open(member) as fh:
                        lines = io.TextIOWrapper(fh, encoding="latin-1", newline="")
                        if exporter is not None:
                            events, edges, result = gdelt.parse_lines(
                                lines,
                                actors_by_iso3=actors_by_iso3,
                                region_pack=pack.name,
                                min_mentions=args.min_mentions,
                                external_powers=pack.external_powers,
                                keep_lines=exporter,
                            )
                        else:
                            events, edges, result = gdelt.parse_lines(
                                lines,
                                actors_by_iso3=actors_by_iso3,
                                region_pack=pack.name,
                                min_mentions=args.min_mentions,
                                external_powers=pack.external_powers,
                            )
                gdelt.write_events(conn, events, edges)
                total_written += result.written
                total_dropped += result.dropped
                print(f"{name}: {result.written} events")
        print(f"total: {total_written} written, {total_dropped} filtered")
        for key, value in rescore_escalation(conn).items():
            print(f"{key}: {value}")
        violations = kuzu_store.check_provenance(conn)
        if violations:
            sys.exit("PROVENANCE VIOLATIONS:\n" + "\n".join(violations))
        print("provenance: ok")
    finally:
        if exporter is not None:
            exporter.close()
        kuzu_store.close(conn)


if __name__ == "__main__":
    main()
