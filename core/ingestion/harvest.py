"""Fetching GDELT's daily exports — the library both the CLI and the job use.

The shape follows `transmission/runner.py`: the driver is a library so the CLI
and the background job run the SAME code, rather than a script and a
re-implementation that drift.

THE OVERLAY, and why the harvest does not append to the committed artifacts.
`data/derived/*.tsv.gz` is in git and ships in the image; a container's copy of
it is replaced on every deploy. So a job that appended there would lose every
harvested day the moment you shipped a commit — silently, because the file
would still be present and still parse. Harvested days therefore go to a
SEPARATE directory on the volume (`GEOGRAPH_HARVEST_DIR`), and `corpus`
globs both. The committed artifacts are the base; the volume holds only what
was learned after the image was built.

WHY THIS EXISTS. Twelve jobs keep the platform current without a deploy, and
not one of them could fetch a new EVENT: the study measures the corpus, so
without a harvest the archive was frozen at whatever was last committed.
Every other job re-derives from that corpus; harvest is the only one that
learns a new fact.

Ordering is not a concern: `corpus.score` sorts by `(event_time, node_id)`
before folding Head B, so an overlay file read after the base is still scored
in time order. That matters more than it looks — a dyad's first event is its
baseline.

COST. One daily export is ~9 MB and serves every lens (it is screened once per
roster), and it yields ~130 rows across the three packs. A day is cheap; the
reason to bound the job anyway is that a cold start after a long gap is not.
"""

from __future__ import annotations

import datetime as dt
import gzip
import io
import os
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from core.ingestion import gdelt

BASE = "http://data.gdeltproject.org/events"

#: The first day GDELT publishes a per-day export. Before this the era is
#: monthly, and before 2006 yearly — `backfill_gdelt.py` covers those; this
#: module is the DAILY era only, because that is the only one that can still
#: gain a file.
DAILY_FROM = dt.date(2013, 4, 1)

#: Actor country-code columns, read from the parser rather than re-counted so
#: a layout change moves one file.
_A1_COUNTRY = gdelt._A1_COUNTRY
_A2_COUNTRY = gdelt._A2_COUNTRY

#: The artifacts are latin-1: GDELT's exports carry bytes that are not valid
#: UTF-8 and the original harvest wrote them through unchanged. Reading or
#: writing them as UTF-8 corrupts the archive.
ENCODING = "latin-1"

#: THE BAR THE MODERN ERA WAS HARVESTED AT, and it is NOT `corpus.MIN_MENTIONS`.
#:
#: Those two constants answer different questions and conflating them is a data
#: bug, which is how this shipped wrong the first time. `corpus.MIN_MENTIONS`
#: (10) is the floor the PARSER applies when re-reading an artifact; the
#: artifacts were already filtered when they were written, so it keeps
#: everything and never binds. This is the bar that decides what a NEW day
#: contains, and it has to match the era it extends —
#: `backfill_gdelt.py --min-mentions 50` built every artifact from 2006 on.
#:
#: Measured, after harvesting four days at 10 by mistake: every committed
#: artifact from 2015 and 2026 has a minimum NumMentions of exactly 50, and
#: the loose days ran ~3x denser than the ones before them (mena 247 rows/day
#: against a committed average of 79). UNEVEN DENSITY IS THIS ARCHIVE'S
#: DEFINING HAZARD — it has silently wrecked two estimators — and a step change
#: on the day the harvest switched on is exactly the shape that does it. More
#: rows is not more data when the rows are not comparable.
MIN_MENTIONS = int(os.getenv("GEOGRAPH_HARVEST_MIN_MENTIONS", "50"))

_MARKER = "harvested-through.txt"


class Missing(Exception):
    """GDELT does not publish this archive (a 404, not a failure)."""


def harvest_dir() -> Path | None:
    """Where harvested days are written, or None when harvesting is off.

    Read PER CALL, like `corpus.derived_dir`, so a monkeypatched env var takes
    effect whenever the module was imported. Absent by default: without it the
    job no-ops and the platform behaves exactly as it did before.
    """
    raw = os.getenv("GEOGRAPH_HARVEST_DIR", "").strip()
    return Path(raw) if raw else None


def daily_archive_name(day: dt.date) -> str:
    return f"{day:%Y%m%d}.export.CSV.zip"


def stream_lines(name: str, *, timeout: int = 300) -> Iterator[str]:
    """One archive's lines, downloaded to MEMORY and discarded after.

    Nothing is cached. The daily era is ~9 MB a day and ~45 GB since 2013-04,
    against a volume that holds five; the filtered output is what is worth
    keeping, and it measures single-digit megabytes per lens per year.
    """
    url = f"{BASE}/{name}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            payload = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise Missing(name) from exc
        raise
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        member = archive.namelist()[0]
        with archive.open(member) as handle:
            yield from io.TextIOWrapper(handle, encoding=ENCODING)


def prefilter(lines: Iterable[str], codes: frozenset[str]) -> list[str]:
    """Lines whose actor pair could match SOME lens, by one bounded split.

    `gdelt.parse_lines` remains the authority on what a lens keeps; this is one
    cheap pass against the UNION of the rosters so the real filter sees
    thousands of lines instead of a hundred and fifty thousand. Whatever
    survives goes on unchanged, so the output is identical and only the work is
    smaller.
    """
    kept: list[str] = []
    limit = max(_A1_COUNTRY, _A2_COUNTRY) + 1
    for line in lines:
        fields = line.split("\t", limit)
        if len(fields) <= limit:
            continue
        if fields[_A1_COUNTRY] in codes and fields[_A2_COUNTRY] in codes:
            kept.append(line)
    return kept


def artifact_for(out_dir: Path, pack_name: str, year: int) -> Path:
    """The overlay artifact for one lens and year.

    `.harvest.` is in the name so the two sets can never be confused for one
    another on disk, and so a stray copy into the committed directory does not
    silently double every row (`corpus` globs `gdelt-<pack>-*.tsv.gz`).
    """
    return out_dir / f"gdelt-{pack_name}-{year}.harvest.tsv.gz"


def harvested_through(out_dir: Path) -> dt.date | None:
    """The last day already harvested, or None.

    A single marker for every lens, because one download serves them all: a
    per-lens marker would let the three drift and re-download the same file
    three times.
    """
    try:
        raw = (out_dir / _MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        return None


def mark_harvested(out_dir: Path, day: dt.date) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / _MARKER).write_text(day.isoformat() + "\n", encoding="utf-8")


def days_to_harvest(
    *, through: dt.date | None, committed_through: dt.date | None,
    today: dt.date, limit: int,
) -> list[dt.date]:
    """Which days this tick should fetch, oldest first.

    Starts after whichever is later — the marker or the last day the COMMITTED
    artifacts already cover — so a fresh volume does not re-download years the
    image already holds.

    Stops at YESTERDAY. GDELT publishes a day's export the following day, so
    asking for today is a guaranteed 404, and a 404 that is really "not yet"
    must not be recorded as harvested.
    """
    floor = DAILY_FROM - dt.timedelta(days=1)
    for candidate in (through, committed_through):
        if candidate and candidate > floor:
            floor = candidate
    start = floor + dt.timedelta(days=1)
    end = today - dt.timedelta(days=1)
    days: list[dt.date] = []
    day = start
    while day <= end and len(days) < limit:
        days.append(day)
        day += dt.timedelta(days=1)
    return days


def append_day(
    day: dt.date, *, lenses: list[tuple[Any, dict[str, Any]]], out_dir: Path,
) -> dict[str, int]:
    """Fetch one day, screen it against every lens, append what survives.

    Returns rows-kept per pack. A day GDELT does not publish yields zeros and
    is still counted as done by the caller — the gap is GDELT's, and blocking
    on it would stall every later day behind one missing file.

    Appending to a gzip file writes a SECOND MEMBER rather than rewriting the
    first, which is valid gzip and what `gzip.open` reads back transparently.
    That is the property that makes a daily append cheap: no read-modify-write
    of a year's artifact for the sake of a hundred rows.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    codes = frozenset().union(*(frozenset(roster) for _, roster in lenses))
    try:
        kept = prefilter(stream_lines(daily_archive_name(day)), codes)
    except Missing:
        return {pack.name: 0 for pack, _ in lenses}

    written: dict[str, int] = {}
    for pack, roster in lenses:
        # `keep_lines` is a FILE-LIKE (parse_lines calls .write), and it is
        # buffered here rather than pointed at the gzip handle so that a lens
        # which keeps nothing does not leave an empty member behind — 365 of
        # those a year for a lens that rarely matches. A day is ~130 rows
        # across all three packs, so the buffer is kilobytes.
        buffer = io.StringIO()
        rows, _edges, _result = gdelt.parse_lines(
            kept, actors_by_iso3=roster, region_pack=pack.name,
            min_mentions=MIN_MENTIONS,
            external_powers=pack.external_powers,
            keep_lines=buffer,
        )
        written[pack.name] = len(rows)
        text = buffer.getvalue()
        if not text:
            continue
        with gzip.open(
            artifact_for(out_dir, pack.name, day.year), "at", encoding=ENCODING
        ) as handle:
            handle.write(text)
    return written
