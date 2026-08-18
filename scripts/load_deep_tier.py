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
import os
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from core import settings as settings_module
from core.graph import kuzu_store
from core.ingestion import cow

#: Where the official files land. On Railway this is pointed at the volume
#: (GEOGRAPH_RAW_DIR=/data/raw) so a redeploy reuses the downloads instead of
#: re-fetching ~15 MB from COW and Yale on every boot.
_RAW = Path(
    os.getenv("GEOGRAPH_RAW_DIR") or Path(__file__).resolve().parent.parent / "data" / "raw"
)

#: What to fetch and what lands where. The zips carry more than the one file
#: each loader needs; only the named members are extracted.
_FETCH: list[tuple[str, str | tuple[str, str] | None, str]] = [
    # (url, member or (outer-zip-member, inner-member) or None, local filename)
    ("https://correlatesofwar.org/wp-content/uploads/states2016.csv",
     None, "states2016.csv"),
    # NMCv7.zip NESTS its data: the CSV lives inside a second zip inside the
    # first. A local hand-extraction masked this until Railway's fresh fetch
    # hit the truth (KeyError on the flat member name) — the tuple form IS
    # that lesson.
    ("https://correlatesofwar.org/wp-content/uploads/NMCv7.zip",
     ("NMCv7/NMC-v7-supplemental.zip", "NMC-70-wsupplementary.csv"),
     "NMC-70-wsupplementary.csv"),
    ("https://correlatesofwar.org/wp-content/uploads/MID-5-Data-and-Supporting-Materials.zip",
     "MIDA 5.0.csv", "MIDA 5.0.csv"),
    ("https://correlatesofwar.org/wp-content/uploads/MID-5-Data-and-Supporting-Materials.zip",
     "MIDB 5.0.csv", "MIDB 5.0.csv"),
    ("https://correlatesofwar.org/wp-content/uploads/version4.1_csv.zip",
     "version4.1_csv/alliance_v4.1_by_directed.csv", "alliance_v4.1_by_directed.csv"),
    ("http://www.econ.yale.edu/~shiller/data/ie_data.xls",
     None, "ie_data.xls"),
    ("https://correlatesofwar.org/wp-content/uploads/state_year_formatv3.zip",
     "state_year_formatv3.csv", "state_year_formatv3.csv"),
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
            content = payload
        elif isinstance(member, tuple):
            outer_name, inner_name = member
            with (
                zipfile.ZipFile(io.BytesIO(payload)) as outer,
                zipfile.ZipFile(io.BytesIO(outer.read(outer_name))) as inner,
            ):
                content = inner.read(inner_name)
        else:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                # Zip members keep their internal paths; extract flat, and a
                # member the archive renamed FAILS LOUDLY instead of loading
                # a stale local copy forever.
                content = archive.read(member)
        # Write-then-rename: a boot killed mid-write must not leave a partial
        # file that every later boot "finds" and trusts.
        partial = target.with_suffix(target.suffix + ".part")
        partial.write_bytes(content)
        partial.replace(target)
        print(f"  -> {target.name}")


from core.classifier.rescore import rescore_escalation  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-fetch", action="store_true", help="local files only")
    parser.add_argument(
        "--prune-only", action="store_true",
        help="only prune the actors no pack names (and what hangs off them); "
             "the boot runs this on EVERY boot because the deep tier itself is "
             "fingerprint-guarded and a prune that only ran when the inputs "
             "moved left 489 of 754 actors on the volume on 2026-08-16",
    )
    parser.add_argument(
        "--skip-rescore", action="store_true",
        help="load but defer the archive-wide Head B rescore. THE BOOT PASSES "
             "THIS. The rescore folds escalation over every event in time "
             "order, so on a 1.5M-event archive it needs hours — run "
             "unconditionally here it would burn this step's whole timeout on "
             "EVERY deploy, fail, and leave the scores no fresher than before. "
             "The boot runs one rescore of its own, once, after all loading is "
             "complete (scripts/boot.py).",
    )
    args = parser.parse_args()

    settings = settings_module.load()
    if args.prune_only:
        from core import packs as packs_module

        conn = kuzu_store.connect(settings.kuzu_db_path)
        try:
            roster = {
                str(actor["id"])
                for name in packs_module.available()
                for actor in packs_module.load(name).actors
            }
            pruned = cow.prune_off_roster_actors(conn, roster)
            from core import archive as archive_bounds
            trimmed = archive_bounds.drop_events_before(conn)
            print("pruned off-roster: " + (", ".join(
                f"{table} {count}" for table, count in pruned.items() if count) or "nothing"))
            print("trimmed before archive floor: " + (", ".join(
                f"{table} {count}" for table, count in trimmed.items() if count) or "nothing"))
        finally:
            kuzu_store.close(conn)
        return

    if not args.no_fetch:
        fetch_missing()
    for _, _, filename in _FETCH:
        if not (_RAW / filename).exists():
            sys.exit(f"missing {_RAW / filename} — run without --no-fetch")

    settings.kuzu_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = kuzu_store.connect(settings.kuzu_db_path)
    try:
        kuzu_store.apply_schema(conn)
        # THE PACKS DEFINE THE SCOPE. The state-system loader used to invent an
        # Actor for every COW state (754 against a roster union of 75) and the
        # volume still holds them, with the alliances, estimates, metrics and
        # disputes that arrived with them. Prune BEFORE loading, against the
        # union of every pack's roster, so this step leaves the graph in the
        # shape its own loader now promises. Idempotent — zero on a clean graph.
        from core import packs as packs_module

        roster = {
            str(actor["id"])
            for name in packs_module.available()
            for actor in packs_module.load(name).actors
        }
        pruned = cow.prune_off_roster_actors(conn, roster)
        if any(pruned.values()):
            print("pruned off-roster: " + ", ".join(
                f"{table} {count}" for table, count in pruned.items() if count))
        from core import archive as archive_bounds
        trimmed = archive_bounds.drop_events_before(conn)
        if any(trimmed.values()):
            print("trimmed before archive floor: " + ", ".join(
                f"{table} {count}" for table, count in trimmed.items() if count))
        steps: list[tuple[str, Callable[[], cow.LoadResult]]] = [
            ("state system", lambda: cow.load_state_system(conn, _RAW / "states2016.csv")),
            ("cinc clout", lambda: cow.load_cinc(conn, _RAW / "NMC-70-wsupplementary.csv")),
            ("mids", lambda: cow.load_mids(conn, _RAW / "MIDA 5.0.csv",
                                           _RAW / "MIDB 5.0.csv")),
            ("alliances", lambda: cow.load_alliances(
                conn, _RAW / "alliance_v4.1_by_directed.csv")),
            ("igo memberships", lambda: cow.load_igo_memberships(
                conn, _RAW / "state_year_formatv3.csv")),
        ]
        results: list[cow.LoadResult] = []
        for label, step in steps:
            result = step()
            results.append(result)
            print(f"{label}: {result.written} written, {result.dropped} dropped")
            for reason, count in sorted(result.reasons.items()):
                print(f"  - {reason}: {count}")
        written = sum(r.written for r in results)
        if args.skip_rescore:
            print(f"rescore: skipped (--skip-rescore), {written} rows written")
        else:
            for key, value in rescore_escalation(conn).items():
                print(f"{key}: {value}")
        violations = kuzu_store.check_provenance(conn)
        if violations:
            sys.exit("PROVENANCE VIOLATIONS:\n" + "\n".join(violations))
        print("provenance: ok")
    finally:
        kuzu_store.close(conn)

    # The deep PANEL: Shiller monthly into Postgres, when a panel exists.
    # Idempotent upserts — a re-run converges. Without DATABASE_URL the graph
    # half above still stands; the study will record the missing-panel skips.
    if settings.database_url:
        from core.ingestion import shiller
        from core.panel import pg_store

        try:
            panel = pg_store.connect(settings)
        except pg_store.PanelUnavailable as exc:
            print(f"panel unavailable — Shiller not loaded: {exc}")
            return
        try:
            pg_store.apply_schema(panel)
            written = shiller.load_monthly(panel, _RAW / "ie_data.xls")
            print(f"shiller monthly: {written} panel rows")
        finally:
            panel.close()
    else:
        print("DATABASE_URL unset — Shiller monthly not loaded (graph half complete)")


if __name__ == "__main__":
    main()
