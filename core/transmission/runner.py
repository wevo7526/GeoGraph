"""The transmission engine's DRIVER — event selection, panel preload, the
measure loop, the batched flush — as a library.

Extracted from `scripts/run_event_study.py` on 2026-08-15 so the same code can
run two ways:

  * as the CLI (the script is now a thin argument parser over this), which
    opens its own graph and holds the single-writer lock for the run;
  * as a JOB INSIDE THE RUNNING API (`core/api/jobs.py`), on a second
    connection to the connection the API already holds — because Kuzu is
    one writer per PROCESS, not per connection, and the API is that writer.

The second is the point. The study is a hundred thousand events walked in date
order with a per-event watermark, and it only ever ran inside a boot: a slice
per deploy, each slice costing the whole container's downtime (the volume
mounts once, so a deploy is stop-then-start). Production had measured ~10% of
the wire and reached 2003. Run as a background job behind the open graph, the
same watermark converges the archive continuously and costs a deploy nothing.

NOTHING ABOUT THE MEASUREMENT CHANGED. Same selection, same preload, same
batched flush, same order (Kuzu first, the Postgres watermark last), same
recorded skips. Only the caller and the stopping rule are new: a job passes a
deadline and stops on it, which the watermark already made safe.
"""

from __future__ import annotations

import bisect
import datetime as dt
import json
import os
import time
from typing import Any

from core.graph import kuzu_store
from core.ingestion import market_data, shiller
from core.panel import pg_store
from core.transmission import effects as effects_writer
from core.transmission import event_study

#: How much history to read per market, BY THE RESOLUTION its era serves: the
#: estimation window plus its gap and the longest measurement window, with
#: slack. 120 daily sessions fit in 400 days; 60 monthly observations need
#: six years; 10 annual ones need thirteen.
LOOKBACK_DAYS: dict[str, int] = {"intraday": 400, "day": 400, "month": 2300, "year": 4800}

#: TemporalResolution → the panel frequency column holding that era's rows.
PANEL_FREQUENCY: dict[str, str] = {
    "intraday": "daily", "day": "daily", "month": "monthly", "year": "annual",
}

#: Events per flush. See `_Flusher`: one commit and one merge per event was
#: 400-700s of pure round-trip latency per region.
DEFAULT_CHUNK = int(os.getenv("GEOGRAPH_STUDY_CHUNK", "500"))

#: GDELT materiality bar for `--all`: a ten-mention consultation does not need
#: a measured CAR, and measuring a hundred thousand of them is attribution
#: soup by construction. Curated and COW events are ALWAYS measured.
DEFAULT_MIN_GDELT_GOLDSTEIN = 7.0

ARCHIVE_QUERY = (
    "MATCH (e:Event) RETURN e.node_id AS id, e.event_time AS date, "
    "e.name AS name, e.goldstein AS goldstein "
    "ORDER BY e.event_time, e.node_id"
)

#: The same scan WITHOUT the event name. The name is only ever printed (the
#: CLI's per-event line); nothing in the measurement reads it. It is also the
#: heaviest column: the whole archive costs ~530 MB per million events held
#: as rows plus parsed dates, and the API's convergence loop holds exactly
#: that between ticks. Dropping the one column it never uses is most of the
#: difference, in a process already carrying the corpus and Kuzu's buffers.
ARCHIVE_QUERY_LEAN = (
    "MATCH (e:Event) RETURN e.node_id AS id, e.event_time AS date, "
    "e.goldstein AS goldstein "
    "ORDER BY e.event_time, e.node_id"
)


def archive(graph: Any, *, with_names: bool = True) -> list[dict[str, Any]]:
    """Every event in the graph, in date order — THE event source.

    The deep tier (COW MIDs) lives only in the graph, so the pack is not the
    source: "the archive" means the archive. `with_names=False` is the
    long-lived caller's form (see ARCHIVE_QUERY_LEAN).
    """
    return kuzu_store.query(
        graph, ARCHIVE_QUERY if with_names else ARCHIVE_QUERY_LEAN
    )


def curated_event_ids(pack: Any) -> set[str]:
    """The events the PACK names — its marquee spine and the case study built
    on it. These are the events the surface is written around, so they are the
    ones a measuring run must never leave for last."""
    ids = {str(event["id"]) for event in pack.marquee_events}
    study = pack.case_study
    if study:
        ids |= {str(event_id) for event_id in study.get("events", [])}
    return ids


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
    measured" on 2026-08-15 with 632,586 measured effects in the same graph.
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


def select_all(
    events: list[dict[str, Any]],
    curated: set[str],
    *,
    min_gdelt_goldstein: float = DEFAULT_MIN_GDELT_GOLDSTEIN,
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
        e for e in events
        if not str(e["id"]).startswith("event:gdelt-")
        or (e["goldstein"] is not None
            and abs(float(e["goldstein"])) >= min_gdelt_goldstein)
    ]
    chosen.sort(key=lambda e: (e["id"] not in curated, str(e["date"]), str(e["id"])))
    return chosen


def effect_source(result: event_study.EffectResult) -> str:
    """The Source the panel rows behind this number came from.

    PROVENANCE FOLLOWS THE PANEL ROWS: a monthly abnormal return is Shiller's
    era, a daily yield move is FRED's, everything else is yfinance.
    Attributing a 1911 measurement to a feed founded a century later would be
    a lie the graph would faithfully preserve.
    """
    if result.resolution in ("month", "year"):
        return shiller.SOURCE_SHILLER
    # Every FRED-loaded tenor, not two of three: DGS3MO's daily effects were
    # stamped yfinance for as long as this tuple omitted it (2026-08-15).
    if result.market_ticker in ("DGS2", "DGS3MO", "DGS10"):
        return market_data.SOURCE_FRED
    return market_data.SOURCE_YFINANCE


class _Overlap:
    """Neighbouring event dates, by binary search.

    Overlap needs only the NEIGHBOURHOOD: another event matters when its date
    falls inside this event's measurement window (a handful of weeks at most),
    and scanning all hundred thousand dates per window is a billion
    comparisons for the same answer.
    """

    def __init__(self, all_dates: dict[str, dt.date]) -> None:
        self.timeline = sorted((date, event_id) for event_id, date in all_dates.items())
        self.dates = [date for date, _ in self.timeline]

    def near(self, event_date: dt.date) -> dict[str, dt.date]:
        lo = bisect.bisect_left(self.dates, event_date - dt.timedelta(days=1))
        hi = bisect.bisect_right(self.dates, event_date + dt.timedelta(days=45))
        return {event_id: date for date, event_id in self.timeline[lo:hi]}


def measure(
    graph: Any,
    panel: Any,
    pack: Any,
    chosen: list[dict[str, Any]],
    *,
    all_dates: dict[str, dt.date],
    dry_run: bool = False,
    chunk: int = DEFAULT_CHUNK,
    deadline: float | None = None,
    on_event: Any = None,
    stop_when: Any = None,
) -> dict[str, Any]:
    """Measure `chosen` against `pack`'s markets; write effects and runs.

    `deadline` is a `time.monotonic()` stamp: the loop stops cleanly at the
    next event boundary after it passes, flushing what it has. That is safe
    for exactly the reason a truncated boot always was — the watermark makes
    progress fungible — and it is what lets this run as a background job
    without holding the writer for minutes at a time.

    `stop_when` is the same idea for a resource the clock cannot see: a
    predicate asked once per chunk, which stops the run at the next event
    boundary when it returns True. The API passes its memory guard, because a
    slice with time left can still reach the container's limit — and an OOM
    kill mid-write is what left the graph unopenable on 2026-08-17. Kept as a
    callable so this module owes nothing to `core.api`.
    """
    market_node_ids = {m["ticker"]: m["id"] for m in pack.markets}
    overlap = _Overlap(all_dates)

    # PRELOAD, ONCE PER (ticker, frequency): the archive is a hundred thousand
    # events, and a per-event panel read is 800k round trips where sixteen
    # bulk reads and an in-memory slice do the same arithmetic. SPANNED BY
    # WHAT IS BEING MEASURED, not by the archive: a spine run measures a dozen
    # modern events and has no use for the 1871 tail of the monthly panel.
    first_event = min(all_dates[e["id"]] for e in chosen)
    last_event = max(all_dates[e["id"]] for e in chosen)
    preloaded: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for market in pack.markets:
        table = (
            json.loads(market["native_frequency"])
            if market.get("native_frequency") else {}
        )
        for resolution in set(table.values()) or {"day"}:
            frequency = PANEL_FREQUENCY[resolution]
            key = (market["ticker"], frequency)
            if key in preloaded:
                continue
            start = (
                first_event - dt.timedelta(days=LOOKBACK_DAYS[resolution])
            ).isoformat()
            end = (last_event + dt.timedelta(days=400)).isoformat()
            preloaded[key] = pg_store.series(
                panel, market["ticker"], start=start, end=end, frequency=frequency
            )
    preloaded_dates = {
        key: [str(r["obs_date"]) for r in rows] for key, rows in preloaded.items()
    }

    def _slice(ticker: str, frequency: str, start: str, end: str) -> list[dict[str, Any]]:
        rows = preloaded.get((ticker, frequency), [])
        dates = preloaded_dates.get((ticker, frequency), [])
        return rows[bisect.bisect_left(dates, start) : bisect.bisect_right(dates, end)]

    written = 0
    measured_events = 0
    pending_results: list[event_study.EffectResult] = []
    pending_skips: list[Any] = []
    pending_by_source: dict[str, list[event_study.EffectResult]] = {}

    def _flush() -> None:
        # BATCHED FLUSH — the convergence fix. record_runs (one Postgres
        # commit) and write_effects (one Kuzu merge) used to run PER EVENT:
        # ~130k commits + ~130k merge transactions per region, 400-700s of
        # pure round-trip latency. A truncated run loses only the current
        # unflushed chunk (re-measured next time, idempotent).
        #
        # A dry run writes NOTHING — including the Postgres side. record_runs
        # feeds measured_events, so a dry-run write would watermark previewed
        # events as covered and real runs would never measure them.
        nonlocal written
        if not dry_run:
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

    stopped_early = False
    stopped_for = None
    for index, event in enumerate(chosen, 1):
        if deadline is not None and time.monotonic() > deadline:
            stopped_early = True
            stopped_for = "deadline"
            break
        # A SECOND WAY TO STOP, and the reason it exists is that the first one
        # cannot save the process: a deadline bounds TIME, and what killed the
        # container on 2026-08-17 was MEMORY inside a slice that still had time
        # left. The caller passes a predicate (the job scheduler's memory
        # guard); this loop only knows that when it says stop, the next event
        # boundary is a clean place to do it. Checked once per chunk — the same
        # boundary the flush uses, so a stop never straddles a write.
        if (
            stop_when is not None
            and index > 1
            and index % chunk == 1
            and stop_when()
        ):
            stopped_early = True
            stopped_for = "memory"
            break
        event_date = all_dates[event["id"]]
        # Each market reads AT ITS OWN ERA'S FREQUENCY, looking back far enough
        # for that resolution's estimation window — the fidelity gradient
        # applied to the read path. A market with no native frequency at the
        # date is alive-but-dataless; the empty slice becomes a recorded skip.
        prices: dict[str, list[dict[str, Any]]] = {}
        for market in pack.markets:
            try:
                resolution = event_study.native_resolution(market, event_date)
            except event_study.StudyError:
                prices[market["ticker"]] = []
                continue
            start = (
                event_date - dt.timedelta(days=LOOKBACK_DAYS[resolution])
            ).isoformat()
            end = (
                event_date + dt.timedelta(days=400 if resolution == "year" else 60)
            ).isoformat()
            prices[market["ticker"]] = _slice(
                market["ticker"], PANEL_FREQUENCY[resolution], start, end
            )

        results, skips = event_study.compute_effects(
            {"node_id": event["id"], "event_time": event["date"]},
            pack.markets,
            prices=prices,
            other_event_dates=overlap.near(event_date),
        )
        if on_event is not None:
            on_event(event, results, skips)

        pending_results.extend(results)
        pending_skips.extend(skips)
        if not dry_run:
            for result in results:
                pending_by_source.setdefault(effect_source(result), []).append(result)
        measured_events += 1
        if index % chunk == 0:
            _flush()
    _flush()  # the final partial chunk

    return {
        "events": measured_events,
        "edges": written,
        "stopped_early": stopped_early,
        "stopped_for": stopped_for,
        "remaining": len(chosen) - measured_events,
    }
