#!/usr/bin/env python3
"""Load the GDELT wire corpus into Postgres from the artifacts in the image.

    python scripts/load_wire.py               # every pack
    python scripts/load_wire.py mena          # one lens
    python scripts/load_wire.py --rescore     # fold Head B after loading

THE POINT OF THIS SCRIPT IS THAT IT IS FAST. The same rows merged into Kuzu ran
at ~145 events/sec and slowed as the graph grew, which put hours of work in
front of a health check because Kuzu's single writer meant the API could not
bind until it finished. COPY streams them instead, so the corpus that took a
four-hour outage to half-load takes minutes and can run while the site serves —
Postgres has no single-writer lock to hold the API hostage with.

IT IS A MATERIALISATION, NOT A SOURCE. `core.wire.corpus` is the corpus — a
pure function of the artifacts in git through the shared parser and Head B —
and this script writes what that module produces, row for row. Serving does
not depend on this table existing (the API warms from the artifacts
directly); it exists for SQL consumers and for anything that should not pay
a parse per process.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from core import packs  # noqa: E402
from core import settings as settings_module  # noqa: E402
from core.ingestion import gdelt  # noqa: E402
from core.wire import corpus  # noqa: E402
from core.wire import store as wire_store  # noqa: E402


def load_pack(conn: Any, name: str) -> dict[str, Any]:
    """Every artifact for one lens, through the SAME parse serving uses."""
    artifacts = corpus.artifacts_for(name)
    if not artifacts:
        print(f"{name}: no derived artifact in the image — nothing to load")
        return {"pack": name, "written": 0, "artifacts": 0}

    pack = packs.load(name)
    written = dropped = 0
    started = time.monotonic()
    for artifact in artifacts:
        rows, result = corpus.parse_artifact(pack, artifact)
        wire_store.copy_events(conn, rows)
        written += len(rows)
        dropped += result.dropped
        print(f"  {artifact.name}: {len(rows):,}", flush=True)
    elapsed = time.monotonic() - started
    rate = written / elapsed if elapsed > 0 else 0.0
    print(f"{name}: {written:,} events in {elapsed:.1f}s ({rate:,.0f}/sec), "
          f"{dropped:,} filtered")
    return {"pack": name, "written": written, "dropped": dropped,
            "seconds": round(elapsed, 1), "artifacts": len(artifacts)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", nargs="?", default="all")
    parser.add_argument("--rescore", action="store_true",
                        help="fold Head B's escalation after loading")
    parser.add_argument("--rescore-only", action="store_true",
                        help="skip loading; only fold escalation")
    args = parser.parse_args()

    settings = settings_module.load()
    conn = wire_store.connect(settings)
    try:
        wire_store.apply_schema(conn)
        # Sources BEFORE the events that cite them — the provenance ordering,
        # and here it is a foreign key that enforces it rather than a validator.
        wire_store.upsert_sources(conn, [gdelt._SOURCE_META])

        if not args.rescore_only:
            names = corpus.installed() if args.pack == "all" else [args.pack]
            for name in names:
                load_pack(conn, name)

        if args.rescore or args.rescore_only:
            outstanding = wire_store.unscored(conn)
            print(f"rescore: {outstanding:,} events unscored, folding Head B")
            started = time.monotonic()
            touched = wire_store.rescore(conn)
            print(f"rescore: {touched:,} events in "
                  f"{time.monotonic() - started:.1f}s")

        print(f"corpus: {wire_store.counts(conn)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
