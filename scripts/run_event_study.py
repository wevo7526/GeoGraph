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
from typing import Any

from core import packs
from core import settings as settings_module
from core.graph import kuzu_store
from core.ingestion import market_data, shiller
from core.panel import pg_store
from core.transmission import effects as effects_writer
from core.transmission import event_study

#: How much history to read per market, BY THE RESOLUTION its era serves: the
#: estimation window plus its gap and the longest measurement window, with
#: slack. 120 daily sessions fit in 400 days; 60 monthly observations need
#: six years; 10 annual ones need thirteen.
_LOOKBACK_DAYS: dict[str, int] = {"intraday": 400, "day": 400, "month": 2300, "year": 4800}

#: TemporalResolution → the panel frequency column holding that era's rows.
_PANEL_FREQUENCY: dict[str, str] = {
    "intraday": "daily", "day": "daily", "month": "monthly", "year": "annual",
}


def _effect_source(result: event_study.EffectResult) -> str:
    """The Source the panel rows behind this number came from."""
    if result.resolution in ("month", "year"):
        return shiller.SOURCE_SHILLER
    if result.market_ticker in ("DGS2", "DGS10"):
        return market_data.SOURCE_FRED
    return market_data.SOURCE_YFINANCE


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

    # THE GRAPH IS THE EVENT SOURCE, not the pack: the deep tier (COW MIDs)
    # lives only in the graph, and "--all" means the whole archive. A dry run
    # opens read-only; a writing run holds the single-writer lock throughout.
    try:
        graph = kuzu_store.connect(settings.kuzu_db_path, read_only=args.dry_run)
    except kuzu_store.GraphUnavailable as exc:
        sys.exit(str(exc))

    archive = kuzu_store.query(
        graph,
        "MATCH (e:Event) RETURN e.node_id AS id, e.event_time AS date, "
        "e.name AS name ORDER BY e.event_time, e.node_id",
    )
    if not archive:
        kuzu_store.close(graph)
        sys.exit("the graph holds no events — seed first")

    if args.event:
        wanted = set(args.event)
        chosen = [e for e in archive if e["id"] in wanted]
        missing = wanted - {e["id"] for e in chosen}
        if missing:
            kuzu_store.close(graph)
            sys.exit(f"no such event(s) in the graph: {', '.join(sorted(missing))}")
    elif args.all:
        chosen = list(archive)
    else:
        candidates = {e["id"] for e in pack.marquee_events if e.get("phase0_candidate")}
        chosen = [e for e in archive if e["id"] in candidates]
        if not chosen:
            kuzu_store.close(graph)
            sys.exit(
                f"packs/{pack.name} marks no phase0_candidate event. Name one with "
                "--event, or run --all."
            )

    all_dates = {e["id"]: event_study.parse_event_date(e["date"]) for e in archive}

    try:
        panel = pg_store.connect(settings)
    except pg_store.PanelUnavailable as exc:
        kuzu_store.close(graph)
        sys.exit(str(exc))

    market_node_ids = {m["ticker"]: m["id"] for m in pack.markets}
    written = 0
    try:
        for event in chosen:
            event_date = all_dates[event["id"]]
            # Each market reads the panel AT ITS OWN ERA'S FREQUENCY, looking
            # back far enough for that resolution's estimation window — the
            # fidelity gradient applied to the read path. A market with no
            # native frequency at the date is alive-but-dataless; series()
            # for it returns nothing and the engine records the skip.
            prices: dict[str, list[dict[str, Any]]] = {}
            for m in pack.markets:
                try:
                    resolution = event_study.native_resolution(m, event_date)
                except event_study.StudyError:
                    prices[m["ticker"]] = []
                    continue
                start = (
                    event_date - dt.timedelta(days=_LOOKBACK_DAYS[resolution])
                ).isoformat()
                end = (event_date + dt.timedelta(days=400 if resolution == "year" else 60)
                       ).isoformat()
                prices[m["ticker"]] = pg_store.series(
                    panel, m["ticker"], start=start, end=end,
                    frequency=_PANEL_FREQUENCY[resolution],
                )

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
            if not args.dry_run:
                # PROVENANCE FOLLOWS THE PANEL ROWS the number came from: a
                # monthly abnormal return is Shiller's era, a daily yield move
                # is FRED's, everything else is yfinance. Attributing a 1911
                # measurement to a feed founded a century later would be a
                # lie the graph faithfully preserved.
                by_source: dict[str, list[event_study.EffectResult]] = {}
                for result in results:
                    by_source.setdefault(_effect_source(result), []).append(result)
                for source_id, group in by_source.items():
                    written += effects_writer.write_effects(
                        graph, group,
                        market_node_ids=market_node_ids,
                        source_id=source_id,
                    )
    finally:
        panel.close()

    try:
        if args.dry_run:
            print("\ndry run — nothing written")
            return
        print(f"\nAFFECTED edges written: {written}")
        violations = kuzu_store.check_provenance(graph)
        if violations:
            sys.exit("PROVENANCE VIOLATIONS:\n" + "\n".join(violations))
        print("provenance: ok")
    finally:
        kuzu_store.close(graph)


if __name__ == "__main__":
    main()
