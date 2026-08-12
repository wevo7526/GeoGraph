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
            target.write_bytes(payload)
        else:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                # Zip members keep their internal paths; extract flat, and a
                # member the archive renamed FAILS LOUDLY instead of loading
                # a stale local copy forever.
                target.write_bytes(archive.read(member))
        print(f"  -> {target.name}")


from core.classifier.rescore import rescore_escalation  # noqa: E402


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
            ("igo memberships", lambda: cow.load_igo_memberships(
                conn, _RAW / "state_year_formatv3.csv")),
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
