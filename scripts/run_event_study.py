"""Run the transmission engine and write AFFECTED edges into the graph.

  python scripts/run_event_study.py                     # the phase0 episode
  python scripts/run_event_study.py --event event:mena-2019-abqaiq
  python scripts/run_event_study.py --all              # the whole spine (Phase 1)
  python scripts/run_event_study.py --dry-run          # measure, write nothing

Reads prices from Postgres, computes in core.transmission.event_study, and
writes through core.transmission.effects — the ONE path numbers take from the
panel into the graph. Stop the API first: writing AFFECTED needs the Kuzu write
lock, and Kuzu is single-writer.

Skips are recorded, not dropped: a market that did not exist at event time and
a ticker with no data both land in event_study_runs with a status, so coverage
is queryable instead of being inferred from silence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from core import packs
from core import settings as settings_module
from core.graph import kuzu_store
from core.ingestion import market_data
from core.panel import pg_store
from core.transmission import effects as effects_writer
from core.transmission import event_study

#: How much history to read per market: the estimation window plus its gap and
#: the longest measurement window, with room for weekends and holidays.
_LOOKBACK_DAYS = 400


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", nargs="?", default="mena")
    parser.add_argument("--event", action="append", help="event node_id; repeatable")
    parser.add_argument("--all", action="store_true", help="every event in the spine")
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = parser.parse_args()

    settings = settings_module.load()
    try:
        pack = packs.load(args.pack)
    except packs.PackError as exc:
        sys.exit(str(exc))

    spine = pack.marquee_events
    if args.event:
        wanted = set(args.event)
        chosen = [e for e in spine if e["id"] in wanted]
        missing = wanted - {e["id"] for e in chosen}
        if missing:
            sys.exit(f"no such event(s) in packs/{pack.name}: {', '.join(sorted(missing))}")
    elif args.all:
        chosen = list(spine)
    else:
        chosen = [e for e in spine if e.get("phase0_candidate")]
        if not chosen:
            sys.exit(
                f"packs/{pack.name} marks no phase0_candidate event. Name one with "
                "--event, or run --all."
            )

    all_dates = {e["id"]: dt.date.fromisoformat(str(e["date"])[:10]) for e in spine}

    try:
        panel = pg_store.connect(settings)
    except pg_store.PanelUnavailable as exc:
        sys.exit(str(exc))

    graph = None
    if not args.dry_run:
        try:
            graph = kuzu_store.connect(settings.kuzu_db_path)
        except kuzu_store.GraphUnavailable as exc:
            sys.exit(str(exc))

    market_node_ids = {m["ticker"]: m["id"] for m in pack.markets}
    written = 0
    try:
        for event in chosen:
            event_date = all_dates[event["id"]]
            start = (event_date - dt.timedelta(days=_LOOKBACK_DAYS)).isoformat()
            end = (event_date + dt.timedelta(days=30)).isoformat()
            prices = {
                m["ticker"]: pg_store.series(panel, m["ticker"], start=start, end=end)
                for m in pack.markets
            }

            results, skips = event_study.compute_effects(
                {"node_id": event["id"], "event_time": event["date"]},
                pack.markets,
                prices=prices,
                other_event_dates=all_dates,
            )

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

            pg_store.record_runs(panel, results, skips)
            if graph is not None:
                written += effects_writer.write_effects(
                    graph, results,
                    market_node_ids=market_node_ids,
                    source_id=market_data.SOURCE_YFINANCE,
                )
    finally:
        panel.close()

    if graph is None:
        print("\ndry run — nothing written")
        return
    try:
        print(f"\nAFFECTED edges written: {written}")
        violations = kuzu_store.check_provenance(graph)
        if violations:
            sys.exit("PROVENANCE VIOLATIONS:\n" + "\n".join(violations))
        print("provenance: ok")
    finally:
        kuzu_store.close(graph)


if __name__ == "__main__":
    main()
