"""Load SWF 13F flows: EDGAR filings → FLOW edges (build-spec section 5.2).

  python scripts/load_13f.py            # every pack's filers, recent quarters
  python scripts/load_13f.py eurasia    # one pack's filers
  python scripts/load_13f.py --limit 4  # fewer quarters per filer

Network against SEC EDGAR (no credentials; identified User-Agent, rate
limited). Stop the API first: writing FLOW needs the Kuzu write lock.
"""

from __future__ import annotations

import argparse
import sys

from core import packs
from core import settings as settings_module
from core.graph import kuzu_store
from core.ingestion import edgar_13f


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # No pack means EVERY pack, matching the docstring the boot relies on: the
    # boot invokes this script argless, and a mena default silently dropped
    # eurasia's declared filers (Norges Bank) from every production graph.
    parser.add_argument("pack", nargs="?", default=None)
    parser.add_argument("--limit", type=int, default=8, help="quarters per filer")
    args = parser.parse_args()

    names = [args.pack] if args.pack else packs.available()
    settings = settings_module.load()
    conn = kuzu_store.connect(settings.kuzu_db_path)
    try:
        total = 0
        for name in names:
            pack = packs.load(name)
            filers = pack.data["assets"].get("swf_filers", [])
            if not filers:
                # A pack with no filers is a skip when sweeping every pack,
                # and an error when the caller named it explicitly.
                if args.pack:
                    sys.exit(f"packs/{pack.name}/assets.yaml declares no swf_filers.")
                print(f"{pack.name}: no swf_filers — skipped")
                continue
            us_equity = next(
                (m["id"] for m in pack.markets if m["ticker"] == "^GSPC"), None
            )
            if us_equity is None:
                sys.exit(
                    f"packs/{pack.name}: no US equity market — FLOW needs its destination."
                )
            written = edgar_13f.load_flows(
                conn, filers, market_node_id=us_equity, limit_per_filer=args.limit
            )
            print(f"{pack.name}: FLOW edges: {written}")
            total += written
        print(f"FLOW edges: {total}")
        violations = kuzu_store.check_provenance(conn)
        if violations:
            sys.exit("PROVENANCE VIOLATIONS:\n" + "\n".join(violations))
        print("provenance: ok")
    finally:
        kuzu_store.close(conn)


if __name__ == "__main__":
    main()
