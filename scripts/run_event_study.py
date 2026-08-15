"""Run the transmission engine and write AFFECTED edges into the graph.

  python scripts/run_event_study.py                     # the phase0 episode
  python scripts/run_event_study.py --event event:mena-2019-abqaiq
  python scripts/run_event_study.py --all              # the whole archive (Phase 1)
  python scripts/run_event_study.py --spine            # the pack's curated events
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


def curated_event_ids(pack: packs.Pack) -> set[str]:
    """The events the PACK names — its marquee spine and the case study built
    on it. These are the events the surface is written around, so they are the
    ones a measuring run must never leave for last."""
    ids = {str(event["id"]) for event in pack.marquee_events}
    study = pack.case_study
    if study:
        ids |= {str(event_id) for event_id in study.get("events", [])}
    return ids


def select_all(
    archive: list[dict[str, Any]], curated: set[str], *, min_gdelt_goldstein: float
) -> list[dict[str, Any]]:
    """`--all`'s event list: the archive above the materiality bar, CURATED
    FIRST and then in date order.

    The archive arrives ordered by event_time, so a run that exhausts its
    budget measures 1905 forward and stops — and every event a surface is
    written around (the case studies, the marquee spine, this decade) sits at
    the far end of that walk. Production had measured 632,586 effects and had
    reached 2003; the front page's own episodes were the last thing it would
    ever get to. The watermark is unchanged and so is the total work; what
    changes is WHICH events a truncated pass covers.
    """
    chosen = [
        e for e in archive
        if not str(e["id"]).startswith("event:gdelt-")
        or (e["goldstein"] is not None
            and abs(float(e["goldstein"])) >= min_gdelt_goldstein)
    ]
    chosen.sort(key=lambda e: (e["id"] not in curated, str(e["date"]), str(e["id"])))
    return chosen


def already_in_the_graph(graph: Any, event_ids: list[str]) -> set[str]:
    """Which of these events already carry an AFFECTED edge — the GRAPH's own
    watermark, for the small curated set.

    The Postgres working set (`event_study_runs`) is the watermark everywhere
    else, and it is the right one at archive scale. It is the WRONG one after
    a graph rebuild: the two stores fail independently, so a rebuilt volume
    (GEOGRAPH_RESET_GRAPH) starts with no AFFECTED edges while Postgres still
    remembers every attempt — and the engine then skips, forever, exactly the
    events it has already measured once. That is how the twelve-day war, the
    fourth strait crisis and the february rupture all served "not yet
    measured" on 2026-08-15 with 632,586 measured effects sitting in the same
    graph.
    """
    measured: set[str] = set()
    for event_id in event_ids:
        rows = kuzu_store.query(
            graph,
            "MATCH (e:Event {node_id: $id})-[a:AFFECTED]->(:Market) "
            "RETURN count(a) AS n",
            {"id": event_id},
        )
        if rows and int(rows[0]["n"] or 0) > 0:
            measured.add(event_id)
    return measured


def _effect_source(result: event_study.EffectResult) -> str:
    """The Source the panel rows behind this number came from."""
    if result.resolution in ("month", "year"):
        return shiller.SOURCE_SHILLER
    # Every FRED-loaded tenor, not two of three: DGS3MO's daily effects were
    # stamped yfinance for as long as this tuple omitted it (2026-08-15).
    if result.market_ticker in ("DGS2", "DGS3MO", "DGS10"):
        return market_data.SOURCE_FRED
    return market_data.SOURCE_YFINANCE


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

    curated = curated_event_ids(pack)
    if args.event:
        wanted = set(args.event)
        chosen = [e for e in archive if e["id"] in wanted]
        missing = wanted - {e["id"] for e in chosen}
        if missing:
            kuzu_store.close(graph)
            sys.exit(f"no such event(s) in the graph: {', '.join(sorted(missing))}")
    elif args.spine:
        chosen = [e for e in archive if e["id"] in curated]
        if not chosen:
            kuzu_store.close(graph)
            sys.exit(f"packs/{pack.name} names no event the graph holds — seed first")
    elif args.all:
        chosen = select_all(
            archive, curated, min_gdelt_goldstein=args.min_gdelt_goldstein
        )
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
        if args.spine:
            # THE GRAPH IS THE WATERMARK for the curated set — see
            # `already_in_the_graph`. Small enough to ask edge by edge, and it
            # is the only watermark that survives a volume rebuild.
            measured = already_in_the_graph(graph, [e["id"] for e in chosen])
        else:
            # PER-MARKET watermark: skip an event only once it is measured
            # against ALL of THIS pack's markets, so packs stop shadowing each
            # other's measurements (a US–Russia event must reach Tadawul under
            # mena even if china measured it against SSE first).
            measured = pg_store.measured_events(
                panel, [m["ticker"] for m in pack.markets]
            )
        before = len(chosen)
        chosen = [e for e in chosen if e["id"] not in measured]
        if before != len(chosen):
            print(f"{before - len(chosen)} already-measured events skipped "
                  "(the watermark; --refresh re-measures)")
        if not chosen:
            # EXIT BEFORE THE PRELOAD. The preload below reads every market's
            # whole era-appropriate span out of Postgres, which is most of the
            # cost of a run with nothing to do — and the spine check runs on
            # EVERY boot, so "nothing to do" has to be nearly free.
            print("nothing new to measure")
            panel.close()
            kuzu_store.close(graph)
            return

    market_node_ids = {m["ticker"]: m["id"] for m in pack.markets}
    # PRELOAD, ONCE PER (ticker, frequency): the archive is a hundred thousand
    # events after the GDELT backfill, and a per-event panel read is 800k
    # round trips where sixteen bulk reads and an in-memory slice do the same
    # arithmetic. Spans cover the earliest event's estimation window through
    # the latest event's measurement window.
    # SPANNED BY WHAT IS BEING MEASURED, not by the archive: a spine run
    # measures a dozen modern events and has no use for the 1871 tail of the
    # monthly panel. (`_nearby` still reads every date — overlap is a property
    # of the whole archive, not of the selection.)
    first_event = min(all_dates[e["id"]] for e in chosen)
    last_event = max(all_dates[e["id"]] for e in chosen)
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
