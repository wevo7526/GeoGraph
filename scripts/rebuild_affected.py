"""Repair the AFFECTED rel table — probe it, and if it cannot be written,
drop it and re-project it from the panel.

  python scripts/rebuild_affected.py --check     # count and report only
  python scripts/rebuild_affected.py --probe     # can AFFECTED be written? (exit 0/1)
  python scripts/rebuild_affected.py --rebuild   # DROP + recreate, then --refill
  python scripts/rebuild_affected.py --refill    # project event_study_runs → AFFECTED
  python scripts/rebuild_affected.py --repair    # probe; rebuild+refill only if it fails

WHY THIS IS SAFE, and it is the reason it exists rather than a database restore.
AFFECTED holds MEASUREMENTS, not facts: every edge is a deterministic function
of an Event's date, a Market's price series in Postgres, and the event-study
code — AND `event_study_runs` in the panel already holds every computed
effect's numbers, keyed as the edge is. So the table can be re-projected from
the panel in minutes (`core/transmission/rebuild.py`) instead of re-measured
over a day and a half. Nothing on it is an observation that would be lost.

WHEN TO REACH FOR IT. A segfault (SIGSEGV, no traceback) on an AFFECTED write
while every other writer and every AFFECTED READ works — production on
2026-08-16, after `write_edges` (which removes MERGE's adjacency scan) had been
writing clean for hours and a kill mid-write intervened. That combination points
at the on-disk storage of one rel table, and no rewrite of the statement can
avoid it. The probe is the actual failing operation, run in THIS process: if it
dies, the exit is a signal and the caller (the boot's one-shot repair step)
knows; if it returns 0, the table is fine and nothing is dropped.

STOP THE API FIRST. Kuzu is single-writer per process; the boot runs this while
the API holds no graph connection.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import packs  # noqa: E402
from core import settings as settings_module  # noqa: E402
from core.graph import kuzu_store  # noqa: E402
from core.transmission import rebuild  # noqa: E402

#: The refill's resume marker, beside the graph — the same volume the table
#: lives on, so a rebuilt volume loses both together.
MARKER_NAME = ".affected-refill.json"


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


def probe(conn: Any) -> dict[str, Any]:
    """Can AFFECTED take a write? THE ACTUAL FAILING OPERATION, not a proxy.

    Re-writes the edges of a few events that already carry them (the SET path
    of `write_edges`) and then writes and removes one fresh edge (the CREATE
    path — the one the study takes almost always). Both go through
    `write_effects`, the one writer. If the storage is damaged this process
    dies here with a signal, which is the answer.
    """
    from core.transmission import effects as effects_writer
    from core.transmission import event_study

    sample = kuzu_store.query(
        conn,
        "MATCH (e:Event)-[a:AFFECTED]->(m:Market) "
        "RETURN e.node_id AS event_id, m.node_id AS market_id, m.ticker AS ticker, "
        "a.window AS window, a.resolution AS resolution, a.raw_return AS raw_return, "
        "a.expected_return AS expected_return, a.abnormal_return AS abnormal_return, "
        "a.t_stat AS t_stat, a.p_value AS p_value, a.first_mover AS first_mover, "
        "a.overlapping AS overlapping, a.method AS method, a.source_id AS source_id "
        "LIMIT 25",
    )
    def _num(value: Any) -> float:
        return float("nan") if value is None else float(value)

    rewritten = 0
    for row in sample:
        result = event_study.EffectResult(
            event_node_id=str(row["event_id"]), market_ticker=str(row["ticker"]),
            window=str(row["window"]), resolution=str(row["resolution"]),
            raw_return=_num(row["raw_return"]), expected_return=_num(row["expected_return"]),
            abnormal_return=_num(row["abnormal_return"]), t_stat=_num(row["t_stat"]),
            p_value=_num(row["p_value"]),
            first_mover=bool(row["first_mover"]), overlapping=bool(row["overlapping"]),
            method=str(row["method"]),
        )
        rewritten += effects_writer.write_effects(
            conn, [result], market_node_ids={result.market_ticker: str(row["market_id"])},
            source_id=str(row["source_id"]),
        )

    # The CREATE path: one real event, one real market, a window that event
    # does not carry for that market — written, counted, then removed so the
    # probe leaves the table exactly as it found it.
    created = 0
    fresh = kuzu_store.query(
        conn,
        "MATCH (e:Event), (m:Market) WHERE NOT EXISTS { MATCH (e)-[:AFFECTED]->(m) } "
        "RETURN e.node_id AS event_id, m.node_id AS market_id, m.ticker AS ticker LIMIT 1",
    )
    if fresh and sample:
        row = fresh[0]
        source_id = str(sample[0]["source_id"])
        result = event_study.EffectResult(
            event_node_id=str(row["event_id"]), market_ticker=str(row["ticker"]),
            window="car_0_1", resolution="day", raw_return=0.0, expected_return=0.0,
            abnormal_return=0.0, t_stat=0.0, p_value=1.0, first_mover=False,
            overlapping=False, method="probe — removed immediately",
        )
        created = effects_writer.write_effects(
            conn, [result], market_node_ids={result.market_ticker: str(row["market_id"])},
            source_id=source_id,
        )
        kuzu_store.delete_edges(
            conn, "AFFECTED",
            [{"src": result.event_node_id, "dst": str(row["market_id"]), "window": "car_0_1"}],
        )
    return {"rewritten": rewritten, "created_then_removed": created}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report only")
    parser.add_argument("--probe", action="store_true", help="try a write; exit 1 if it fails")
    parser.add_argument("--rebuild", action="store_true",
                        help="DROP the table, recreate it empty, then refill")
    parser.add_argument("--refill", action="store_true",
                        help="project event_study_runs onto AFFECTED (resumable)")
    parser.add_argument("--repair", action="store_true",
                        help="probe; if the probe fails, rebuild + refill")
    parser.add_argument("--budget-seconds", type=float, default=None,
                        help="stop the refill cleanly at a chunk boundary after this long")
    args = parser.parse_args()
    if not (args.check or args.probe or args.rebuild or args.refill or args.repair):
        parser.error("pass one of --check --probe --rebuild --refill --repair")

    settings = settings_module.load()
    try:
        conn = kuzu_store.connect(settings.kuzu_db_path, read_only=args.check)
    except kuzu_store.GraphUnavailable as exc:
        sys.exit(str(exc))

    try:
        before = counts(conn)
        print(f"AFFECTED holds {before['total']:,} edges across "
              f"{len(before['per_market'])} markets", flush=True)
        for ticker, n in list(before["per_market"].items())[:10]:
            print(f"  {ticker:>12}  {n:,}", flush=True)
        if args.check:
            return

        needs_rebuild = args.rebuild
        if args.probe or args.repair:
            print("probe: writing through the one door…", flush=True)
            # A crash here is the answer: the process dies with a signal and
            # the caller reads the returncode. A clean return means the table
            # takes writes.
            outcome = probe(conn)
            print(f"probe: ok — {outcome}", flush=True)
            if args.probe:
                return
            # --repair with a clean probe: nothing to rebuild.
            if not args.rebuild:
                print("repair: the table takes writes; nothing dropped", flush=True)
                return

        if needs_rebuild:
            print("\ndropping AFFECTED — the measurements are re-derivable and "
                  "event_study_runs keeps them", flush=True)
            kuzu_store.recreate_edge_table(conn, "AFFECTED")
            rebuild.Marker(settings.kuzu_db_path.with_name(MARKER_NAME)).clear()
            print(f"AFFECTED now holds {counts(conn)['total']:,} edges", flush=True)

        if needs_rebuild or args.refill:
            _refill(conn, settings, budget=args.budget_seconds)
    finally:
        kuzu_store.close(conn)


def _refill(conn: Any, settings: Any, *, budget: float | None) -> None:
    from core.panel import pg_store

    try:
        panel = pg_store.connect(settings)
    except pg_store.PanelUnavailable as exc:
        sys.exit(str(exc))
    try:
        started = time.monotonic()
        rows = rebuild.panel_effect_rows(panel)
    finally:
        panel.close()
    summary = rebuild.summarize(rows)
    print(f"panel remembers {summary['rows']:,} measured rows over "
          f"{summary['events']:,} events ({time.monotonic() - started:.1f}s to read)",
          flush=True)
    dates = rebuild.event_dates(conn)
    loaded = [packs.load(name) for name in packs.available()]
    marker = rebuild.Marker(settings.kuzu_db_path.with_name(MARKER_NAME))
    outcome = rebuild.refill(
        conn, rows, loaded, dates, marker=marker,
        deadline=(time.monotonic() + budget) if budget else None,
        log=lambda line: print(f"refill: {line}", flush=True),
    )
    print(f"refill: {outcome}", flush=True)
    if outcome["complete"]:
        marker.clear()
        after = counts(conn)
        print(f"AFFECTED now holds {after['total']:,} edges "
              f"(panel remembers {summary['rows']:,})", flush=True)
        violations = kuzu_store.check_provenance(conn)
        if violations:
            sys.exit("PROVENANCE VIOLATIONS:\n" + "\n".join(violations))
        print("provenance: ok", flush=True)
    else:
        print("refill: budget spent — run again to resume from the marker", flush=True)


if __name__ == "__main__":
    main()
