"""Drop and recreate the AFFECTED rel table — the last resort, and a safe one.

  python scripts/rebuild_affected.py --check          # count and report only
  python scripts/rebuild_affected.py --rebuild        # drop, recreate, report

WHY THIS IS SAFE, and it is the reason it exists rather than a database restore.
AFFECTED holds MEASUREMENTS, not facts: every edge is a deterministic function
of an Event's date, a Market's price series in Postgres, and the event-study
code. Nothing in it is an observation that would be lost. `event_study_runs` in
the panel records which (event, market, window) triples were measured and what
they returned, so the watermark survives independently of the graph, and the
study job re-derives the whole table at ~2,300 edges/sec once it is empty.

WHEN TO REACH FOR IT. `csr_node_group.cpp KU_UNREACHABLE` on an AFFECTED write,
after `write_edges` — which removes MERGE's adjacency scan — has also failed.
That combination points at the on-disk CSR for a Market node rather than at any
statement: the assertion is the `default:` arm of `CSRNodeGroup::scan()`, and a
node group left inconsistent by a process killed mid-write is the one thing
that reaches it that no rewrite of the query can avoid.

720,000 edges onto twenty Market nodes across twelve checkpointed sessions ran
clean on a FRESH database, at production's exact concentration — so the shape
is not the cause and a rebuild is not a workaround for a design problem. It is
a repair.

STOP THE API FIRST. Kuzu is single-writer per process, and this holds the lock
for the whole drop.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import settings as settings_module  # noqa: E402
from core.graph import kuzu_store  # noqa: E402
from core.ontology import kuzu_schema as ontology  # noqa: E402


def counts(conn: Any) -> dict[str, Any]:
    """Edges in total, and per market — the per-market number is the one that
    matters, because concentration is what makes a group large."""
    total = kuzu_store.query(
        conn, "MATCH ()-[r:AFFECTED]->() RETURN count(r) AS n")[0]["n"]
    per_market = kuzu_store.query(
        conn,
        "MATCH ()-[r:AFFECTED]->(m:Market) "
        "RETURN m.ticker AS ticker, count(r) AS n ORDER BY n DESC",
    )
    return {"total": int(total),
            "per_market": {str(r["ticker"]): int(r["n"]) for r in per_market}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report only")
    parser.add_argument("--rebuild", action="store_true",
                        help="DROP the table and recreate it empty")
    args = parser.parse_args()
    if not (args.check or args.rebuild):
        parser.error("pass --check or --rebuild")

    settings = settings_module.load()
    try:
        conn = kuzu_store.connect(settings.kuzu_db_path, read_only=args.check)
    except kuzu_store.GraphUnavailable as exc:
        sys.exit(str(exc))

    try:
        before = counts(conn)
        print(f"AFFECTED holds {before['total']:,} edges across "
              f"{len(before['per_market'])} markets")
        for ticker, n in list(before["per_market"].items())[:10]:
            print(f"  {ticker:>12}  {n:,}")
        if not args.rebuild:
            return

        spec = ontology.edges()["AFFECTED"]
        print("\ndropping AFFECTED — the measurements are re-derivable and "
              "event_study_runs keeps the watermark")
        with kuzu_store.ACCESS.write():
            conn.execute("DROP REL TABLE AFFECTED")
        # Recreate from the ONTOLOGY's own DDL, never a hand-written copy:
        # the LinkML file is the source of truth for this table's shape.
        with kuzu_store.ACCESS.write():
            conn.execute(spec.ddl())
        after = counts(conn)
        print(f"AFFECTED now holds {after['total']:,} edges")
        print(
            "\nThe study job refills it. Its watermark is per pack and lives "
            "in Postgres (`event_study_runs`), so it will SKIP everything it "
            "already measured and write nothing — run\n"
            "  python scripts/run_event_study.py <pack> --all --refresh\n"
            "with the API stopped, or clear the panel's runs for these "
            "markets, to make it re-measure."
        )
    finally:
        kuzu_store.close(conn)


if __name__ == "__main__":
    main()
