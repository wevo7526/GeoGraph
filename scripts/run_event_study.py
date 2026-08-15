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
import bisect
import datetime as dt
import json
import os
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
    parser.add_argument(
        "--refresh", action="store_true",
        help="re-measure events that already have recorded runs. The default "
             "SKIPS them (the watermark): the engine is deterministic, so an "
             "already-measured event re-derives the same numbers — and at a "
             "hundred thousand events, skipping is what lets a deploy boot in "
             "seconds and a cut-off run resume where it stopped.",
    )
    parser.add_argument(
        "--min-gdelt-goldstein", type=float, default=7.0,
        help="materiality bar for GDELT-sourced events under --all: a ten-"
             "mention consultation does not need a measured CAR, and measuring "
             "a hundred thousand of them is attribution soup by construction. "
             "Curated and COW events are ALWAYS measured.",
    )
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
        "e.name AS name, e.goldstein AS goldstein "
        "ORDER BY e.event_time, e.node_id",
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
        chosen = [
            e for e in archive
            if not e["id"].startswith("event:gdelt-")
            or (e["goldstein"] is not None
                and abs(float(e["goldstein"])) >= args.min_gdelt_goldstein)
        ]
        excluded = len(archive) - len(chosen)
        if excluded:
            print(
                f"{excluded} GDELT events below the |goldstein| "
                f">= {args.min_gdelt_goldstein} materiality bar are not measured "
                "(they remain in the graph and in Head B's baselines)"
            )
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
    # Overlap needs only the NEIGHBOURHOOD: another event matters when its
    # date falls inside this event's measurement window (a handful of weeks
    # at most), and scanning all hundred thousand dates per window is a
    # billion comparisons for the same answer.
    timeline = sorted((date, event_id) for event_id, date in all_dates.items())
    timeline_dates = [date for date, _ in timeline]

    def _nearby(event_date: dt.date) -> dict[str, dt.date]:
        lo = bisect.bisect_left(timeline_dates, event_date - dt.timedelta(days=1))
        hi = bisect.bisect_right(timeline_dates, event_date + dt.timedelta(days=45))
        return {event_id: date for date, event_id in timeline[lo:hi]}

    try:
        panel = pg_store.connect(settings)
    except pg_store.PanelUnavailable as exc:
        kuzu_store.close(graph)
        sys.exit(str(exc))

    if not args.refresh:
        # PER-MARKET watermark: skip an event only once it is measured against
        # ALL of THIS pack's markets, so packs stop shadowing each other's
        # measurements (a US–Russia event must reach Tadawul under mena even if
        # china measured it against SSE first).
        measured = pg_store.measured_events(panel, [m["ticker"] for m in pack.markets])
        before = len(chosen)
        chosen = [e for e in chosen if e["id"] not in measured]
        if before != len(chosen):
            print(f"{before - len(chosen)} already-measured events skipped "
                  "(the watermark; --refresh re-measures)")
        if not chosen:
            print("nothing new to measure")

    market_node_ids = {m["ticker"]: m["id"] for m in pack.markets}
    # PRELOAD, ONCE PER (ticker, frequency): the archive is a hundred thousand
    # events after the GDELT backfill, and a per-event panel read is 800k
    # round trips where sixteen bulk reads and an in-memory slice do the same
    # arithmetic. Spans cover the earliest event's estimation window through
    # the latest event's measurement window.
    first_event = min(all_dates.values())
    last_event = max(all_dates.values())
    preloaded: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for m in pack.markets:
        table = json.loads(m["native_frequency"]) if m.get("native_frequency") else {}
        for resolution in set(table.values()) or {"day"}:
            frequency = _PANEL_FREQUENCY[resolution]
            key = (m["ticker"], frequency)
            if key in preloaded:
                continue
            start = (
                first_event - dt.timedelta(days=_LOOKBACK_DAYS[resolution])
            ).isoformat()
            end = (last_event + dt.timedelta(days=400)).isoformat()
            preloaded[key] = pg_store.series(
                panel, m["ticker"], start=start, end=end, frequency=frequency
            )

    preloaded_dates = {
        key: [str(r["obs_date"]) for r in rows] for key, rows in preloaded.items()
    }

    def _slice(ticker: str, frequency: str, start: str, end: str) -> list[dict[str, Any]]:
        rows = preloaded.get((ticker, frequency), [])
        dates = preloaded_dates.get((ticker, frequency), [])
        return rows[bisect.bisect_left(dates, start) : bisect.bisect_right(dates, end)]

    written = 0
    # BATCHED FLUSH — the convergence fix. record_runs (one Postgres commit)
    # and write_effects (one Kuzu merge) used to run PER EVENT: ~130k commits
    # + ~130k merge transactions per region, 400-700s of pure round-trip
    # latency, which is why each metered pack slice timed out without
    # finishing. Accumulate across a chunk and flush once — identical rows,
    # ~500x fewer round trips. A truncated boot loses only the current
    # unflushed chunk (re-measured next boot, idempotent), so the
    # measured-events watermark's resumability is preserved.
    chunk = int(os.getenv("GEOGRAPH_STUDY_CHUNK", "500"))
    pending_results: list[event_study.EffectResult] = []
    pending_skips: list[Any] = []
    pending_by_source: dict[str, list[event_study.EffectResult]] = {}

    def _flush() -> None:
        nonlocal written
        # A dry run writes NOTHING — including the Postgres side. record_runs
        # feeds measured_events, so a dry-run write would watermark previewed
        # events as covered and real runs would never measure them.
        if not args.dry_run:
            # Kuzu first, the watermark LAST: record_runs is what makes an
            # event "measured", so it must be the final durable step. The old
            # order committed the watermark before the merge — a mid-flush
            # graph failure (full volume, lost lock) then stranded up to a
            # chunk of events as measured-with-no-edges, invisibly.
            for source_id, group in pending_by_source.items():
                written += effects_writer.write_effects(
                    graph, group, market_node_ids=market_node_ids, source_id=source_id
                )
            if pending_results or pending_skips:
                pg_store.record_runs(panel, pending_results, pending_skips)
        pending_results.clear()
        pending_skips.clear()
        pending_by_source.clear()

    try:
        for index, event in enumerate(chosen, 1):
            event_date = all_dates[event["id"]]
            # Each market reads AT ITS OWN ERA'S FREQUENCY, looking back far
            # enough for that resolution's estimation window — the fidelity
            # gradient applied to the read path. A market with no native
            # frequency at the date is alive-but-dataless; the empty slice
            # becomes the engine's recorded skip.
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
                prices[m["ticker"]] = _slice(
                    m["ticker"], _PANEL_FREQUENCY[resolution], start, end
                )

            results, skips = event_study.compute_effects(
                {"node_id": event["id"], "event_time": event["date"]},
                pack.markets,
                prices=prices,
                other_event_dates=_nearby(event_date),
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

            pending_results.extend(results)
            pending_skips.extend(skips)
            if not args.dry_run:
                # PROVENANCE FOLLOWS THE PANEL ROWS the number came from: a
                # monthly abnormal return is Shiller's era, a daily yield move
                # is FRED's, everything else is yfinance. Attributing a 1911
                # measurement to a feed founded a century later would be a
                # lie the graph faithfully preserved.
                for result in results:
                    pending_by_source.setdefault(_effect_source(result), []).append(result)
            if index % chunk == 0:
                _flush()
        _flush()  # the final partial chunk

        if args.dry_run:
            print("\ndry run — nothing written")
            return
        print(f"\nAFFECTED edges written: {written}")
        violations = kuzu_store.check_provenance(graph)
        if violations:
            sys.exit("PROVENANCE VIOLATIONS:\n" + "\n".join(violations))
        print("provenance: ok")
    finally:
        # One finally covers BOTH stores on every path — the graph close used
        # to live in a second try that an exception in the loop never reached,
        # leaving the write lock (and Kuzu's 8 TiB reservation) to process
        # exit.
        panel.close()
        kuzu_store.close(graph)


if __name__ == "__main__":
    main()
