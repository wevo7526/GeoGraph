"""Rebuild AFFECTED from the panel's own record of what was measured.

WHY THIS CAN EXIST AT ALL. `event_study_runs` in Postgres is not only the
study's watermark: `pg_store.record_runs` writes every computed effect's
NUMBERS there — raw, expected and abnormal return, t, p, the method line, the
resolution — keyed exactly as the graph's edge is (event, market, window). So
the AFFECTED rel table is a PROJECTION of the panel: everything on an edge is
either in that row, in the pack (the market node and its calendar), or on the
Event node (its date). Nothing on it is an observation that would be lost if
the table were dropped.

WHY IT IS NEEDED. On 2026-08-16 every AFFECTED write in production died in
Kuzu's C++ layer — SIGSEGV, no Python evidence, in the API's process and in a
child alike — while every other writer (wire events, Head B's SETs and
OF_DYAD, seeds, the deep tier) wrote clean and AFFECTED itself READ clean
(`/api/stats` counted 1,051,722 edges; the effects endpoints served them). A
kill mid-write earlier that day is the only thing that could have left ONE rel
table's storage in a state a fresh write cannot enter. Dropping the table and
re-measuring a million events would take the study a day and a half; dropping
it and re-projecting the panel takes minutes.

WHAT IS RE-DERIVED RATHER THAN READ. Two edge fields are not stored in the
panel: `first_mover` (a property of the SET of markets a pack measures
together — resolved through `trading_calendar.first_movers` over the pack's
markets alive at the event's date, exactly as `event_study.compute_effects`
resolves it) and `source_id` (`runner.effect_source`: Shiller for the monthly
and annual eras, FRED for its tenors, yfinance otherwise — the same rule the
study applies as it writes). Everything else is copied.

THE WRITE PATH IS THE ONE WRITE PATH. Rows go through
`transmission.effects.write_effects` — the only writer of AFFECTED — so the
provenance validation, the key_slots identity and the `write_edges` statement
(never MERGE against a Market's adjacency list) are the same as the study's.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Any

from core.transmission import calendar as trading_calendar
from core.transmission import effects as effects_writer
from core.transmission import event_study, runner

#: The statuses that mean "a number was measured". A skip has no edge.
MEASURED_STATUSES = ("computed", "overlapping")

#: Events per write chunk. Each event carries a handful of edges; a thousand
#: events is a few thousand rows, which `write_edges` batches on its own.
DEFAULT_CHUNK_EVENTS = 1000


def panel_effect_rows(panel: Any, *, after: str | None = None) -> list[dict[str, Any]]:
    """Every measured (event, market, window) row the panel remembers — or,
    with `after`, only those past a resume marker's event id, so a job that
    refills in slices does not re-read a million rows a tick.

    One read, materialised: a million rows is ~200 MB of Python dicts, which
    is what a repair child has to spare and a request thread does not — this
    is never called from one.
    """
    with panel.cursor() as cur:
        cur.execute(
            "SELECT event_node_id, market_ticker, effect_window, resolution, "
            "status, raw_return, expected_return, abnormal_return, t_stat, "
            "p_value, method FROM event_study_runs "
            "WHERE status = ANY(%s) AND event_node_id > %s ORDER BY event_node_id",
            (list(MEASURED_STATUSES), after or ""),
        )
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def event_dates(graph: Any) -> dict[str, dt.date]:
    """Every event's date, from the graph — the one field an edge needs that
    lives on neither the panel row nor the pack."""
    return {
        str(e["id"]): event_study.parse_event_date(e["date"])
        for e in runner.archive(graph, with_names=False)
    }


def _num(value: Any) -> float:
    """A stored NULL is 'not a measurement', which `write_effects` spells NaN
    → None. Kept as NaN here so the EffectResult stays float-typed."""
    return float("nan") if value is None else float(value)


def _alive_markets(markets: list[dict[str, Any]], date: dt.date) -> list[dict[str, Any]]:
    """The markets that could print a price on `date` — inception reached
    and a data era in force — mirroring `event_study.compute_effects`."""
    alive = []
    for market in markets:
        inception = dt.date.fromisoformat(str(market["inception_date"])[:10])
        if inception > date:
            continue
        try:
            event_study.native_resolution(market, date)
        except event_study.StudyError:
            continue
        alive.append(market)
    return alive


def results_for_pack(
    rows_by_event: dict[str, list[dict[str, Any]]],
    pack: Any,
    dates: dict[str, dt.date],
    *,
    event_ids: list[str],
    tickers: set[str] | None = None,
) -> tuple[list[event_study.EffectResult], dict[str, int]]:
    """Panel rows → EffectResults for ONE pack's markets, with `first_mover`
    resolved over that pack's markets exactly as the study resolves it.

    `tickers` narrows the rows to the markets THIS pack owns in the refill
    (see `refill`: a market two packs share is written once, by the first
    pack that names it). Returns the results and a drop count by reason: an
    event the graph no longer holds gets no edge (its src node is gone — the
    wire may have been reloaded under a different roster).
    """
    by_ticker = {str(m["ticker"]): m for m in pack.markets}
    if tickers is None:
        tickers = set(by_ticker)
    results: list[event_study.EffectResult] = []
    dropped: dict[str, int] = {}

    def _drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for event_id in event_ids:
        date = dates.get(event_id)
        rows = [r for r in rows_by_event.get(event_id, []) if r["market_ticker"] in tickers]
        if not rows:
            continue
        if date is None:
            for _ in rows:
                _drop("event no longer in the graph")
            continue
        alive = _alive_markets(pack.markets, date)
        movers = trading_calendar.first_movers(
            {m["ticker"]: trading_calendar.calendar_for(m, date) for m in alive}, date
        )
        for row in rows:
            ticker = str(row["market_ticker"])
            if ticker not in by_ticker:
                _drop("ticker not in pack")
                continue
            results.append(event_study.EffectResult(
                event_node_id=event_id,
                market_ticker=ticker,
                window=str(row["effect_window"]),
                resolution=str(row["resolution"]),
                raw_return=_num(row["raw_return"]),
                expected_return=_num(row["expected_return"]),
                abnormal_return=_num(row["abnormal_return"]),
                t_stat=_num(row["t_stat"]),
                p_value=_num(row["p_value"]),
                first_mover=bool(movers.get(ticker, False)),
                overlapping=str(row["status"]) == "overlapping",
                method=str(row["method"]),
            ))
    return results, dropped


def write_results(
    graph: Any, results: list[event_study.EffectResult], pack: Any
) -> int:
    """Through the one door, grouped by the source each number came from."""
    market_node_ids = {m["ticker"]: m["id"] for m in pack.markets}
    by_source: dict[str, list[event_study.EffectResult]] = {}
    for result in results:
        by_source.setdefault(runner.effect_source(result), []).append(result)
    written = 0
    for source_id, group in by_source.items():
        written += effects_writer.write_effects(
            graph, group, market_node_ids=market_node_ids, source_id=source_id
        )
    return written


class Marker:
    """Where a refill got to, on disk beside the graph, so a run cut by its
    budget resumes at the next chunk instead of at the beginning.

    Idempotence already makes a restart SAFE (`write_edges` reads before it
    creates); the marker makes it CHEAP — without it every resumed slice
    re-reads a million existence rows to write nothing.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.state: dict[str, Any] = {"done_packs": [], "pack": None, "after": None}
        with contextlib.suppress(OSError, ValueError, TypeError):
            self.state.update(json.loads(path.read_text(encoding="utf-8")))

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state), encoding="utf-8")
        except OSError:
            pass  # a read-only volume must not fail the refill

    def clear(self) -> None:
        self.state = {"done_packs": [], "pack": None, "after": None}
        with contextlib.suppress(OSError):
            self.path.unlink()


def refill(
    graph: Any,
    rows: list[dict[str, Any]],
    packs: list[Any],
    dates: dict[str, dt.date],
    *,
    marker: Marker | None = None,
    chunk_events: int = DEFAULT_CHUNK_EVENTS,
    deadline: float | None = None,
    log: Any = None,
) -> dict[str, Any]:
    """Project the panel's measured rows back onto AFFECTED, pack by pack.

    Stops cleanly at a chunk boundary once `deadline` (a `time.monotonic()`
    stamp) passes, recording where it got to in `marker`; a later call
    resumes there. Returns counts, and `complete` once every pack is done.
    """
    rows_by_event: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_event.setdefault(str(row["event_node_id"]), []).append(row)
    ordered_events = sorted(rows_by_event)

    state = marker.state if marker is not None else {"done_packs": [], "pack": None, "after": None}
    written = 0
    events_done = 0
    dropped_total: dict[str, int] = {}
    stopped_early = False

    # A SHARED MARKET IS WRITTEN ONCE. The edge's identity is (event, market,
    # window) whichever pack measured it, so writing brent under mena and again
    # under china would be the same edges twice — an existence read and a SET
    # for nothing. `first_mover` is the one field that depends on WHICH pack:
    # it says "earliest session among the markets measured together", and the
    # study writes it last-pack-wins. Here the owner is the pack with the
    # BROADEST market set among those naming the ticker (ties: pack order), so
    # the flag is resolved against the widest comparison the platform makes
    # and the choice is deterministic rather than an accident of seed order.
    owned_by: dict[str, str] = {}
    for pack in sorted(packs, key=lambda p: -len(p.markets)):
        for market in pack.markets:
            owned_by.setdefault(str(market["ticker"]), pack.name)

    for pack in packs:
        if pack.name in state["done_packs"]:
            continue
        owned = {t for t, owner in owned_by.items() if owner == pack.name}
        after = state["after"] if state.get("pack") == pack.name else None
        pending = [e for e in ordered_events if after is None or e > after]
        state["pack"] = pack.name
        for start in range(0, len(pending), chunk_events):
            if deadline is not None and time.monotonic() > deadline:
                stopped_early = True
                break
            batch = pending[start:start + chunk_events]
            results, dropped = results_for_pack(
                rows_by_event, pack, dates, event_ids=batch, tickers=owned
            )
            for reason, count in dropped.items():
                dropped_total[reason] = dropped_total.get(reason, 0) + count
            if results:
                written += write_results(graph, results, pack)
            events_done += len(batch)
            state["after"] = batch[-1]
            if marker is not None:
                marker.save()
            if log is not None:
                log(f"{pack.name}: {events_done} events, {written} edges so far")
        if stopped_early:
            break
        state["done_packs"] = [*state["done_packs"], pack.name]
        state["pack"] = None
        state["after"] = None
        if marker is not None:
            marker.save()

    complete = not stopped_early and all(p.name in state["done_packs"] for p in packs)
    return {
        "edges_written": written,
        "events": events_done,
        "dropped": dropped_total,
        "stopped_early": stopped_early,
        "complete": complete,
        "done_packs": list(state["done_packs"]),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """What the panel says it measured — the number the rebuilt table should
    reach, so a reader can compare before and after."""
    events = {str(r["event_node_id"]) for r in rows}
    tickers: dict[str, int] = {}
    for row in rows:
        tickers[str(row["market_ticker"])] = tickers.get(str(row["market_ticker"]), 0) + 1
    finite = sum(
        1 for r in rows
        if r["abnormal_return"] is not None and math.isfinite(float(r["abnormal_return"]))
    )
    return {"rows": len(rows), "events": len(events), "finite_abnormal": finite,
            "per_ticker": dict(sorted(tickers.items(), key=lambda kv: -kv[1]))}
