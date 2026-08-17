"""GDELT 2.0's 15-minute stream — the wire, live.

THE ARCHIVE READS 1.0, WHICH PUBLISHES A DAY IN ARREARS. That single fact is
why nothing here was ever tradeable: by the time an event reached the
platform, session 0 — the session whose return CONTAINS the event's impact —
had closed. GDELT 2.0 publishes every fifteen minutes, so an event can be
scored inside the session it moves.

WHAT THIS IS NOT. Fifteen minutes is slow against a headline; a news algorithm
is in the book before this file exists. The edge is not latency, it is the
READING: this archive knows the pair's own running baseline, so it can say
that an event is seven points from what these two normally do, which a feed
that scores in milliseconds cannot. Speed gets you the headline; the baseline
tells you whether the headline is unusual.
"""

from __future__ import annotations

import datetime as dt
import io
import urllib.request
import zipfile
from typing import Any

from core.classifier import escalation
from core.ingestion import gdelt

LATEST = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"

# One process may serve three regional lenses. Cache the newest compressed
# export once, then run the cheap roster filter per pack. The previous path
# downloaded the same 15-minute file once per region, which was both needless
# load on GDELT and a poor failure mode during a page refresh.
_FILE_CACHE: dict[str, Any] = {"url": None, "lines": None, "fetched_at": None}


def latest_export_url(*, timeout: int = 60) -> str | None:
    """The newest 15-minute export file, from GDELT's own pointer."""
    with urllib.request.urlopen(LATEST, timeout=timeout) as response:  # noqa: S310
        for line in response.read().decode("utf-8", "replace").splitlines():
            parts = line.split()
            if parts and parts[-1].endswith(".export.CSV.zip"):
                return str(parts[-1])
    return None


def fetch_lines(url: str, *, timeout: int = 90) -> list[str]:
    """One export file's rows. ~1,300 rows, ~85 KB zipped — cheap enough to
    poll, which is the whole point."""
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        member = archive.namelist()[0]
        return archive.read(member).decode("latin-1", "replace").splitlines(keepends=True)


def clear_cache() -> None:
    """Clear the shared export cache (tests and an explicit refresh use this)."""
    _FILE_CACHE.update({"url": None, "lines": None, "fetched_at": None})


def latest_file() -> tuple[str | None, list[str], str | None]:
    """Fetch the newest export once and return ``(url, lines, fetched_at)``."""
    url = latest_export_url()
    if not url:
        return None, [], None
    if _FILE_CACHE["url"] == url and _FILE_CACHE["lines"] is not None:
        return url, _FILE_CACHE["lines"], _FILE_CACHE["fetched_at"]
    lines = fetch_lines(url)
    fetched_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    _FILE_CACHE.update({"url": url, "lines": lines, "fetched_at": fetched_at})
    return url, lines, fetched_at


def stamp_of(url: str) -> str:
    """The file's own timestamp, which is when GDELT published it."""
    tail = url.rsplit("/", 1)[-1]
    return tail.split(".", 1)[0]


def available_at(url: str) -> str:
    """The export filename's publication stamp as an ISO UTC value."""
    stamp = stamp_of(url)
    return gdelt._iso_timestamp(stamp) or stamp


def poll(pack: Any, roster: dict[str, Any], *, min_mentions: int = 1) -> dict[str, Any]:
    """Fetch the newest file and shape whatever concerns this roster.

    `min_mentions` is 1, not the archive's 10, and that is deliberate: a
    fifteen-minute window is too early for a story to have been picked up ten
    times. The mention count rides on the row so a reader can weigh it, rather
    than the event being withheld until it is no longer news.
    """
    url, lines, fetched_at = latest_file()
    if not url:
        return {"skipped": "GDELT published no pointer", "rows": []}
    rows, _edges, result = gdelt.parse_lines(
        lines,
        actors_by_iso3=roster,
        region_pack=pack.name,
        min_mentions=min_mentions,
        external_powers=pack.external_powers,
        layout=gdelt.V2,
    )
    for row in rows:
        # The parser deliberately has no pack-specific graph knowledge. The
        # live contract does: names and canonical ids make the event useful to
        # the wire without asking the graph to open during a write job.
        initiator = roster.get(str(row.get("initiator_iso3") or ""), {})
        target = roster.get(str(row.get("target_iso3") or ""), {})
        if initiator.get("node_id") and target.get("node_id"):
            row["initiator_id"] = initiator["node_id"]
            row["target_id"] = target["node_id"]
            row["dyad_id"] = escalation.dyad_id(
                str(initiator["node_id"]), str(target["node_id"])
            )
        row["available_at"] = row.get("available_at") or available_at(url)
    return {
        "url": url,
        "published": stamp_of(url),
        "fetched_at": fetched_at,
        "scanned": len(lines),
        "kept": len(rows),
        "dropped": dict(result.drops) if hasattr(result, "drops") else {},
        "rows": rows,
    }
