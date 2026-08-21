"""The jobs themselves — what the API converges in the background.

Each is a bounded, resumable slice of something that used to happen only in a
boot. They are defined here rather than in `jobs.py` so the scheduler stays a
scheduler: cadence, deadlines, status and failure backoff live there; what the
platform actually owes itself lives here.

Order of value, which is also the order they were written:

  * `study` — while the snapshot is frozen this is a no-op. Live intake
    is GDELT 2.0, scored and measured in memory against the baseline map.
    Unfrozen, it walks leftover graph events into Postgres; it does not
    grow the wire or the AFFECTED copy.
  * `games` — the region scenario maps. They read the graph and write only
    Postgres, so they were never a reason to hold anyone's lock; as a boot
    step they cost ~3.5 minutes of container downtime per re-solve, which is
    what made a PAYLOAD_VERSION bump expensive enough to skip.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import functools
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from core import packs, snapshot
from core import settings as settings_module
from core.ingestion import harvest as harvest_module
from core.transmission import event_study, runner
from core.wire import corpus


def _panel() -> Any | None:
    from core.panel import pg_store

    try:
        return pg_store.connect(settings_module.load())
    except pg_store.PanelUnavailable:
        return None


def _pack_names() -> list[str]:
    try:
        return packs.available()
    except Exception:  # noqa: BLE001 - a broken pack is not a scheduler failure
        return []


def _newest_event_time(conn: Any) -> str:
    """The archive's newest timestamp, without re-parsing 1.33M rows.

    `forecasting.rows_from_conn` unions the graph with a full corpus parse —
    ~1.4 GB of dicts that `serving.warm()` already threw away. The slim view
    is already in memory; the graph max is the fallback when it is not.
    """
    try:
        from core.wire import serving

        rows, _truncated = serving.events_window(
            None, None, None, 1, newest_first=True
        )
        if rows:
            return str(rows[0].get("event_time") or "")
    except Exception:  # noqa: BLE001 - a closed corpus must not force a re-parse
        pass
    from core.graph import kuzu_store

    rows = kuzu_store.query(conn, "MATCH (e:Event) RETURN max(e.event_time) AS n")
    return str(rows[0]["n"] or "") if rows else ""


#: Headroom a job wants before it starts a REGION-SIZED read. The scheduler's
#: own guard asks once, before the job; this is the check inside a job that
#: loops over regions, because the second region is where the first region's
#: working set is still resident.
REGION_HEADROOM = 0.30


def _memory_is_tight(floor: float = REGION_HEADROOM) -> bool:
    """Is the container too close to its limit to load another region?

    An OOM kill is not catchable — the kernel takes the process mid-write. On
    2026-08-18 the games and markets jobs each walked all three regions in one
    slice, peaked at 7.76 GB of 8 GB, and the container restart-looped.
    """
    from core.api.jobs import memory_is_tight

    return memory_is_tight(floor)


# ── the measurement backlog ────────────────────────────────────────────────


#: Events handed to one tick. The DEADLINE is the real bound; this only stops
#: a tick from preloading a panel span for work it will never reach — so it
#: has to be comfortably more than a slice can measure, or it becomes the
#: bound by accident. Measured 2026-08-16: a tick measured 400 events in 23.6s
#: of a 45s slice, i.e. the cap was doing the stopping and half the slice went
#: unused, against a backlog of 564,596 events.
STUDY_EVENTS_PER_TICK = int(os.getenv("GEOGRAPH_STUDY_EVENTS_PER_TICK", "2500"))

#: The floor the adaptive cap will not go below — under this a tick's preload
#: costs more than the measuring it enables.
STUDY_EVENTS_FLOOR = 250

#: Kuzu's "I could not get a page" signature. Unlike the storage assertion this
#: is a legitimate resource limit, not a bug: the answer is a smaller tick, not
#: a different process.
_POOL_EXHAUSTED = "Buffer manager exception"

#: Halved for the process's life each time the pool is exhausted, because the
#: right tick size depends on how large AFFECTED has grown and that only ever
#: goes up.
_events_per_tick = STUDY_EVENTS_PER_TICK

#: The archive scan, memoised on the graph's own event count. Reading every
#: event and parsing every date is ~4s at 456k events and grows with the wire
#: load; doing it per tick would spend a quarter of every slice re-deriving an
#: input that only changes when the wire job writes. The count is the cache
#: key because it is one query and it moves exactly when the archive does.
_archive_cache: dict[str, Any] = {"count": None, "events": None, "dates": None}


def _archive(conn: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from core.graph import kuzu_store

    rows = kuzu_store.query(conn, "MATCH (e:Event) RETURN count(e) AS n")
    count = int(rows[0]["n"]) if rows else 0
    if _archive_cache["count"] != count:
        # Lean rows: the job never prints an event name, and the name is the
        # heaviest column in a cache this process holds between ticks.
        events = runner.archive(conn, with_names=False)
        _archive_cache.update({
            "count": count,
            "events": events,
            "dates": {
                e["id"]: event_study.parse_event_date(e["date"]) for e in events
            },
        })
    return _archive_cache["events"], _archive_cache["dates"]


def forget_wire_ids() -> None:
    """Drop the wire job's process-lifetime flags. Called under memory
    pressure so the next tick re-merges roster dyads if it has to."""
    global _dyads_ensured
    _dyads_ensured = False


def forget_archive() -> None:
    """Drop the cached archive. The scheduler calls this under memory
    pressure: it is ~1.07M lean rows plus their parsed dates, the largest
    rebuildable thing this process owns after the corpus, and rebuilding it
    is one graph scan."""
    _archive_cache.update({"count": None, "events": None, "dates": None})


#: How long a study CHILD may run when one is needed, and how much graph-dark
#: time that costs. See `study` for when a child is needed at all.
STUDY_CHILD_SECONDS = float(os.getenv("GEOGRAPH_STUDY_CHILD_SECONDS", "90"))

#: THE STUDY RUNS AS A CHILD BY DEFAULT, and the reason is not the one that
#: moved it there the first time.
#:
#: 2026-08-16, in production: the container restart-looped for hours. Boot
#: clean, "Application startup complete", then gone — no traceback, no error
#: line, 4.25 GB peak against an 8 GB limit, so not the OOM of the day before.
#: The breadcrumbs named it in three identical cycles: counts finished, games
#: finished, `study starting`, process dead. A segfault in Kuzu's C++ layer on
#: the AFFECTED write, which Python cannot catch and which took the whole API
#: with it every ninety seconds.
#:
#: In-process is cheaper and it was measured working — 2,500 events a turn,
#: clean, all night. It is still the wrong default, because the failure mode
#: is not "the job fails", it is "the site dies". A child that segfaults is a
#: recorded non-zero exit and a job that backs off; the graph goes dark for
#: the slice and comes back. That asymmetry decides it: this loop exists to
#: keep the platform current, and it must not be able to take the platform
#: down to do it.
#:
#: `GEOGRAPH_STUDY_IN_PROCESS=1` opts back in once the write is trustworthy.
_PREFER_CHILD = os.getenv("GEOGRAPH_STUDY_IN_PROCESS", "") != "1"

#: Seconds between CHILD slices. The in-process path runs on the job's own
#: cadence and costs nothing; a child takes the graph dark for its whole
#: budget, so it rate-limits ITSELF rather than making the scheduler's
#: interval depend on which path is in use. 90s dark per 900s is ~90%
#: availability, which is the deliberate trade when the fallback is in play.
CHILD_INTERVAL_SECONDS = float(os.getenv("GEOGRAPH_STUDY_CHILD_EVERY", "900"))
_LAST_CHILD = 0.0

#: The internal-assertion signature that means "this write cannot be done from
#: this process". Matched narrowly: Kuzu raises RuntimeError for it, and
#: catching RuntimeError broadly would swallow real failures.
_STORAGE_ASSERTION = "KU_UNREACHABLE"


def study(conn: Any, deadline: float) -> dict[str, Any]:
    """Measure leftover graph events, or skip while the snapshot is frozen.

    THE SNAPSHOT IS THE BASELINE MAP. While it is frozen (the default),
    this job is a no-op — same posture as harvest. Leftover graph events
    are not walked into `event_study_runs`, because that table is the
    transmission map live GDELT 2.0 is priced against. Unfreeze to grow
    the training set again.

    IN A CHILD PROCESS when it does run, because a segfault in the writer
    must not be able to take the API with it — see `_PREFER_CHILD`.

    THE HISTORY, because this looks like flip-flopping and is not. Four write
    topologies died in `csr_node_group.cpp KU_UNREACHABLE` — a sibling
    connection, this connection, this connection behind a fair lock, and a
    child process — and the fourth is what showed the topology was never the
    variable. Line 411 is the `default:` arm of `CSRNodeGroup::scan()`, so the
    failing statement was the READ that MERGE performs to decide whether to
    create, walking a Market node's share of 756,025 edges.
    `transmission/effects.py` now writes through `kuzu_store.write_edges`,
    which never asks for that scan, and the first slice after it shipped wrote
    18,000 edges with provenance clean.

    So the work comes back here. If the assertion ever returns, the job records
    it and switches to the child for the rest of the process's life rather
    than failing every tick — the fallback is kept precisely because it is the
    configuration that wrote the first 632,000 edges.

    Round-robins by taking whichever pack has the most left, so no pack is
    starved by alphabet (the failure the boot era's fair-share comment
    records: mena, always last, measured nothing for weeks).
    """
    global _PREFER_CHILD, _events_per_tick
    from core.graph import kuzu_store as _store

    if snapshot.frozen():
        return {
            "skipped": "snapshot frozen — live intake is GDELT 2.0",
            "weights": "transmission map",
        }

    graph_path = settings_module.load().kuzu_db_path
    # THE 5 GB GRAPH HOLDS THE KNOWLEDGE GRAPH — actors, dyads, RELATES_TO
    # and the AFFECTED edges already written. New AFFECTED edges are a mirror
    # of the panel and cost ~2 KB each, which is what filled the volume.
    # While frozen this function has already returned. Unfrozen, a tight
    # volume still measures into Postgres and stops adding edges.
    write_graph = runner.WRITE_GRAPH_EFFECTS and not _store.disk_is_tight(graph_path)
    # A child is the AFFECTED-writer fallback. Postgres-only has nothing
    # for it to do, and a child that still honours WRITE_GRAPH_EFFECTS
    # would try to grow a full volume.
    if _PREFER_CHILD and write_graph:
        return _study_child_plan(deadline)
    try:
        return _study_in_process(conn, deadline, write_graph=write_graph)
    except RuntimeError as exc:
        if _POOL_EXHAUSTED in str(exc):
            # A RESOURCE LIMIT, NOT A BUG. Kuzu could not get a page for the
            # working set this tick asked for. The tick is what is wrong, so
            # the tick shrinks — for the process's life, because the right
            # size depends on how large AFFECTED has grown and that only goes
            # one way.
            was = _events_per_tick
            _events_per_tick = max(STUDY_EVENTS_FLOOR, _events_per_tick // 2)
            return {
                "pool_exhausted": True,
                "events_per_tick": _events_per_tick,
                "was": was,
                "reason": (
                    "Kuzu's buffer pool could not free a page for this tick's "
                    f"working set; the tick shrinks from {was} to "
                    f"{_events_per_tick} events for the life of this process"
                ),
            }
        if _STORAGE_ASSERTION not in str(exc):
            raise
        _PREFER_CHILD = True
        return {
            "switched_to_child": True,
            "reason": (
                "an in-process AFFECTED write hit Kuzu's storage assertion "
                f"({_STORAGE_ASSERTION}); the study runs as a child from now "
                "on, which costs ~90s of graph-dark time per slice"
            ),
        }


def _study_child_plan(deadline: float) -> dict[str, Any]:
    """The plan the scheduler's child runner executes: an argv and a budget.

    The child stops on its OWN budget rather than being killed, because a
    write killed mid-commit is the one way this loop could damage the volume.
    """
    global _LAST_CHILD
    from core.panel import pg_store

    if snapshot.frozen():
        return {
            "skipped": "snapshot frozen — live intake is GDELT 2.0",
            "weights": "transmission map",
        }

    since = time.monotonic() - _LAST_CHILD
    if _LAST_CHILD and since < CHILD_INTERVAL_SECONDS:
        return {"skipped": "the child rate-limits itself — each slice takes "
                           "the graph dark",
                "next_in_seconds": round(CHILD_INTERVAL_SECONDS - since)}
    names = _pack_names()
    if not names:
        return {"skipped": "no packs"}
    panel = _panel()
    if panel is None:
        return {"skipped": "no panel"}
    try:
        backlog: list[tuple[int, str]] = []
        for name in names:
            pack = packs.load(name)
            measured = pg_store.measured_events(
                panel, [m["ticker"] for m in pack.markets]
            )
            backlog.append((len(measured), name))
    finally:
        panel.close()
    # `measured_events` counts what is DONE, so the pack with the fewest done
    # is the one furthest behind on a shared archive.
    backlog.sort()
    target = backlog[0][1]
    budget = min(STUDY_CHILD_SECONDS, max(0.0, deadline - time.monotonic()))
    if budget < 20:
        return {"skipped": "slice too short", "pack": target}
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_event_study.py"
    _LAST_CHILD = time.monotonic()
    return {
        "pack": target,
        "budget_seconds": budget,
        "argv": [sys.executable, str(script), target, "--all",
                 "--budget-seconds", str(int(budget))],
        "measured_by_pack": {n: c for c, n in backlog},
    }


def _pack_study_events(
    conn: Any, pack: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Candidates for one pack: graph spine/deep-tier, plus corpus wire
    only when the snapshot is unfrozen.

    Frozen: leftover graph events are still listed here, but `study()`
    never reaches this function. Unfrozen opt-in can grow the training
    set from the corpus; that path is not the launch default.
    """
    curated = runner.curated_event_ids(pack)
    graph_events, graph_dates = _archive(conn)
    events = [
        e for e in graph_events
        if e["id"] in curated or not str(e["id"]).startswith("event:gdelt-")
    ]
    dates = {e["id"]: graph_dates[e["id"]] for e in events if e["id"] in graph_dates}
    # Frozen: the corpus is weights. Measuring new GDELT 1.0 days into the
    # panel would grow the transmission map the live overlay is priced against.
    if not snapshot.frozen():
        events, dates = runner.add_corpus_wire(events, dates, pack)
    return runner.select_all(events, curated), dates


def _study_in_process(
    conn: Any, deadline: float, write_graph: bool = True,
) -> dict[str, Any]:
    from core.graph import kuzu_store as _store
    from core.panel import pg_store

    if snapshot.frozen():
        return {
            "skipped": "snapshot frozen — live intake is GDELT 2.0",
            "weights": "transmission map",
        }

    graph_path = settings_module.load().kuzu_db_path
    names = _pack_names()
    if not names:
        return {"skipped": "no packs"}
    panel = _panel()
    if panel is None:
        return {"skipped": "no panel"}
    try:
        backlog: list[tuple[int, str, list[dict[str, Any]], Any, dict[str, Any]]] = []
        for name in names:
            pack = packs.load(name)
            candidates, dates = _pack_study_events(conn, pack)
            measured = pg_store.measured_events(
                panel, [m["ticker"] for m in pack.markets]
            )
            left = [e for e in candidates if e["id"] not in measured]
            backlog.append((len(left), name, left, pack, dates))
        backlog.sort(key=lambda item: -item[0])

        remaining_total = sum(item[0] for item in backlog)
        if not remaining_total:
            return {"measured": 0, "remaining": 0, "note": "archive fully measured"}

        count, name, left, pack, dates = backlog[0]
        if time.monotonic() >= deadline:
            return {"skipped": "no time in this slice", "remaining": remaining_total}
        from core.api.jobs import memory_is_tight as jobs_tight

        def out_of_room() -> bool:
            """Stop this slice on EITHER ceiling.

            Memory: this is the job that was writing when the container was
            killed on 2026-08-17, and the kill is what left the graph
            unopenable.

            Disk: when this slice is still mirroring AFFECTED into the graph,
            stop at the 400 MB floor rather than filling to zero. A later tick
            continues into Postgres with `write_graph=False`. The panel has
            the same 5 GB cap: stop a tick that would fill it, prune GDELT
            skips, and resume. Memory still always stops the slice — that
            kill is uncatchable.
            """
            if jobs_tight():
                return True
            if pg_store.panel_is_tight(panel):
                return True
            return write_graph and _store.disk_is_tight(graph_path)

        if pg_store.panel_is_tight(panel):
            pruned = pg_store.prune_gdelt_runs(panel, drop_skips=True)
            if pg_store.panel_is_tight(panel):
                return {
                    "stopped": "panel volume nearly full",
                    "postgres": pg_store.storage_report(panel),
                    "pruned": pruned,
                    "remaining_total": remaining_total,
                    "note": (
                        "The study writes computed CARs into Postgres, not "
                        "AFFECTED. GDELT skip-rows are the blast; they are "
                        "pruned into event_study_coverage. If this still "
                        "fires, the panel itself is full of numbers."
                    ),
                }

        slice_events = left[:_events_per_tick]
        outcome = runner.measure(
            conn, panel, pack, slice_events,
            all_dates=dates, deadline=deadline,
            stop_when=out_of_room, write_graph=write_graph,
            graph_event_ids=runner.graph_mirror_ids(slice_events),
        )
        return {
            "pack": name,
            "measured": outcome["events"],
            "edges": outcome["edges"],
            "stopped_for": outcome.get("stopped_for"),
            "remaining_in_pack": count - outcome["events"],
            "remaining_total": remaining_total - outcome["events"],
            "backlog": {n: c for c, n, *_ in backlog},
            "graph_effects": write_graph,
        }
    finally:
        panel.close()


# ── an unfinished AFFECTED refill, finished with the site up ────────────────


#: Events per refill chunk INSIDE THE API: small, because every write is a
#: statement a reader waits behind (the FIFO lock), and the point of doing
#: this here rather than in a boot is that the site stays up.
REFILL_CHUNK_EVENTS = 200

#: Left beside the graph by a reset: "this graph needs its measured effects
#: projected back from the panel once the wire job has marked the roster
#: ready (GDELT Event nodes are no longer copied into the graph).
REFILL_PENDING = ".affected-refill-pending"

#: Panel rows a refill tick may hold at once. The projection writes ~200
#: edges/s, so a 180s slice writes ~36k edges and never needs more than this;
#: the cap exists because the READ is what allocates, not the write. Unbounded,
#: it pulled every remaining row of ~1M into Python dicts in a single call and
#: killed the container.
REFILL_ROWS_PER_TICK = int(os.getenv("GEOGRAPH_REFILL_ROWS", "120000"))


def refill(conn: Any, deadline: float) -> dict[str, Any]:
    """Finish an AFFECTED re-projection the boot's repair step left unfinished.

    The 2026-08-16 repair proved the table takes writes again (the probe after
    the rebuild passed) and re-projected 447,484 of 1,051,722 edges before its
    budget ended — at ~200 edges/s a full refill is ~90 minutes, and every
    minute of it in a boot is a minute the graph is dark. So the marker the
    boot left (`.affected-refill.json`) is picked up HERE, in slices, through
    the same `rebuild.refill`, and cleared when the projection is complete.
    Idempotent: `write_edges` reads before it creates, and the marker says
    where to resume. Nothing to do when no marker exists.
    """
    from core.graph import kuzu_store
    from core.transmission import rebuild

    graph_path = settings_module.load().kuzu_db_path
    marker_path = graph_path.with_name(".affected-refill.json")
    pending_path = graph_path.with_name(REFILL_PENDING)
    if not marker_path.exists():
        # A rebuilt graph asks for a refill by leaving `REFILL_PENDING`; it
        # can only start once roster dyads exist and the wire job has marked
        # complete (it no longer copies GDELT into Kuzu — refill projects
        # onto spine/deep-tier Event nodes only).
        if not pending_path.exists():
            return {"note": "no refill pending"}
        if not wire_complete():
            return {"waiting": "waiting for the wire job to mark the roster ready"}
        rebuild.Marker(marker_path).save()
        with contextlib.suppress(OSError):
            pending_path.unlink()
    if kuzu_store.disk_is_tight(graph_path):
        return {"stopped": "volume nearly full", "disk": kuzu_store.disk_usage(graph_path)}
    marker = rebuild.Marker(marker_path)
    panel = _panel()
    if panel is None:
        return {"skipped": "no panel"}
    try:
        # Only the rows past the marker for the pack in progress; a pack not
        # yet started reads from its beginning on its own turn.
        after = marker.state.get("after") if marker.state.get("pack") else None
        # BOUNDED, because this runs in the API's own process. Reading every
        # remaining row took the container to 7.1 GB of a 7.45 GB limit the
        # first time the wire finished and this job actually had work — a
        # crash loop no guard could catch, since the whole allocation happens
        # inside one call. A tick's slice writes far fewer than this anyway.
        rows = rebuild.panel_effect_rows(panel, after=after, limit=REFILL_ROWS_PER_TICK)
    finally:
        panel.close()
    dates = rebuild.event_dates(conn)
    loaded = [packs.load(name) for name in _pack_names()]
    outcome = rebuild.refill(
        conn, rows, loaded, dates, marker=marker,
        chunk_events=REFILL_CHUNK_EVENTS, deadline=deadline,
    )
    if outcome["complete"]:
        marker.clear()
    return {**outcome, "rows_read": len(rows)}


# ── the region scenario maps ───────────────────────────────────────────────


#: How far AFFECTED must grow before a persisted map is re-priced. The study
#: adds ~5,000 edges a tick; re-solving on every tick would re-solve forever
#: for a market row that moved in the fourth decimal.
GAMES_REPRICE_GROWTH = 0.05


def games_inputs(conn: Any, panel: Any | None = None) -> dict[str, Any]:
    """What a region solve READS, as cheap facets.

    THE FINGERPRINT COVERS WHAT THE SOLVE READS — the lesson the deep-tier
    guard taught on 2026-08-16. The persisted maps were re-solved on a
    PAYLOAD_VERSION change and on nothing else, so correcting a relationship
    (US-Israel re-dated, US-Iran declared) reached the game page only through
    a code deploy that bumped the version by hand. Now: the RELATES_TO web
    (the standing every classification and chip reads), the measured-run
    count (the pricing — panel first, graph AFFECTED as fallback), and the
    frozen model (the tilt).
    """
    from core.games import scenarios
    from core.graph import kuzu_store
    from core.panel import pg_store

    def _one(query: str) -> Any:
        rows = kuzu_store.query(conn, query)
        return rows[0]["n"] if rows else None

    affected: Any = None
    if panel is not None:
        try:
            affected = pg_store.computed_run_count(panel)
        except Exception:  # noqa: BLE001 - fall back to the graph copy
            affected = None
    if affected is None:
        affected = _one("MATCH ()-[a:AFFECTED]->() RETURN count(a) AS n")

    return {
        "version": str(scenarios.PAYLOAD_VERSION),
        "relates": _one("MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS n"),
        "relates_latest": _one("MATCH ()-[r:RELATES_TO]->() RETURN max(r.valid_from) AS n"),
        "affected": affected,
        "model_frozen": _one(
            "MATCH (f:Forecast) WHERE f.mode = 'model' RETURN max(f.generated_at) AS n"
        ),
    }


def games_stale(stored: dict[str, Any] | None, current: dict[str, Any]) -> str | None:
    """Why a persisted region map should be re-solved, or None if it stands.

    A missing row and a version mismatch are the old rules; a standing change
    (any movement in the RELATES_TO web), a re-frozen model, or AFFECTED
    growth past `GAMES_REPRICE_GROWTH` are the new ones. A map solved before
    this check existed carries no fingerprint and is re-solved once.
    """
    if stored is None:
        return "no persisted map"
    was = stored.get("inputs")
    if not isinstance(was, dict):
        return "persisted map carries no inputs fingerprint"
    for key in ("version", "relates", "relates_latest", "model_frozen"):
        if was.get(key) != current.get(key):
            return f"{key} moved ({was.get(key)!r} -> {current.get(key)!r})"
    if snapshot.frozen():
        # Live GDELT 2.0 is an overlay at read time. Re-solving the region
        # because a 15-minute file arrived is what OOM-killed the boot:
        # three region solves plus refresh_all on top of the warmed corpus
        # peaked at 7.76 GB of 8 GB, then the kernel took the process.
        return None
    before, now = was.get("affected"), current.get("affected")
    if (isinstance(before, int) and isinstance(now, int) and before > 0
            and abs(now - before) / before >= GAMES_REPRICE_GROWTH):
        return f"AFFECTED moved {before:,} -> {now:,}"
    return None


def games(conn: Any, deadline: float) -> dict[str, Any]:
    """Re-solve a region whose persisted map is stale, one region per tick.

    STALE is decided by `games_stale` over `games_inputs`: no row, a different
    `PAYLOAD_VERSION` (the reader would reject it and the endpoint would solve
    live on every request), a moved RELATES_TO web, a re-frozen model, or
    AFFECTED grown past the re-price threshold. When the snapshot is frozen,
    the map is weights; live GDELT 2.0 overlays at read time and does not
    re-solve.
    """
    from core.games import context as context_module
    from core.games import scenarios
    from core.games import solve as solve_module
    from core.panel import pg_store

    panel = _panel()
    if panel is None:
        return {"skipped": "no panel"}
    solved_now: list[dict[str, Any]] = []
    try:
        pg_store.apply_schema(panel)
        current = games_inputs(conn, panel)
        for name in _pack_names():
            if time.monotonic() >= deadline:
                return {"solved": solved_now, "skipped": "slice spent"}
            stored = pg_store.game_solution(
                panel, name, scope="region", version=scenarios.PAYLOAD_VERSION
            )
            why = games_stale(stored, current)
            if why is None:
                continue
            # ONE REGION PER TICK. A version bump used to KEEP GOING through
            # all three (~3 min, several GB) inside the serving process;
            # 2026-08-18 that peaked at 7.76 GB of 8 GB and the kernel killed
            # the container on boot. A stale region answers `resolving: true`
            # until its turn — that is the honest fallback, not a live solve.
            if _memory_is_tight():
                return {
                    "solved": solved_now,
                    "skipped": "memory tight",
                    "waiting": name,
                    "why": why,
                }
            try:
                context = context_module.build(conn, name)
            except (context_module.GraphNeeded, context_module.NothingToSolve) as exc:
                solved_now.append({"region": name, "skipped": str(exc)})
                continue
            payoffs = solve_module.Payoffs(**context_module.fitted_payoffs(name))
            solved = scenarios.region_map(
                context, region=name, payoffs=payoffs, graph_conn=conn,
                dyad_ids=context_module.active_dyads(context, scenarios.REGION_DYADS),
            )
            # The fingerprint travels WITH the map, so the next tick can ask
            # whether what it read has moved without re-solving to find out.
            solved["region"]["inputs"] = current
            written = pg_store.record_game_solutions(
                panel, name, solved, solver=solved["region"]["primary_solver"]
            )
            solved_now.append({
                "region": name, "rows": written,
                "dyads": solved["region"]["dyads_solved"], "why": why,
            })
            return {"solved": solved_now, "version": scenarios.PAYLOAD_VERSION}
        if solved_now:
            return {"solved": solved_now, "version": scenarios.PAYLOAD_VERSION}
        return {"note": "every region's map is current"}
    finally:
        panel.close()


# ── the wire: corpus is the store; the graph keeps the roster ──────────────


#: The materiality bar the study uses for corpus wire events — the same bar
#: the transmission engine always measured at. An event under it is never
#: measured. The GRAPH no longer holds a copy of these events: putting them
#: in Kuzu (then AFFECTED onto them at ~2 KB/edge) is what filled the 5 GB
#: volume. Forecasts, games, hostility and the wire page already read the
#: corpus. While frozen, live intake is GDELT 2.0 on the overlay.
GRAPH_MIN_GOLDSTEIN = float(os.getenv("GEOGRAPH_GRAPH_MIN_GOLDSTEIN", "7.0"))

_wire_done: set[str] = set()
_dyads_ensured: bool = False


def _lean_marker(pack_name: str) -> Any:
    """Per-pack marker the refill used to wait on.

    Kept so a volume that already has these files still reads as complete,
    and so a reset's `.affected-refill-pending` is not blocked forever now
    that the wire job no longer copies GDELT into Kuzu.
    """
    root = settings_module.load().kuzu_db_path.parent / ".gdelt-loaded"
    return root / f"{pack_name}-lean.done"


def wire_complete() -> bool:
    """Roster dyads are in and the refill is allowed to project onto the
    spine. The corpus is the wire; there is no graph copy to wait for."""
    from core.wire import corpus

    return all(_lean_marker(name).exists() for name in corpus.installed())


def measurable(row: dict[str, Any]) -> bool:
    """Does this wire event clear the study's bar and the archive floor?"""
    from core import archive as archive_bounds

    goldstein = row.get("goldstein")
    return (
        archive_bounds.covers(row.get("event_time"))
        and goldstein is not None
        and abs(float(goldstein)) >= GRAPH_MIN_GOLDSTEIN
    )


def wire(conn: Any, deadline: float) -> dict[str, Any]:
    """Keep roster Dyad nodes current and poll the GDELT 2.0 overlay.

    Projecting GDELT Event nodes into Kuzu — and then AFFECTED onto them —
    is what filled the 5 GB volume with a 2 KB/edge duplicate of
    `event_study_runs`. The frozen snapshot is the wire. This job MERGEs
    the declared pairs (tens of nodes), marks lean-complete so a pending
    refill is not stranded, and refreshes the in-memory GDELT 2.0 overlay
    — scored against snapshot EWMAs, this-event CARs in process, never a
    graph edge and never a panel row.
    """
    del deadline
    from core.wire import corpus
    from core.wire import live as live_overlay

    written = _ensure_roster_dyads(conn)
    marked: list[str] = []
    for name in corpus.installed():
        if not _lean_marker(name).exists():
            _mark(_lean_marker(name))
            marked.append(name)
        _wire_done.add(name)

    live_out: dict[str, Any] = {}
    try:
        published = live_overlay.refresh_all()
        attached = 0
        for name in _pack_names():
            try:
                attached += live_overlay.attach_measured(packs.load(name))
            except Exception:  # noqa: BLE001 - one pack failing is not a job failure
                continue
        live_out = {"published": published, "measured": attached}
    except Exception as exc:  # noqa: BLE001 - live failing must not fail dyad merge
        live_out = {"error": str(exc)[:160]}

    return {
        "dyads": written,
        "marked_complete": marked,
        "live": live_out,
        "note": "roster dyads current; live intake is GDELT 2.0",
    }


def _ensure_roster_dyads(conn: Any) -> int:
    """Merge a Dyad node for every declared pack pair. Once per process.

    MERGE by node_id, and the skeleton omits EWMA slots so a re-merge cannot
    wipe Head B's baseline.
    """
    global _dyads_ensured
    from core.graph import kuzu_store
    from core.wire import corpus

    if _dyads_ensured:
        return 0
    written = 0
    for name in corpus.installed():
        rows = packs.load(name).dyad_nodes()
        if rows:
            written += kuzu_store.merge_nodes(conn, "Dyad", rows)
    _dyads_ensured = True
    return written


def _mark(marker: Any) -> None:
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("corpus", encoding="utf-8")
    except OSError:  # a read-only volume must not fail the loop
        pass


# ── the scoreboard, warmed ─────────────────────────────────────────────────


def calibration(conn: Any, deadline: float) -> dict[str, Any]:
    """Compute the calibration walk per region and cache it for the process.

    The walk re-runs the near-term estimator at every closed-horizon cutoff —
    a few seconds per region, plus the archive read. Left to the first reader
    it is a request that outlives a browser's patience, and it gets slower as
    the wire job grows the archive. A job pays it instead, and the endpoint
    serves an answer that is already there.
    """
    from core.reasoning import calibration as calibration_module
    from core.reasoning import forecasting

    # The API already holds the write lock — a second `kuzu.Database` here
    # is the configuration the job comments exist to forbid. And this is the
    # last heavy read on the first post-boot pass; use the connection we have.
    rows = forecasting.rows_from_conn(conn)
    size = len(rows)
    done: list[str] = []
    for name in _pack_names():
        if calibration_module.is_current(name, size):
            continue
        if time.monotonic() >= deadline:
            return {"computed": done, "note": "slice spent", "archive_rows": size}
        walk_out = calibration_module.walk(rows, region_pack=name)
        try:
            one = calibration_module.horizon_variant_walks(
                rows, region_pack=name, horizons=(1,),
            )
            walk_out = {
                **walk_out,
                "locked_horizon_years": one["locked_horizon_years"],
                "horizon_variants": one["variants"],
                "horizon_note": one["note"],
            }
        except Exception as exc:  # noqa: BLE001 - the 3y board must still ship
            walk_out = {
                **walk_out,
                "locked_horizon_years": 3,
                "horizon_note": f"1y variant skipped: {str(exc)[:120]}",
            }
        calibration_module.remember(name, size, walk_out)
        done.append(name)
    if not done:
        return {"note": "every region's scoreboard is current", "archive_rows": size}
    return {"computed": done, "archive_rows": size}


# ── Head B over what the wire just loaded ──────────────────────────────────


def rescore(conn: Any, deadline: float) -> dict[str, Any]:
    """Score the escalation of events the loader wrote without it.

    THE GAP THE WIRE JOB OPENS. `gdelt.write_events` writes an event's code,
    date, actors and Goldstein — not its escalation direction, magnitude or
    baseline, because Head B folds per dyad in time order and that is a
    separate pass. Everything that reads escalation OFF THE GRAPH (the dyad
    timeline, structural pressure, the graph half of the forecast union) sees
    nulls until this runs, so a wire load without a rescore quietly degrades
    exactly the surfaces the load was meant to feed.

    One dyad per step, whole history each time — see `rescore_dyad` for why
    that is equivalent to the archive-wide pass rather than a partial fold.
    """
    from core.classifier import rescore as rescore_module

    dyads = rescore_module.unscored_dyads(conn, limit=40)
    if not dyads:
        return {"note": "every event carries Head B's coding"}
    done: list[str] = []
    events = 0
    for dyad_id in dyads:
        if time.monotonic() >= deadline:
            break
        outcome = rescore_module.rescore_dyad(conn, dyad_id)
        events += int(outcome["events_rescored"])
        done.append(dyad_id)
    return {
        "dyads_rescored": len(done),
        "events": events,
        "dyads_pending_at_least": len(dyads) - len(done),
    }


# ── the frozen forecasts ───────────────────────────────────────────────────


#: The archive must move by this share before the freeze is worth re-running.
#: A freeze is minutes of solving, and 5,000 new wire events do not change a
#: base rate counted over a million — but 500,000 of them do, which is exactly
#: what the wire and study jobs are adding.
FREEZE_GROWTH = 0.05

#: Event count at the last freeze, per process. Forecast nodes persist on the
#: graph: a restart ADOPTS them rather than re-freezing. Re-freezing on every
#: boot was "cheap insurance" until it was the crash loop — 6.0 GB of a 7.45 GB
#: container on 2026-08-17, and with the corpus resident after boot the kernel
#: killed the process on every restart.
_frozen_at: dict[str, int] = {}


def forecasts(conn: Any, deadline: float) -> dict[str, Any]:
    """Re-freeze each region's Forecast nodes as the archive converges.

    THE CALL GOES STALE OTHERWISE. Every mode — the counted near-term base
    rates, the structural pressure, the model trajectory, the solved sequence
    — is a function of the archive, and the archive is now growing by hundreds
    of thousands of events in the background. Frozen on 2026-08-15 and never
    re-frozen, "the call" would describe a graph that no longer exists while
    the pages around it moved on.

    Runs INSIDE this process on the API's connection (`freeze(..., conn=...)`),
    because a second `kuzu.Database` here would fail on the write lock this
    process already holds.
    """
    import importlib.util
    from pathlib import Path

    from core.graph import kuzu_store

    rows = kuzu_store.query(conn, "MATCH (e:Event) RETURN count(e) AS n")
    size = int(rows[0]["n"]) if rows else 0
    if not size:
        return {"skipped": "empty graph"}

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "run_forecasts", root / "scripts" / "run_forecasts.py"
    )
    if spec is None or spec.loader is None:
        return {"skipped": "run_forecasts unavailable"}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from core.api.jobs import memory_is_tight as jobs_tight

    settings = settings_module.load()
    done: list[dict[str, Any]] = []
    for name in _pack_names():
        was = _frozen_at.get(name)
        if was is None:
            # THE WATERMARK OUTLIVES THE PROCESS. Same lesson as markets:
            # `_frozen_at` starts empty, so every restart re-froze every
            # region. That is the heaviest job in the loop, and after boot the
            # corpus is already resident — 2026-08-18 the kernel took the
            # container. The nodes are already on the graph; adopt the current
            # archive size. The next 5% of growth re-freezes.
            existing = kuzu_store.query(
                conn,
                "MATCH (f:Forecast) WHERE f.region_pack = $r RETURN count(f) AS n",
                {"r": name},
            )
            if existing and int(existing[0]["n"] or 0) > 0:
                _frozen_at[name] = size
                was = size
        if was is not None and was > 0 and abs(size - was) / was < FREEZE_GROWTH:
            continue
        if time.monotonic() >= deadline:
            return {"frozen": done, "skipped": "slice spent", "archive": size}
        # A REGION BOUNDARY IS A MEMORY BOUNDARY TOO. Freezing a region builds
        # columnar archives over the whole record, and this is the heaviest
        # job in the loop: measured at 6.0 GB of a 7.45 GB container on
        # 2026-08-17, with the wire job disabled. The deadline cannot see that,
        # and a region already frozen is recorded in `_frozen_at`, so stopping
        # between regions costs a tick rather than the work.
        if jobs_tight():
            return {"frozen": done, "skipped": "paused for memory", "archive": size}
        written = module.freeze(settings.kuzu_db_path, region_pack=name, conn=conn)
        _frozen_at[name] = size
        done.append({"region": name, "modes": [r["mode"] for r in written]})
    if not done:
        return {"note": "every region's forecast is current", "archive": size}
    return {"frozen": done, "archive": size}


# ── scoring the frozen trail ───────────────────────────────────────────────


def scores(conn: Any, deadline: float) -> dict[str, Any]:
    """Brier-score what has resolved; attach a retrodiction to what cannot be.

    Near-term calls are scored only once the archive outlives their horizon —
    three years, so nothing frozen this week resolves this week, and an open
    question is left visibly unscored rather than counted as a zero. The
    long-horizon mode is never Brier-scored (pressure over windows carries no
    dated point prediction); it carries a retrodiction instead, and THAT is
    what goes stale as the archive grows, because both the flagged windows and
    the conflict that followed are recomputed from it.
    """
    import json

    from core.graph import kuzu_store
    from core.reasoning import calibration as calibration_module
    from core.reasoning import forecasting

    rows = kuzu_store.query(
        conn,
        "MATCH (f:Forecast) RETURN f.node_id AS node_id, f.mode AS mode, "
        "f.region_pack AS region_pack, f.question AS question, "
        "f.generated_at AS generated_at, f.horizon_end AS horizon_end, "
        "f.scenarios_json AS scenarios_json, "
        "f.frozen_inputs_json AS frozen_inputs_json, "
        "f.boundary_statement AS boundary_statement, "
        "f.brier_score AS brier_score, f.retrodiction_json AS retrodiction_json "
        "ORDER BY f.node_id",
    )
    if not rows:
        return {"skipped": "no frozen forecasts"}

    # CHEAP LATEST, not the 1.33M-row archive. `rows_from_conn` re-parses the
    # corpus (~1.4 GB of dicts) that `serving.warm()` already evicted — and
    # on a restart that parse ran on the first job tick, on top of the freeze.
    # Ask the warmed slim view (or the graph) for the newest timestamp first;
    # skip the archive read when nothing is newly scoreable.
    latest = _newest_event_time(conn)
    needs_brier = False
    needs_retro = False
    for row in rows:
        mode = row.get("mode")
        if mode == "near_term":
            horizon_end = str(row.get("horizon_end") or "")
            if (
                horizon_end and latest and latest >= horizon_end
                and row.get("brier_score") in (None, "")
            ):
                needs_brier = True
        elif mode == "long_horizon" and not row.get("retrodiction_json"):
            needs_retro = True
    if not needs_brier and not needs_retro:
        return {"note": "nothing newly scoreable", "forecasts": len(rows)}

    archive_rows = forecasting.rows_from_conn(conn)
    episodes = calibration_module.episode_quarters(archive_rows)

    retro_by_region: dict[str, str] = {}
    if latest:
        as_of = f"{int(latest[:4]) - 10}-12-31"
        for name in _pack_names():
            if time.monotonic() >= deadline:
                break
            try:
                retro_by_region[name] = json.dumps(calibration_module.retrodict(
                    settings_module.load().kuzu_db_path,
                    as_of=as_of, region_pack=name, conn=conn,
                ))
            except Exception as exc:  # noqa: BLE001 - one region's failure is its own
                retro_by_region[name] = ""
                del exc

    updates: list[dict[str, Any]] = []
    scored = 0
    for row in rows:
        base = {k: (v if v is not None else "") for k, v in row.items()
                if k not in ("brier_score", "retrodiction_json")}
        scenarios = json.loads(str(row["scenarios_json"]) or "[]")
        frozen = json.loads(str(row["frozen_inputs_json"]) or "{}")
        if row["mode"] == "near_term":
            as_of = str(frozen.get("as_of") or "")
            horizon_end = str(row["horizon_end"] or "")
            if not as_of or not horizon_end or not latest or latest < horizon_end:
                continue  # an open question is not a zero
            quarters = max(int(horizon_end[:4]) - int(as_of[:4]), 1) * 4
            outcomes = calibration_module.near_term_outcomes(
                scenarios, episodes, as_of=as_of, horizon_quarters=quarters
            )
            if not outcomes:
                continue
            base["brier_score"] = calibration_module.score_forecast(scenarios, outcomes)
            scored += 1
        elif row["mode"] == "long_horizon":
            attached = retro_by_region.get(str(row["region_pack"]) or "")
            if not attached:
                continue
            base["retrodiction_json"] = attached
        else:
            continue
        updates.append(base)

    if updates:
        kuzu_store.merge_nodes(conn, "Forecast", updates)
    variants: dict[str, Any] = {}
    for name in _pack_names():
        if time.monotonic() >= deadline:
            break
        try:
            variants[name] = calibration_module.horizon_variant_walks(
                archive_rows, region_pack=name, horizons=(1,),
            )
        except Exception as exc:  # noqa: BLE001 - one pack's walk is its own
            variants[name] = {"skipped": str(exc)[:120]}
    return {
        "updated": len(updates),
        "brier_scored": scored,
        "horizon_1y": variants,
        "note": "1y is a named variant; the frozen call stays three years",
    }


# ── the network's shape, and the paper book ────────────────────────────────


#: Metrics and the backtest both read the whole archive, so both are gated on
#: it having actually moved rather than on a clock.
_metrics_at: dict[str, int] = {}
_backtest_at: dict[str, int] = {}


def metrics(conn: Any, deadline: float) -> dict[str, Any]:
    """Centrality, brokerage and communities over the windowed subgraphs.

    Persisted NetworkMetric nodes are what the explorer's time slider draws,
    and they are a function of the RELATES_TO web and the events inside each
    window — so they drift as the wire lands. Runs one window at a time so a
    slice can stop cleanly.
    """
    import importlib.util
    from pathlib import Path

    from core.graph import analytics, kuzu_store

    rows = kuzu_store.query(conn, "MATCH (e:Event) RETURN count(e) AS n")
    size = int(rows[0]["n"]) if rows else 0
    was = _metrics_at.get("all")
    if was is None:
        existing = kuzu_store.query(
            conn, "MATCH (n:NetworkMetric) RETURN count(n) AS n"
        )
        if existing and int(existing[0]["n"] or 0) > 0:
            _metrics_at["all"] = size
            was = size
    if was is not None and was > 0 and abs(size - was) / was < FREEZE_GROWTH:
        return {"note": "network metrics are current", "archive": size}

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "run_network_metrics", root / "scripts" / "run_network_metrics.py"
    )
    if spec is None or spec.loader is None:
        return {"skipped": "runner unavailable"}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    written = 0
    windows = module.standard_windows()
    for window in windows:
        if time.monotonic() >= deadline:
            return {"windows": written, "of": len(windows), "skipped": "slice spent"}
        written += analytics.compute_windows(None, [window], conn=conn)[0][1]
    _metrics_at["all"] = size
    return {"windows": len(windows), "metrics": written, "archive": size}


def backtest(conn: Any, deadline: float) -> dict[str, Any]:
    """The walk-forward paper book, re-walked as the archive grows.

    Reads the graph and writes only Postgres, so it never contends for the
    write lock; it is here because it is a function of the archive and the
    archive is moving.
    """
    import importlib.util
    from pathlib import Path

    from core.graph import kuzu_store

    rows = kuzu_store.query(conn, "MATCH (e:Event) RETURN count(e) AS n")
    size = int(rows[0]["n"]) if rows else 0
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "run_backtest", root / "scripts" / "run_backtest.py"
    )
    if spec is None or spec.loader is None:
        return {"skipped": "runner unavailable"}
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    persisted: set[str] = set()
    panel = _panel()
    if panel is not None:
        try:
            from core.panel import pg_store

            for name in _pack_names():
                if pg_store.backtest_run(panel, name) is not None:
                    persisted.add(name)
        finally:
            panel.close()

    done: list[str] = []
    for name in _pack_names():
        was = _backtest_at.get(name)
        if was is None and name in persisted:
            # Ledger lives in Postgres. A restart that re-walked every region
            # stacked another gigabyte on the first job tick after boot.
            _backtest_at[name] = size
            was = size
        if was is not None and was > 0 and abs(size - was) / was < FREEZE_GROWTH:
            continue
        if time.monotonic() >= deadline:
            return {"walked": done, "skipped": "slice spent"}
        if module.run(name) is not None:
            _backtest_at[name] = size
            done.append(name)
    if not done:
        return {"note": "every region's book is current", "archive": size}
    return {"walked": done, "archive": size}


# ── the markets story ──────────────────────────────────────────────────────


#: The archive must move by this share of AFFECTED before a region's markets
#: story is rebuilt — a region-wide read over the effects is seconds and the
#: story is quantiles, which a few thousand more measurements do not move.
MARKETS_GROWTH = 0.05
_markets_at: dict[str, int] = {}


def markets(conn: Any, deadline: float) -> dict[str, Any]:
    """Rebuild each region's markets story (core/reasoning/markets.py) and
    persist it, when the panel's measured-run count has moved enough since
    the last build."""
    from core.games import context as context_module
    from core.games import duration as duration_module
    from core.games import pricing as pricing_module
    from core.games import scenarios
    from core.graph import kuzu_store
    from core.models import panel as panel_module
    from core.panel import pg_store
    from core.reasoning import impact
    from core.reasoning import markets as markets_module

    panel = _panel()
    if panel is None:
        return {"skipped": "no panel"}
    try:
        affected = pg_store.computed_run_count(panel)
    except Exception:  # noqa: BLE001 - fall back to the graph copy
        rows = kuzu_store.query(conn, "MATCH ()-[a:AFFECTED]->() RETURN count(a) AS n")
        affected = int(rows[0]["n"]) if rows else 0
    done: list[str] = []
    try:
        pg_store.apply_schema(panel)
        flows = kuzu_store.query(
            conn,
            "MATCH (a:Actor)-[f:FLOW]->(m:Market) "
            "RETURN a.node_id AS actor_id, a.name AS actor_name, "
            "m.node_id AS market_id, f.as_of AS as_of, f.value_usd AS value_usd",
        )
        for name in _pack_names():
            was = _markets_at.get(name)
            if was is None:
                # THE WATERMARK OUTLIVES THE PROCESS. It used to live only in
                # this dict, so every restart rebuilt all three stories — the
                # heaviest read in the loop, running while the corpus was
                # still warming. 2026-08-18: that pair took the container to
                # 7.76 GB of 8 GB and the kernel killed it on boot.
                #
                # A story persisted BEFORE the stamp existed adopts the current
                # size rather than being rebuilt: a restart-looping container
                # must not answer "how stale is this?" by paying for all three,
                # and the next 5% of archive growth rebuilds it anyway.
                stored = pg_store.market_story(panel, name)
                if stored is not None:
                    built_at = stored.get("built_at_affected")
                    was = _markets_at[name] = (
                        built_at if isinstance(built_at, int) else affected
                    )
            if was is not None and was > 0 and abs(affected - was) / was < MARKETS_GROWTH:
                continue
            if time.monotonic() >= deadline:
                return {"built": done, "skipped": "slice spent"}
            if _memory_is_tight():
                return {"built": done, "skipped": "memory tight", "waiting": name}
            pack = packs.load(name)
            fund_ids = {str(a["id"]) for a in pack.actors if a.get("actor_type") == "swf"}
            game_map = pg_store.game_solution(
                panel, name, scope="region", version=scenarios.PAYLOAD_VERSION
            )
            try:
                context = context_module.build(conn, name)
                duration = duration_module.report(
                    context["effects"], pricing_module.dyad_of_event(context["effects"])
                )
                dyad_names = {
                    str(d["dyad_id"]): str(d["dyad_name"])
                    for d in panel_module.dyad_summary(context["table"])
                }
                as_of = context.get("as_of")
            except (context_module.GraphNeeded, context_module.NothingToSolve):
                duration, dyad_names, as_of = None, {}, None
            roster = {str(a["id"]) for a in pack.actors}
            coverage = impact.dyad_coverage(conn, name, roster)
            payload = markets_module.story(
                conn, pack, game_map=game_map, duration=duration,
                flows=[f for f in flows if str(f["actor_id"]) in fund_ids],
                coverage=coverage, as_of=as_of, dyad_names=dyad_names,
                panel=panel,
            )
            payload["built_at_affected"] = affected
            pg_store.record_market_story(panel, name, payload)
            _markets_at[name] = affected
            done.append(name)
            # ONE REGION PER TICK, for the reason games is: three region reads
            # in one slice is several gigabytes inside the serving process.
            return {"built": done, "affected": affected}
    finally:
        panel.close()
    return {"note": "every region's markets story is current", "affected": affected}


# ── the desk's AI narrative (core/reasoning/narrative.py) ───────────────────
#
# Generated here, in the loop, so a page load never waits on a model, and
# persisted per snapshot beside the numbers (Postgres `narratives`). ONE
# generation per tick — a model call is the most expensive thing in the loop —
# and only when the packet's fingerprint has moved, so a routine tick that finds
# every surface current costs one Postgres read per surface and no tokens. Dark
# without OPENAI_API_KEY: the surfaces serve their deterministic prose.

#: How many top-ranked pairs per region get a dyad-level narrative (the game and
#: the relationship). Region-level surfaces (markets, the region map) are always
#: covered; dyad prose is bounded because there are many pairs and each is a call.
NARRATE_TOP_DYADS = int(os.getenv("GEOGRAPH_NARRATE_TOP_DYADS", "4"))


def _narrate_worklist(panel: Any, conn: Any, name: str) -> list[dict[str, Any]]:
    """(surface, subject_id, payload) triples for one region, region-level
    surfaces first, then the top pairs. A payload of None is skipped."""
    from core.games import scenarios
    from core.panel import pg_store

    work: list[dict[str, Any]] = []
    story = pg_store.market_story(panel, name)
    if story is not None:
        work.append({"surface": "markets", "subject": "", "payload": story})
    net = _network_compact(conn, name)
    if net is not None:
        work.append({"surface": "network", "subject": "", "payload": net})
    region_map = pg_store.game_solution(
        panel, name, scope="region", version=scenarios.PAYLOAD_VERSION
    )
    if region_map is not None:
        # Re-derive standings before narrating so the AI packet (and thus the
        # regenerated narrative) reflects a standing correction that landed
        # without a re-solve. Fingerprint moves -> the narrate job rewrites.
        scenarios.refresh_region_standings(region_map, conn)
        work.append({"surface": "game_region", "subject": "", "payload": region_map})
        ranking = region_map.get("ranking") or []
        top = [str(r.get("dyad_id")) for r in ranking[:NARRATE_TOP_DYADS] if r.get("dyad_id")]
        for dyad in top:
            sol = pg_store.game_solution(
                panel, name, scope="dyad", dyad_id=dyad,
                version=scenarios.PAYLOAD_VERSION,
            )
            if sol is None:
                continue
            scenarios.refresh_dyad_standing(sol, conn, dyad)
            work.append({"surface": "game_dyad", "subject": dyad, "payload": sol})
            work.append({
                "surface": "relationship", "subject": dyad,
                "payload": _relationship_bundle(conn, panel, name, dyad, sol),
            })
    return work


def _relationship_bundle(
    conn: Any, panel: Any, name: str, dyad: str, sol: dict[str, Any]
) -> dict[str, Any]:
    """The relationship packet's inputs, assembled from what the page reads: the
    solved game (the call), the measured timeline (history) and the region
    calibration (work). Defensive — a missing piece is simply absent."""
    from core.reasoning import calibration as calibration_module
    from core.reasoning import impact as impact_module

    timeline: dict[str, Any] = {}
    try:
        timeline = impact_module.dyad_timeline(conn, dyad) or {}
    except Exception:  # noqa: BLE001 - the bundle is best-effort
        timeline = {}
    calibration: dict[str, Any] = {}
    try:
        calibration = calibration_module.cached(name) or {}
    except Exception:  # noqa: BLE001
        calibration = {}
    # The pair's own quarterly trajectory (peak / active quarters / span) so the
    # relationship History has depth beyond the timeline. Read from the cached
    # corpus table the dyads router uses, not the graph, so it stays cheap.
    series: dict[str, Any] = {}
    try:
        from core.models import panel as panel_module
        from core.wire import serving

        table = serving.table(name)
        if table is not None:
            rows = panel_module.series_for(table, dyad)
            if rows:
                active = [r for r in rows if r["intensity"] > 0.0]
                series = {
                    "peak": max(r["intensity"] for r in rows),
                    "active_quarters": len(active),
                    "span": [rows[0]["date"], rows[-1]["date"]],
                }
    except Exception:  # noqa: BLE001 - trajectory is best-effort
        series = {}
    return {
        "dyad_name": sol.get("dyad_name"),
        "solution": sol,
        "timeline": timeline,
        "calibration": calibration,
        "series": series,
    }


def _narrate_payload(
    panel: Any, conn: Any, region: str, surface: str, subject: str
) -> dict[str, Any] | None:
    """Load the current payload for one (surface, subject), used to satisfy a
    reader's viewed-pair request that is not in the standard top-N worklist."""
    from core.games import scenarios
    from core.panel import pg_store

    if surface == "markets":
        return pg_store.market_story(panel, region)
    if surface == "network":
        return _network_compact(conn, region)
    if surface == "game_region":
        region_map = pg_store.game_solution(
            panel, region, scope="region", version=scenarios.PAYLOAD_VERSION
        )
        if region_map is not None:
            scenarios.refresh_region_standings(region_map, conn)
        return region_map
    if surface in ("game_dyad", "relationship"):
        sol = pg_store.game_solution(
            panel, region, scope="dyad", dyad_id=subject,
            version=scenarios.PAYLOAD_VERSION,
        )
        if sol is None:
            return None
        scenarios.refresh_dyad_standing(sol, conn, subject)
        return sol if surface == "game_dyad" else _relationship_bundle(
            conn, panel, region, subject, sol
        )
    return None


def _generate_one(
    panel: Any, region: str, surface: str, subject: str,
    payload: Any, deadline: float,
) -> dict[str, Any] | None:
    """Generate + persist one surface's narrative if its numbers moved. Returns
    a result dict when the tick should end (wrote / error / dark / slice spent),
    or None to move on (already current, or a generation that failed validation
    and left the deterministic prose in place)."""
    from core.panel import pg_store
    from core.reasoning import narrative as narrative_module

    if not isinstance(payload, dict):
        return None
    try:
        packet = narrative_module.build_packet(surface, payload)
    except Exception:  # noqa: BLE001 - a malformed payload is not fatal
        return None
    fingerprint = narrative_module.fingerprint_of(packet)
    stored = pg_store.narrative(
        panel, region_pack=region, surface=surface, subject_id=subject
    )
    if stored is not None and stored.get("fingerprint") == fingerprint:
        return None
    if time.monotonic() >= deadline:
        return {"skipped": "slice spent"}
    try:
        composed = narrative_module.compose(surface, payload)
    except narrative_module.AgentUnavailable as exc:
        return {"skipped": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a bad generation is not fatal
        return {"surface": surface, "region": region, "subject": subject, "error": str(exc)}
    if composed is None:
        # Failed numeric/qualitative provenance (or empty). Keep the
        # deterministic prose and move on so one bad packet does not wedge.
        return None
    pg_store.record_narrative(
        panel, region_pack=region, surface=surface, subject_id=subject,
        fingerprint=fingerprint, blocks=composed["blocks"],
        evidence=composed["evidence"], model=composed["model"],
    )
    return {"wrote": surface, "region": region, "subject": subject, "model": composed["model"]}


def _network_compact(conn: Any, region: str) -> dict[str, Any] | None:
    """A lean structural snapshot for the network narrative packet: the top
    brokers and structurally central actors and the coalition count at the
    latest computed window. Roster-only, like the network endpoint."""
    from core.graph import kuzu_store

    try:
        pack = packs.load(region)
    except Exception:  # noqa: BLE001 - a broken pack is not the narrator's problem
        return None
    names = {str(a["id"]): str(a["name"]) for a in pack.actors}
    roster = set(names)
    latest = kuzu_store.query(
        conn,
        "MATCH (m:NetworkMetric) RETURN m.window_start AS s, m.window_end AS e "
        "ORDER BY m.window_end DESC LIMIT 1",
    )
    if not latest:
        return None
    ws, we = latest[0]["s"], latest[0]["e"]
    rows = kuzu_store.query(
        conn,
        "MATCH (m:NetworkMetric) WHERE m.window_start = $s AND m.window_end = $e "
        "RETURN m.subject_id AS subject_id, m.metric_name AS metric_name, "
        "m.value AS value",
        {"s": ws, "e": we},
    )

    def top(metric: str, n: int = 5) -> list[dict[str, Any]]:
        picked = [
            r for r in rows
            if r["metric_name"] == metric and r["subject_id"] in roster
            and r.get("value") is not None
        ]
        picked.sort(key=lambda r: float(r["value"]), reverse=True)
        return [{"name": names[r["subject_id"]], "value": float(r["value"])} for r in picked[:n]]

    communities = {
        int(float(r["value"])) for r in rows
        if r["metric_name"] == "community" and r["subject_id"] in roster
        and r.get("value") is not None
    }
    brokers = top("betweenness")
    if not brokers:
        return None
    return {
        "region": getattr(pack, "label", region),
        "window_start": ws, "window_end": we,
        "brokers": brokers, "eigenvector": top("eigenvector"),
        "communities": len(communities),
    }


def narrate(conn: Any, deadline: float) -> dict[str, Any]:
    """Regenerate one stale surface's narrative and persist it, viewed pairs
    first. Bounded per tick; dark (and free) without a key."""
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return {"skipped": "no OPENAI_API_KEY — narrative dark"}
    from core.panel import pg_store
    from core.reasoning import narrative as narrative_module

    panel = _panel()
    if panel is None:
        return {"skipped": "no panel"}
    try:
        pg_store.apply_schema(panel)
        names = set(_pack_names())
        # PRIORITY: what a reader has actually opened, newest first, so a pair
        # outside the top-ranked few is narrated the moment someone looks at it.
        for region, surface, subject in narrative_module.requested():
            if region not in names:
                continue
            if time.monotonic() >= deadline:
                return {"skipped": "slice spent"}
            payload = _narrate_payload(panel, conn, region, surface, subject)
            result = _generate_one(panel, region, surface, subject, payload, deadline)
            if result is not None:
                return result
        # STANDARD: region-level surfaces + the top-N ranked pairs per region.
        for name in _pack_names():
            if time.monotonic() >= deadline:
                return {"skipped": "slice spent"}
            for item in _narrate_worklist(panel, conn, name):
                result = _generate_one(
                    panel, name, item["surface"], item["subject"],
                    item["payload"], deadline,
                )
                if result is not None:
                    return result
        return {"note": "every surface's narrative is current"}
    finally:
        panel.close()


# ── the counts behind /api/stats ───────────────────────────────────────────


#: Days fetched per tick. One day is a ~9 MB download that yields ~130 rows
#: across the three lenses, so the steady state needs ONE — this bound is for
#: the cold start, where a volume that has been off for a month has thirty to
#: fetch and should not spend a whole slice doing it.
HARVEST_DAYS_PER_TICK = int(os.getenv("GEOGRAPH_HARVEST_DAYS", "6"))


@functools.lru_cache(maxsize=1)
def _committed_through(pack_names: tuple[str, ...]) -> _dt.date | None:
    """The last day the SHIPPED artifacts actually cover.

    READ FROM THE CONTENTS, not the filenames, and that distinction is the
    whole function. The obvious version takes the newest artifact's year and
    calls it complete through 31 December — which for the CURRENT year claims
    coverage the file does not have. Clamped to yesterday it then reports
    "committed through yesterday" every single day, `days_to_harvest` returns
    nothing every single day, and the harvest job is a permanent no-op that
    looks like it is working.

    So the newest year's artifact is scanned for its real maximum date. That
    is one ~1 MB gzip of ~20k rows per lens, memoised for the process, against
    a job that runs hourly.
    """
    latest: _dt.date | None = None
    for name in pack_names:
        newest: Path | None = None
        newest_year = -1
        for artifact in corpus.artifacts_for(name):
            if ".harvest." in artifact.name:
                continue
            year_text = artifact.name.replace(".tsv.gz", "").rsplit("-", 1)[-1]
            if year_text.isdigit() and int(year_text) > newest_year:
                newest_year, newest = int(year_text), artifact
        if newest is None:
            continue
        seen = _max_date_in(newest)
        if seen and (latest is None or seen > latest):
            latest = seen
    return latest


def _max_date_in(artifact: Path) -> _dt.date | None:
    """The largest SQLDATE in one artifact, or None."""
    import gzip

    best: _dt.date | None = None
    try:
        with gzip.open(artifact, "rt", encoding="latin-1") as handle:
            for line in handle:
                fields = line.split("\t", 2)
                if len(fields) < 2:
                    continue
                stamp = fields[1].strip()
                if len(stamp) != 8 or not stamp.isdigit():
                    continue
                try:
                    day = _dt.date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
                except ValueError:
                    continue
                if best is None or day > best:
                    best = day
    except OSError:
        return None
    return best


def harvest(conn: Any, deadline: float) -> dict[str, Any]:
    """Fetch GDELT's new daily exports into the volume's overlay artifacts.

    THE JOB THE LOOP WAS MISSING. Twelve jobs kept the platform current and not
    one of them could learn a new EVENT: `wire` projects artifacts that ship
    inside the image, so the archive was frozen at whatever was last committed
    and every other job re-derived from that snapshot. This is the only job
    that reaches the network for new facts.

    It takes NO graph lock and writes no graph — it only downloads, screens and
    appends files, so it cannot block a reader or damage the volume's database.
    The `wire` job picks the new rows up on the next process start, when the
    corpus re-parses.

    Bounded by days rather than by time, because the unit of work here is one
    archive and a partially-written day must not be marked done. The marker
    advances only over days actually fetched, so an interrupted tick costs the
    day it was in.
    """
    del conn  # deliberately no graph access
    if snapshot.frozen():
        return {
            "skipped": "snapshot frozen — live intake is GDELT 2.0",
            "weights": "corpus",
        }
    out = harvest_module.harvest_dir()
    if out is None:
        return {"skipped": "GEOGRAPH_HARVEST_DIR is not set — harvesting is off"}

    names = _pack_names()
    if not names:
        return {"skipped": "no packs"}

    today = _dt.date.today()
    days = harvest_module.days_to_harvest(
        through=harvest_module.harvested_through(out),
        committed_through=_committed_through(tuple(names)),
        today=today,
        limit=HARVEST_DAYS_PER_TICK,
    )
    if not days:
        return {"note": "the archive is current", "through": str(
            harvest_module.harvested_through(out) or _committed_through(tuple(names)))}

    lenses = []
    for name in names:
        pack = packs.load(name)
        roster = {
            a["iso3"]: {"node_id": a["id"], "name": a["name"]}
            for a in pack.actors if a.get("iso3")
        }
        if roster:
            lenses.append((pack, roster))
    if not lenses:
        return {"skipped": "no iso3-coded actors in any pack"}

    kept: dict[str, int] = {name: 0 for name, _ in ((p.name, r) for p, r in lenses)}
    fetched = 0
    for day in days:
        written = harvest_module.append_day(day, lenses=lenses, out_dir=out)
        for pack_name, count in written.items():
            kept[pack_name] = kept.get(pack_name, 0) + count
        # Marked per DAY, so an exception on day three does not re-fetch days
        # one and two, and a day GDELT has not published yet is never marked.
        harvest_module.mark_harvested(out, day)
        fetched += 1
        if time.monotonic() >= deadline:
            break

    # THE ONE PLACE THAT BREAKS "the corpus is immutable per process".
    # Everything downstream reads `serving`'s derived tables, which are built
    # once at startup precisely because the artifacts used to be frozen inside
    # the image. Now they are not, so a day fetched here would sit on the
    # volume unread until the container happened to restart — which is days,
    # and makes a job that keeps the archive current not actually current.
    #
    # Re-warmed HERE rather than by invalidating and walking away: `table()`
    # rebuilds lazily on first read, so dropping the tables without refilling
    # them hands the ~20s re-parse to whichever user clicks next. A job has
    # the time; a page does not. Skipped entirely when nothing was written, or
    # an hourly no-op would re-parse three lenses for nothing.
    rewarmed = False
    if any(kept.values()):
        from core.wire import serving

        serving.reset()
        serving.warm()  # ends in corpus.evict(), so the raw rows do not linger
        rewarmed = True

    return {
        "days": fetched,
        "through": str(days[fetched - 1]) if fetched else None,
        "rows": kept,
        "rewarmed": rewarmed,
        "remaining_to_yesterday": max(
            0, (today - _dt.timedelta(days=1) - days[fetched - 1]).days
        ) if fetched else None,
    }


#: How stale the newest close may get before a refresh. Four days covers a
#: long weekend plus a holiday without fetching for nothing; the same number
#: the boot guard uses, because they are the same question asked twice.
PRICES_STALE_DAYS = int(os.getenv("GEOGRAPH_PRICES_STALE_DAYS", "4"))

_LOAD_PANEL_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "load_panel.py"


def prices(conn: Any, deadline: float) -> dict[str, Any]:
    """Keep the price panel current — the OTHER half of "not a snapshot".

    The `harvest` job made new EVENTS arrive; this makes new PRICES arrive, and
    without it the loop only looked live. Prices were fetched in the boot and
    nowhere else, so on a service that deploys rarely the panel drifts, and a
    drifting panel does not merely age — the study cannot MEASURE an event
    whose estimation window runs past the newest close, so the freshest events
    are exactly the ones that stay unmeasured, and the paper book marks
    positions against a price that stopped moving.

    Postgres only: `load_panel.py` writes market observations and touches no
    graph, so this takes no Kuzu lock and cannot block a reader. It runs as a
    subprocess for the same reason the boot does — it is the same script, so
    there is one price-loading path rather than two that can disagree.

    Windowed, not a reload. From a week before the newest close, upserted, so
    a refresh is seconds of yfinance rather than the full-history fetch the
    depth guard pays for.

    Intraday prints refresh even when daily closes are current: live GDELT
    2.0 measures `intraday_open_close` from those prints, and a four-day
    daily freshness check would skip them on every ordinary weekday.
    """
    del conn  # deliberately no graph access
    from core.panel import pg_store

    settings = settings_module.load()
    try:
        panel = pg_store.connect(settings)
    except pg_store.PanelUnavailable as exc:
        return {"skipped": f"no panel: {exc}"}
    try:
        with panel.cursor() as cur:
            cur.execute("SELECT max(obs_date) FROM market_observations")
            row = cur.fetchone()
        latest = row[0] if row else None
    finally:
        panel.close()
    if latest is None:
        return {"skipped": "panel is empty — the boot's depth guard owns the "
                           "first load, which is a full history, not a window"}

    today = _dt.date.today()
    stale_days = (today - latest).days
    daily: dict[str, Any]
    if stale_days <= PRICES_STALE_DAYS:
        daily = {
            "note": "daily prices are current",
            "latest": latest.isoformat(),
            "stale_days": stale_days,
        }
    else:
        start = (latest - _dt.timedelta(days=7)).isoformat()
        refreshed: list[dict[str, Any]] = []
        for name in _pack_names():
            if time.monotonic() >= deadline:
                break
            proc = subprocess.run(  # noqa: S603
                [sys.executable, str(_LOAD_PANEL_SCRIPT), name, "--start", start],
                capture_output=True, text=True,
                timeout=max(30.0, deadline - time.monotonic()),
                check=False,
            )
            refreshed.append({
                "pack": name, "ok": proc.returncode == 0,
                "error": (proc.stderr or "").strip()[-160:] if proc.returncode else None,
            })
        daily = {
            "refreshed_from": start,
            "was_stale_days": stale_days,
            "packs": refreshed,
        }
    return {**daily, "intraday": _refresh_intraday(deadline)}


def _refresh_intraday(deadline: float) -> list[dict[str, Any]]:
    """~60 days of hourly prints — the ceiling yfinance actually keeps.

    Recent events get a real `intraday_open_close` only if these rows exist.
    A miss omits the window — not a skip-row, and never a daily number
    under an intraday label.
    """
    from core.ingestion import market_data

    settings = settings_module.load()
    out: list[dict[str, Any]] = []
    for name in _pack_names():
        if time.monotonic() >= deadline:
            break
        try:
            written = market_data.load_intraday(settings, region_pack=name)
            out.append({"pack": name, "ok": True, "rows": written})
        except Exception as exc:  # noqa: BLE001 - one pack must not stop the others
            out.append({"pack": name, "ok": False, "error": str(exc)[-160:]})
    return out


def counts(conn: Any, deadline: float) -> dict[str, Any]:
    """Refill the /api/stats cache so no reader pays for twenty table scans.

    The front page and the explorer both open with those counts, and they cost
    12.4s once the archive passed a million events — a number that only grows
    as the wire lands. A job has the time; a page does not.
    """
    del deadline  # one pass, and it is short by construction
    from core.api.routers import graph as graph_router

    out = graph_router.count_tables(conn)
    return {
        "events": out["nodes"].get("Event"),
        "affected": out["edges"].get("AFFECTED"),
    }


#: Events a sensor tick may update. Each call is several graph statements
#: (effects, actors, prior estimate, write) so the cap is small; the watermark
#: makes the next tick resume.
SENSOR_EVENTS_PER_TICK = int(os.getenv("GEOGRAPH_SENSOR_EVENTS_PER_TICK", "20"))
SENSOR_WATERMARK = ".sensor-watermark"


def sensor(conn: Any, deadline: float) -> dict[str, Any]:
    """Update resolve estimates from REALIZED AFFECTED edges only.

    Spec §4: the market-as-sensor loop is powered only by measured outcomes,
    never by the model's own predictions. The library existed and was tested;
    nothing in the job loop called it, so production resolve estimates never
    moved after seeding. Watermarked by event_time so a slice is resumable
    and a re-run does not double-count (each update reads the prior mean).
    """
    from core import archive as archive_bounds
    from core.graph import kuzu_store
    from core.reasoning import sensor_loop

    graph_path = settings_module.load().kuzu_db_path
    if kuzu_store.disk_is_tight(graph_path):
        return {"stopped": "volume nearly full", "disk": kuzu_store.disk_usage(graph_path)}

    mark_path = graph_path.parent / SENSOR_WATERMARK
    last = archive_bounds.START
    if mark_path.exists():
        last = mark_path.read_text(encoding="utf-8").strip() or last

    # Spine events still carry AFFECTED. GDELT measurements live in the panel
    # after the graph copy of the wire is retired, so the corpus window after
    # the watermark is the other half of the candidate set.
    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event)-[:AFFECTED]->(:Market) "
        "WHERE e.quad_class = $quad AND e.event_time > $last "
        "RETURN e.node_id AS node_id, e.event_time AS event_time "
        "ORDER BY e.event_time, e.node_id LIMIT $limit",
        {
            "quad": "material_conflict",
            "last": last,
            "limit": SENSOR_EVENTS_PER_TICK * 12,
        },
    )
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        node_id = str(row["node_id"])
        if node_id in seen:
            continue
        seen.add(node_id)
        unique.append({"node_id": node_id, "event_time": row["event_time"]})
        if len(unique) >= SENSOR_EVENTS_PER_TICK:
            break
    panel = _panel()
    try:
        if panel is not None and len(unique) < SENSOR_EVENTS_PER_TICK:
            from core.wire import corpus as corpus_mod
            from core.wire import serving

            for name in corpus_mod.installed():
                window, _ = serving.events_window(
                    name, last, None, SENSOR_EVENTS_PER_TICK * 4,
                )
                for row in window:
                    if len(unique) >= SENSOR_EVENTS_PER_TICK:
                        break
                    if str(row.get("event_time") or "") <= last:
                        continue
                    if row.get("quad_class") != "material_conflict":
                        continue
                    node_id = str(row["node_id"])
                    if node_id in seen:
                        continue
                    seen.add(node_id)
                    unique.append({
                        "node_id": node_id, "event_time": row["event_time"],
                    })
                if len(unique) >= SENSOR_EVENTS_PER_TICK:
                    break
        unique.sort(key=lambda r: (str(r["event_time"]), str(r["node_id"])))
        unique = unique[:SENSOR_EVENTS_PER_TICK]
        if not unique:
            return {"note": "sensor loop is current", "as_of": last}

        written = 0
        scanned = 0
        newest = last
        for row in unique:
            if time.monotonic() >= deadline:
                break
            scanned += 1
            try:
                out = sensor_loop.update_from_effect(
                    conn, str(row["node_id"]), panel=panel,
                )
            except ValueError:
                out = []
            written += len(out)
            newest = str(row["event_time"])
        mark_path.write_text(newest, encoding="utf-8")
        return {"scanned": scanned, "estimates_written": written, "as_of": newest}
    finally:
        if panel is not None:
            panel.close()


#: GDELT Event nodes to DETACH DELETE per trim tick. Bound so a reader waits
#: at most one batched delete; the volume's internal pages become reusable
#: for spine writes. OS free space does not grow (Kuzu reuses pages in-file).
TRIM_EVENTS_PER_TICK = int(os.getenv("GEOGRAPH_TRIM_EVENTS", "400"))


def trim(conn: Any, deadline: float) -> dict[str, Any]:
    """Delete the graph's GDELT Event copy, a slice per tick.

    The corpus is the wire. `event_study_runs` holds the measurements.
    DETACH DELETE takes AFFECTED with the event. Spine, dyads and RELATES_TO
    stay. Deleting still needs WAL room, so a tight volume waits for reclaim.
    """
    del deadline
    from core import archive as archive_bounds
    from core.graph import kuzu_store

    path = settings_module.load().kuzu_db_path
    if kuzu_store.disk_is_tight(path):
        return {
            "skipped": "volume nearly full — reclaim first",
            "disk": kuzu_store.disk_usage(path),
        }
    deleted = archive_bounds.drop_gdelt_events(conn, limit=TRIM_EVENTS_PER_TICK)
    remaining = kuzu_store.query(
        conn,
        "MATCH (e:Event) WHERE starts_with(e.node_id, 'event:gdelt-') "
        "RETURN count(e) AS n",
    )
    left = int(remaining[0]["n"]) if remaining else 0
    if deleted:
        forget_archive()
    return {"deleted": deleted, "gdelt_events_left": left}


def reclaim(conn: Any, deadline: float) -> dict[str, Any]:
    """Free what a full volume can actually free: WAL tails, then dead panel rows.

    The study pauses at 400 MB free. Acting at 512 MB means the tails are
    gone before writers stop — the boot's reclaim only fires below 64 MB,
    which is already the crash-loop zone. Takes no graph lock.

    The panel half drops GDELT skip rows and unused windows, then VACUUMs
    so those pages can be reused. Coverage is the watermark now, so those
    skip rows are dead weight on the OTHER 5 GB volume whether or not the
    snapshot is frozen. The frozen transmission map is not rewritten.
    """
    del conn, deadline
    from core.graph import kuzu_store
    from core.panel import pg_store

    path = settings_module.load().kuzu_db_path
    usage = kuzu_store.disk_usage(path)
    graph: dict[str, Any]
    if usage is not None and usage["free"] > (512 << 20):
        graph = {"skipped": "volume has headroom", "disk": usage}
    else:
        graph = kuzu_store.reclaim_non_data(path)

    panel_out: dict[str, Any]
    panel = _panel()
    if panel is None:
        panel_out = {"skipped": "no panel"}
    else:
        try:
            panel_out = pg_store.prune_gdelt_runs(
                panel, drop_skips=True,
            )
            if int(panel_out.get("deleted") or 0) > 0:
                panel_out["vacuum"] = pg_store.vacuum_runs(panel)
        finally:
            panel.close()
    return {"graph": graph, "panel": panel_out}
