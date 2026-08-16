"""Run the transmission engine and write AFFECTED edges into the graph.

  python scripts/run_event_study.py                     # the phase0 episode
  python scripts/run_event_study.py --event event:mena-2019-abqaiq
  python scripts/run_event_study.py --all              # the whole archive
  python scripts/run_event_study.py --spine            # the pack's curated events
  python scripts/run_event_study.py --dry-run          # measure, write nothing

The measurement itself lives in `core/transmission/runner.py`, so the API can
run the SAME engine as a background job on its own connection (the study used
to exist only inside a boot, a slice per deploy, and each slice cost the
container's downtime). This file is the command-line half: it opens the graph,
picks the events, and prints.

Reads prices from Postgres, computes in core.transmission.event_study, and
writes through core.transmission.effects — the ONE path numbers take from the
panel into the graph. Stop the API first: writing AFFECTED needs the Kuzu write
lock, and Kuzu is single-writer PER PROCESS.

Skips are recorded, not dropped: a market that did not exist at event time and
a ticker with no data both land in event_study_runs with a status, so coverage
is queryable instead of being inferred from silence.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from core import packs
from core import settings as settings_module
from core.graph import kuzu_store
from core.panel import pg_store
from core.transmission import event_study, runner


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", nargs="?", default="mena")
    parser.add_argument("--event", action="append", help="event node_id; repeatable")
    parser.add_argument("--all", action="store_true", help="every event in the archive")
    parser.add_argument(
        "--spine", action="store_true",
        help="only the events the pack NAMES (marquee + case study), watermarked "
             "against the graph rather than the panel — cheap, bounded, and the "
             "one selection the surface cannot serve without",
    )
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    parser.add_argument(
        "--refresh", action="store_true",
        help="re-measure events that already have recorded runs. The default "
             "SKIPS them (the watermark): the engine is deterministic, so an "
             "already-measured event re-derives the same numbers — and at a "
             "hundred thousand events, skipping is what lets a deploy boot in "
             "seconds and a cut-off run resume where it stopped.",
    )
    parser.add_argument(
        "--min-gdelt-goldstein", type=float,
        default=runner.DEFAULT_MIN_GDELT_GOLDSTEIN,
        help="materiality bar for GDELT-sourced events under --all",
    )
    args = parser.parse_args()

    settings = settings_module.load()
    try:
        pack = packs.load(args.pack)
    except packs.PackError as exc:
        sys.exit(str(exc))

    # A dry run opens read-only; a writing run holds the lock throughout.
    try:
        graph = kuzu_store.connect(settings.kuzu_db_path, read_only=args.dry_run)
    except kuzu_store.GraphUnavailable as exc:
        sys.exit(str(exc))

    events = runner.archive(graph)
    if not events:
        kuzu_store.close(graph)
        sys.exit("the graph holds no events — seed first")

    curated = runner.curated_event_ids(pack)
    if args.event:
        wanted = set(args.event)
        chosen = [e for e in events if e["id"] in wanted]
        missing = wanted - {e["id"] for e in chosen}
        if missing:
            kuzu_store.close(graph)
            sys.exit(f"no such event(s) in the graph: {', '.join(sorted(missing))}")
    elif args.spine:
        chosen = [e for e in events if e["id"] in curated]
        if not chosen:
            kuzu_store.close(graph)
            sys.exit(f"packs/{pack.name} names no event the graph holds — seed first")
    elif args.all:
        chosen = runner.select_all(
            events, curated, min_gdelt_goldstein=args.min_gdelt_goldstein
        )
        excluded = len(events) - len(chosen)
        if excluded:
            print(
                f"{excluded} GDELT events below the |goldstein| "
                f">= {args.min_gdelt_goldstein} materiality bar are not measured "
                "(they remain in the graph and in Head B's baselines)"
            )
    else:
        candidates = {e["id"] for e in pack.marquee_events if e.get("phase0_candidate")}
        chosen = [e for e in events if e["id"] in candidates]
        if not chosen:
            kuzu_store.close(graph)
            sys.exit(
                f"packs/{pack.name} marks no phase0_candidate event. Name one with "
                "--event, or run --all."
            )

    all_dates = {e["id"]: event_study.parse_event_date(e["date"]) for e in events}

    try:
        panel = pg_store.connect(settings)
    except pg_store.PanelUnavailable as exc:
        kuzu_store.close(graph)
        sys.exit(str(exc))

    if not args.refresh:
        if args.spine:
            # THE GRAPH IS THE WATERMARK for the curated set — see
            # `runner.already_in_the_graph`. Small enough to ask edge by edge,
            # and the only watermark that survives a volume rebuild.
            measured = runner.already_in_the_graph(graph, [e["id"] for e in chosen])
        else:
            # PER-MARKET watermark: skip an event only once it is measured
            # against ALL of THIS pack's markets, so packs stop shadowing each
            # other's measurements.
            measured = pg_store.measured_events(
                panel, [m["ticker"] for m in pack.markets]
            )
        before = len(chosen)
        chosen = [e for e in chosen if e["id"] not in measured]
        if before != len(chosen):
            print(f"{before - len(chosen)} already-measured events skipped "
                  "(the watermark; --refresh re-measures)")
        if not chosen:
            # EXIT BEFORE THE PRELOAD, which reads every market's whole
            # era-appropriate span out of Postgres — most of the cost of a run
            # with nothing to do, and the spine check runs on EVERY boot.
            print("nothing new to measure")
            panel.close()
            kuzu_store.close(graph)
            return

    def _print(event: dict[str, Any], results: list[Any], skips: list[Any]) -> None:
        print(f"\n{event['id']}  {event['date']}  {event['name']}")
        for result in sorted(results, key=lambda r: (r.market_ticker, r.window)):
            flags = "".join((
                " FIRST" if result.first_mover else "",
                " OVERLAP" if result.overlapping else "",
            ))
            print(
                f"  {result.market_ticker:>10} {result.window:<8} "
                f"raw {result.raw_return * 100:+7.2f}%  "
                f"abn {result.abnormal_return * 100:+7.2f}%  "
                f"t {result.t_stat:+6.2f}  p {result.p_value:.4f}{flags}"
            )
        for skip in skips:
            print(f"  {skip.market_ticker:>10} {skip.window:<8} {skip.status}: {skip.reason}")

    try:
        outcome = runner.measure(
            graph, panel, pack, chosen,
            all_dates=all_dates, dry_run=args.dry_run, on_event=_print,
        )
        if args.dry_run:
            print("\ndry run — nothing written")
            return
        print(f"\nAFFECTED edges written: {outcome['edges']}")
        violations = kuzu_store.check_provenance(graph)
        if violations:
            sys.exit("PROVENANCE VIOLATIONS:\n" + "\n".join(violations))
        print("provenance: ok")
    finally:
        # One finally covers BOTH stores on every path — the graph close used
        # to live in a second try that an exception in the loop never reached,
        # leaving the write lock (and Kuzu's 8 TiB reservation) to process exit.
        panel.close()
        kuzu_store.close(graph)


if __name__ == "__main__":
    main()
