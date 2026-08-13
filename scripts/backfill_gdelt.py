"""Backfill GDELT events from the free raw files — no credentials at all.

  python scripts/backfill_gdelt.py                        # 1979–2005, caching
  python scripts/backfill_gdelt.py mena --from 2006 --to 2026 \
      --min-mentions 50 --harvest-to data/derived           # the modern era
  python scripts/backfill_gdelt.py mena --from-filtered <artifact.tsv.gz>

TWO PATHS, and the difference is the whole story on a 5 GB volume.

The CACHING path (`_fetch`) keeps every archive under GEOGRAPH_RAW_DIR. That
was fine for 1979–2005: 27 yearly zips, 2.0 GB, downloaded once. It does not
survive contact with the modern era — the file eras are YEARLY through 2005,
MONTHLY to 2013-03, then one file per DAY, and the daily files measure ~9 MB
each. 2013-04 to now is ~4,900 of them: about 45 GB, on a volume that holds
five.

The HARVEST path (`--harvest-to`) streams each archive through memory,
filters it against the pack roster, appends the survivors to a PER-YEAR
artifact and discards the download. Peak disk is one file, ~12 MB. What it
leaves behind is what was worth keeping: at --min-mentions 50 the whole
2006–2026 span is single-digit megabytes per pack, against the ~52 GB of
archives it was distilled from. Harvest touches no graph and takes no lock,
so it can run for hours beside a live container; `--from-filtered` then loads
the artifacts, resumably, in a short pass that does need the lock.

Stop the API before LOADING. Ends with the archive-wide Head B rescore and
the provenance backstop.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import io
import os
import sys
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from functools import partial
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
#: The first day the daily export exists.
_DAILY_FROM = dt.date(2013, 4, 1)


def _archives_for_year(year: int) -> list[str]:
    """Every archive covering `year`, in the naming its era uses.

    Three eras, three shapes: one zip a year through 2005, one a month to
    2013-03, one a DAY after that. The daily era is why this function returns
    a list per year rather than a flat list for a range — 2014 alone is 365
    downloads, and a caller that cannot checkpoint between years cannot
    finish.
    """
    if year <= _YEARLY_THROUGH:
        return [f"{year}.zip"]
    names: list[str] = []
    if year <= _MONTHLY_THROUGH[0]:
        last = _MONTHLY_THROUGH[1] if year == _MONTHLY_THROUGH[0] else 12
        names.extend(f"{year}{month:02d}.zip" for month in range(1, last + 1))
    if year >= _MONTHLY_THROUGH[0]:
        day = max(_DAILY_FROM, dt.date(year, 1, 1))
        end = min(dt.date(year, 12, 31), dt.date.today() - dt.timedelta(days=1))
        while day <= end:
            names.append(f"{day:%Y%m%d}.export.CSV.zip")
            day += dt.timedelta(days=1)
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


def _roster(pack: Any) -> dict[str, dict[str, Any]]:
    """iso3 → the node the GDELT country code resolves to for this lens."""
    return {
        a["iso3"]: {"node_id": a["id"], "name": a["name"]}
        for a in pack.actors
        if a.get("iso3")
    }


class _Missing(Exception):
    """A dated archive GDELT does not publish. Some days simply have no file;
    that is a gap in the source, not a failure of this run."""


#: Archives downloaded AND prescreened at once. The work is mostly waiting on
#: data.gdeltproject.org, so a single stream leaves the link idle between
#: files and 4,900 files fetched one at a time is an hour of that idling.
_PARALLEL_FETCHES = 24


def _prefilter(lines: list[str], codes: frozenset[str]) -> list[str]:
    """Lines whose actor pair could match SOME lens, by one bounded split.

    `parse_lines` is the authority on what a lens keeps, but running it once
    per lens meant three full passes over 150,000 lines per archive when the
    same two fields decide nearly all of it. This is one pass against the
    UNION of the rosters; whatever survives goes to the real filter unchanged,
    so the output is identical and only the work is smaller.

    MEASURED, after a slower attempt. Prescreening each line with a compiled
    regex over the roster codes — the obvious "do it in C" move — was TWICE as
    slow: this split has maxsplit=18 and stops eighteen tabs in, while a
    forty-way alternation scans the whole two-kilobyte line, most of which is
    URLs. The C call lost to the interpreter loop it was meant to replace.
    """
    kept = []
    limit = max(_A1_COUNTRY_RAW, _A2_COUNTRY_RAW) + 1
    for line in lines:
        fields = line.split("\t", limit)
        if len(fields) <= limit:
            continue
        if fields[_A1_COUNTRY_RAW] in codes and fields[_A2_COUNTRY_RAW] in codes:
            kept.append(line)
    return kept


def _fetch_and_screen(
    name: str, *, codes: frozenset[str]
) -> tuple[int, list[str]] | None:
    """Download AND prescreen inside the worker thread.

    Handing 150,000 raw lines per archive back to one consumer thread makes
    that thread the bottleneck — at 24 workers it was the whole cost. Handing
    back only the few thousand that survive does not. None means the archive
    is one GDELT does not publish.
    """
    try:
        lines = list(_stream_lines(name))
    except _Missing:
        return None
    return len(lines), _prefilter(lines, codes)


#: Actor country-code columns in the export layout, for the prefilter above.
#: The authoritative indices live in core/ingestion/gdelt.py; these are read
#: from it rather than re-counted, so a layout change moves one file.
_A1_COUNTRY_RAW = gdelt._A1_COUNTRY
_A2_COUNTRY_RAW = gdelt._A2_COUNTRY


def _stream_lines(name: str) -> Iterator[str]:
    """The archive's lines, downloaded to MEMORY and discarded after.

    CACHING THE DAILY ERA IS NOT AN OPTION ON A 5 GB VOLUME. The 1979–2005
    yearly files are 2.0 GB cached and that already dominates the volume; the
    daily era measures ~9 MB a day, which is ~45 GB from 2013-04 to now. The
    output is what is worth keeping — the filtered artifact for the whole
    daily era is single-digit megabytes — so the archives stream through
    memory one at a time and are never written down. Peak footprint is one
    file, about 12 MB.
    """
    url = f"{_BASE}/{name}"
    try:
        with urllib.request.urlopen(url, timeout=300) as response:  # noqa: S310
            payload = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _Missing(name) from exc
        raise
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        member = zf.namelist()[0]
        with zf.open(member) as fh:
            yield from io.TextIOWrapper(fh, encoding="latin-1", errors="replace")


def harvest(
    lenses: list[tuple[Any, dict[str, dict[str, Any]]]],
    *,
    start_year: int,
    end_year: int,
    min_mentions: int,
    out_dir: Path,
) -> dict[str, int]:
    """Stream every archive in [start_year, end_year] into PER-YEAR artifacts,
    filtering it against EVERY lens in one pass.

    All packs together, because the archives are what cost: 2006–2026 is about
    52 GB of downloads, and harvesting three regions one after another would
    stream that three times over for no reason. Each file is fetched once, run
    against each roster, and dropped.

    Per year, not per range, because the daily era makes this thousands of
    downloads and a run that cannot checkpoint cannot finish: a year already
    harvested for a lens is skipped, so an interruption costs the year it was
    in. The artifact is the durable output — a year of filtered lines is
    around a megabyte gzipped against the ~3 GB of archives behind it.

    Nothing is written to the graph. Loading is the --from-filtered path,
    which is separately resumable and needs the write lock this does not.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    totals = {pack.name: 0 for pack, _ in lenses}
    for year in range(start_year, end_year + 1):
        pending = [
            (pack, roster, out_dir / f"gdelt-{pack.name}-{year}.tsv.gz")
            for pack, roster in lenses
            if not (out_dir / f"gdelt-{pack.name}-{year}.tsv.gz").exists()
        ]
        if not pending:
            print(f"{year}: artifacts present for every lens, skipping")
            continue
        names = _archives_for_year(year)
        if not names:
            continue
        kept = {pack.name: 0 for pack, _, _ in pending}
        scanned = missing = 0
        # Write to PARTIAL files and rename at the end: a killed run must not
        # leave a half-year artifact that the skip-check above would then treat
        # as complete.
        # One handle per lens, all open across the year's archives — a
        # with-block per file would reopen and re-header each of them 366
        # times. Closed in the finally below.
        handles = {
            pack.name: gzip.open(  # noqa: SIM115
                target.with_suffix(".gz.partial"), "wt", encoding="latin-1"
            )
            for pack, _, target in pending
        }
        # The union of every lens's roster, for the cheap first pass.
        codes = frozenset().union(*(frozenset(roster) for _, roster, _ in pending))

        fetch = partial(_fetch_and_screen, codes=codes)

        try:
            index = 0
            # Chunked rather than one big map: the executor would otherwise
            # race ahead and hold every archive of the year in memory at once.
            with ThreadPoolExecutor(max_workers=_PARALLEL_FETCHES) as pool:
                for start in range(0, len(names), _PARALLEL_FETCHES):
                    chunk = names[start:start + _PARALLEL_FETCHES]
                    for result in pool.map(fetch, chunk):
                        index += 1
                        if result is None:
                            missing += 1
                            continue
                        seen, candidates = result
                        scanned += seen
                        for pack, roster, _target in pending:
                            events, _edges, _result = gdelt.parse_lines(
                                candidates,
                                actors_by_iso3=roster,
                                region_pack=pack.name,
                                min_mentions=min_mentions,
                                external_powers=pack.external_powers,
                                keep_lines=handles[pack.name],
                            )
                            kept[pack.name] += len(events)
                    tally = " ".join(f"{n} {v:,}" for n, v in sorted(kept.items()))
                    print(
                        f"  {year} {index}/{len(names)} archives · "
                        f"{scanned:,} scanned · {tally}"
                        + (f" · {missing} absent" if missing else ""),
                        flush=True,
                    )
        finally:
            for handle in handles.values():
                handle.close()
        for pack, _roster, target in pending:
            target.with_suffix(".gz.partial").rename(target)
            print(f"{year}: {pack.name} {kept[pack.name]:,} events -> "
                  f"{target.name} ({target.stat().st_size / 1e6:.1f} MB)")
            totals[pack.name] += kept[pack.name]
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pack", nargs="?", default="mena",
        help="a pack name, or 'all' to harvest every installed lens in one "
             "download pass (--harvest-to only)",
    )
    parser.add_argument("--from", dest="start", type=int, default=1979)
    parser.add_argument("--to", dest="end", type=int, default=2005)
    parser.add_argument("--min-mentions", type=int, default=10)
    parser.add_argument(
        "--harvest-to",
        help="stream the archives into PER-YEAR artifacts in this directory "
             "and write nothing to the graph. The only way to cover the daily "
             "era: nothing is cached, so the run costs ~12 MB of disk rather "
             "than the ~45 GB the archives themselves weigh.",
    )
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

    pack = packs.load(packs.available()[0] if args.pack == "all" else args.pack)
    actors_by_iso3 = _roster(pack)
    if not actors_by_iso3:
        sys.exit(f"packs/{pack.name} has no iso3-coded actors to match against.")

    # HARVEST TOUCHES NO GRAPH. Kuzu is single-writer and the API holds the
    # lock, so a step that only downloads and filters must not take it — this
    # can run for hours beside a live container, and the load that does need
    # the lock is a separate, short, resumable pass.
    if args.harvest_to:
        # `all` harvests every installed lens in ONE download pass. The named
        # form stays for a single region, but three sequential harvests stream
        # the same ~52 GB three times.
        if args.pack == "all":
            lenses = [(packs.load(n), _roster(packs.load(n))) for n in packs.available()]
        else:
            lenses = [(pack, actors_by_iso3)]
        totals = harvest(
            lenses,
            start_year=args.start, end_year=args.end,
            min_mentions=args.min_mentions, out_dir=Path(args.harvest_to),
        )
        print(f"\nharvested into {args.harvest_to}")
        for name, count in sorted(totals.items()):
            print(f"  {name:<10} {count:,} events")
        return

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
            archives = [
                name
                for year in range(args.start, args.end + 1)
                for name in _archives_for_year(year)
            ]
            if len(archives) > 24:
                print(
                    f"note: {len(archives)} archives on the CACHING path "
                    f"(~{len(archives) * 9 / 1000:.1f} GB written to "
                    f"{_RAW}). --harvest-to streams instead and keeps nothing."
                )
            for name in archives:
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
