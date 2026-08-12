"""Load SWF 13F flows: EDGAR filings → FLOW edges (build-spec section 5.2).

  python scripts/load_13f.py            # every pack filer, recent quarters
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
    parser.add_argument("pack", nargs="?", default="mena")
    parser.add_argument("--limit", type=int, default=8, help="quarters per filer")
    args = parser.parse_args()

    pack = packs.load(args.pack)
    filers = pack.data["assets"].get("swf_filers", [])
    if not filers:
        sys.exit(f"packs/{pack.name}/assets.yaml declares no swf_filers.")
    us_equity = next(
        (m["id"] for m in pack.markets if m["ticker"] == "^GSPC"), None
    )
    if us_equity is None:
        sys.exit("no US equity market in the pack — FLOW needs its destination.")

    settings = settings_module.load()
    conn = kuzu_store.connect(settings.kuzu_db_path)
    try:
        written = edgar_13f.load_flows(
            conn, filers, market_node_id=us_equity, limit_per_filer=args.limit
        )
        print(f"FLOW edges: {written}")
        violations = kuzu_store.check_provenance(conn)
        if violations:
            sys.exit("PROVENANCE VIOLATIONS:\n" + "\n".join(violations))
        print("provenance: ok")
    finally:
        kuzu_store.close(conn)


if __name__ == "__main__":
    main()
