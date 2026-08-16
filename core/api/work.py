"""The jobs themselves — what the API converges in the background.

Each is a bounded, resumable slice of something that used to happen only in a
boot. They are defined here rather than in `jobs.py` so the scheduler stays a
scheduler: cadence, deadlines, status and failure backoff live there; what the
platform actually owes itself lives here.

Order of value, which is also the order they were written:

  * `study` — the measurement backlog. The engine walks the archive with a
    per-event watermark; production had reached 2003 and ~10% of the wire
    because each pass got one 600s slice per DEPLOY. Here it gets a slice
    every few minutes, forever, and the same watermark makes it converge.
  * `games` — the region scenario maps. They read the graph and write only
    Postgres, so they were never a reason to hold anyone's lock; as a boot
    step they cost ~3.5 minutes of container downtime per re-solve, which is
    what made a PAYLOAD_VERSION bump expensive enough to skip.
"""

from __future__ import annotations

import time
from typing import Any

from core import packs
from core import settings as settings_module
from core.transmission import event_study, runner


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


# ── the measurement backlog ────────────────────────────────────────────────


#: Events handed to one tick. The bound is the deadline, not this; the cap
#: only stops a single tick from preloading a panel span for work it will
#: never reach.
STUDY_EVENTS_PER_TICK = 400

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
        events = runner.archive(conn)
        _archive_cache.update({
            "count": count,
            "events": events,
            "dates": {
                e["id"]: event_study.parse_event_date(e["date"]) for e in events
            },
        })
    return _archive_cache["events"], _archive_cache["dates"]


def study(conn: Any, deadline: float) -> dict[str, Any]:
    """Measure the next unmeasured events, one pack at a time.

    Round-robins across packs by taking whichever pack has the most left, so
    no pack is starved by alphabet — the failure mode `_run_study`'s fair-share
    comment already records from the boot era (mena, always last, inherited a
    one-second slice and measured nothing for weeks).
    """
    from core.panel import pg_store

    names = _pack_names()
    if not names:
        return {"skipped": "no packs"}
    panel = _panel()
    if panel is None:
        return {"skipped": "no panel"}
    try:
        events, all_dates = _archive(conn)
        if not events:
            return {"skipped": "empty graph"}

        backlog: list[tuple[int, str, list[dict[str, Any]], Any]] = []
        for name in names:
            pack = packs.load(name)
            curated = runner.curated_event_ids(pack)
            candidates = runner.select_all(events, curated)
            measured = pg_store.measured_events(
                panel, [m["ticker"] for m in pack.markets]
            )
            left = [e for e in candidates if e["id"] not in measured]
            backlog.append((len(left), name, left, pack))
        backlog.sort(key=lambda item: -item[0])

        remaining_total = sum(item[0] for item in backlog)
        if not remaining_total:
            return {"measured": 0, "remaining": 0, "note": "archive fully measured"}

        count, name, left, pack = backlog[0]
        if time.monotonic() >= deadline:
            return {"skipped": "no time in this slice", "remaining": remaining_total}
        outcome = runner.measure(
            conn, panel, pack, left[:STUDY_EVENTS_PER_TICK],
            all_dates=all_dates, deadline=deadline,
        )
        return {
            "pack": name,
            "measured": outcome["events"],
            "edges": outcome["edges"],
            "remaining_in_pack": count - outcome["events"],
            "remaining_total": remaining_total - outcome["events"],
            "backlog": {n: c for c, n, _, _ in backlog},
        }
    finally:
        panel.close()


# ── the region scenario maps ───────────────────────────────────────────────


def games(conn: Any, deadline: float) -> dict[str, Any]:
    """Re-solve a region whose persisted map is stale, one region per tick.

    STALE MEANS THREE THINGS, all of them cheap to check: no persisted row, a
    row of a different `PAYLOAD_VERSION` (the reader would reject it and the
    endpoint would solve live on every request), or a row older than the
    archive's own as_of. Nothing else triggers a solve — a re-solve costs ~70s
    and produces the same numbers from the same inputs.
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
        for name in _pack_names():
            if time.monotonic() >= deadline:
                return {"solved": solved_now, "skipped": "slice spent"}
            stored = pg_store.game_solution(
                panel, name, scope="region", version=scenarios.PAYLOAD_VERSION
            )
            if stored is not None:
                continue
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
            written = pg_store.record_game_solutions(
                panel, name, solved, solver=solved["region"]["primary_solver"]
            )
            # KEEP GOING while the slice allows: a version bump leaves EVERY
            # region stale at once, and one region per tick meant the rest
            # answered "being re-solved" for as long as it took to come round.
            solved_now.append({
                "region": name, "rows": written,
                "dyads": solved["region"]["dyads_solved"],
            })
        if solved_now:
            return {"solved": solved_now, "version": scenarios.PAYLOAD_VERSION}
        return {"note": "every region's map is current"}
    finally:
        panel.close()


# ── the wire, into the graph ───────────────────────────────────────────────


#: Lines merged per write. The loader's own batch size, and the reason it is
#: batched at all: an interrupted load keeps the batches it completed, so a
#: load too slow to finish once can still finish.
WIRE_BATCH_LINES = 5_000

#: Per (pack) memo of the event ids the graph already holds. Built once —
#: it is a set of a few hundred thousand strings — and grown as this process
#: writes, because rebuilding it per tick would cost more than the loading.
_wire_seen: dict[str, set[str]] = {}
_wire_done: set[str] = set()

#: Lines scanned between deadline checks. A pass over an artifact whose events
#: are ALL already held writes nothing, so without this the tick only noticed
#: its deadline between files — measured locally at 22 artifacts and 454k
#: lines, which is minutes of holding a slice meant to last one.
_WIRE_SCAN_CHECK = 20_000


def _artifacts(pack_name: str) -> list[Any]:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "data" / "derived"
    return sorted(root.glob(f"gdelt-{pack_name}-*.tsv.gz"))


def _marker(pack_name: str, artifact: Any) -> Any:
    """THE ARTIFACT IS THE UNIT OF COMPLETENESS — the boot's own marker file,
    the same path and the same name (`scripts/boot.py::_pending_artifacts`).

    Shared deliberately: a boot that loaded an artifact and a job that loaded
    it are the same fact, and the marker lives beside the graph so a rebuilt
    volume loses both together. Comparing event counts instead looks airtight
    and is not — the same GDELT id appears in more than one lens's artifacts,
    so a lens can never reach its own count and the load never ends.
    """
    from core import settings as settings_module

    root = settings_module.load().kuzu_db_path.parent / ".gdelt-loaded"
    return root / f"{pack_name}-{artifact.stem}.done"


def _mark_loaded(pack_name: str, artifact: Any) -> None:
    marker = _marker(pack_name, artifact)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(artifact.name, encoding="utf-8")
    except OSError:  # a read-only volume must not fail the loop
        pass


def wire(conn: Any, deadline: float) -> dict[str, Any]:
    """Merge the committed GDELT artifacts into the graph, a batch at a time.

    THE GAP THIS CLOSES. The wire's read path is the corpus (a pure function of
    the artifacts in the image), so the graph copy exists for one reason: the
    transmission engine measures events IN THE GRAPH, and effects are the
    market half of every surface. After the 2026-08-15 volume rebuild the load
    ran alphabetically and never reached mena, so the flagship region held 351
    graph events on its roster dyads against china's 340,784 — and its measured
    effects were the deep tier alone.

    Reloading it was a downtime decision (~1h of graph-dark) for as long as
    bulk writes could only happen in a boot. Here it is a background slice:
    events already in the graph are skipped, batches commit as they go, and the
    deadline stops the tick.

    Escalation fields are NOT scored here — Head B folds per dyad in time order
    across the whole archive, which is an archive-wide pass and belongs in its
    own job. Measurement does not read them (the study selects on goldstein and
    date), so the market half converges without waiting for it.
    """
    import gzip

    from core.graph import kuzu_store
    from core.ingestion import gdelt

    names = [n for n in _pack_names() if n not in _wire_done]
    if not names:
        return {"note": "every pack's wire is loaded"}

    for name in names:
        artifacts = _artifacts(name)
        if not artifacts:
            _wire_done.add(name)
            continue
        pack = packs.load(name)
        seen = _wire_seen.get(name)
        if seen is None:
            rows = kuzu_store.query(
                conn,
                "MATCH (e:Event) WHERE e.region_pack = $pack "
                "AND starts_with(e.node_id, 'event:gdelt-') "
                "RETURN e.node_id AS id",
                {"pack": name},
            )
            seen = {str(r["id"]) for r in rows}
            _wire_seen[name] = seen
        actors_by_iso3 = {
            str(a["iso3"]): {"node_id": a["id"], "name": a["name"]}
            for a in pack.actors if a.get("iso3")
        }

        written = 0
        pending = [a for a in artifacts if not _marker(name, a).exists()]
        if not pending:
            _wire_done.add(name)
            continue
        for artifact in pending:
            if time.monotonic() >= deadline:
                return {"pack": name, "written": written, "held": len(seen),
                        "artifacts_left": len(pending), "stopped_early": True}
            batch: list[str] = []
            scanned = 0
            with gzip.open(artifact, "rt", encoding="latin-1") as fh:
                for line in fh:
                    scanned += 1
                    if scanned % _WIRE_SCAN_CHECK == 0 and time.monotonic() >= deadline:
                        # Stop mid-artifact WITHOUT a marker: the ids already
                        # written are skipped next tick, so resuming costs a
                        # scan and never a re-merge.
                        if batch:
                            written += _merge_wire_batch(
                                conn, gdelt, batch, pack, actors_by_iso3, seen
                            )
                        return {"pack": name, "written": written,
                                "held": len(seen), "artifact": artifact.name,
                                "artifacts_left": len(pending),
                                "stopped_early": True}
                    event_id = f"event:gdelt-{line.split(chr(9), 1)[0]}"
                    if event_id in seen:
                        continue
                    batch.append(line)
                    if len(batch) < WIRE_BATCH_LINES:
                        continue
                    written += _merge_wire_batch(
                        conn, gdelt, batch, pack, actors_by_iso3, seen
                    )
                    batch = []
            if batch:
                written += _merge_wire_batch(
                    conn, gdelt, batch, pack, actors_by_iso3, seen
                )
            # A COMPLETE PASS is what earns the marker, whether it wrote a
            # hundred thousand events or none: an artifact whose events are
            # all held is loaded, and re-reading it every tick is how a
            # resumable loader stops being cheap.
            _mark_loaded(name, artifact)
        return {"pack": name, "written": written, "held": len(seen),
                "artifacts": len(pending)}
    return {"note": "every pack's wire is loaded"}


def _merge_wire_batch(
    conn: Any, gdelt: Any, batch: list[str], pack: Any,
    actors_by_iso3: dict[str, dict[str, Any]], seen: set[str],
) -> int:
    events, edges, result = gdelt.parse_lines(
        batch,
        actors_by_iso3=actors_by_iso3,
        region_pack=pack.name,
        external_powers=pack.external_powers,
    )
    gdelt.write_events(conn, events, edges)
    # The memo grows with what we wrote AND with what the parser dropped: a
    # line that fails the roster or the mention floor will fail it again next
    # tick, and re-reading it forever is how a resumable loader stops resuming.
    for line in batch:
        seen.add(f"event:gdelt-{line.split(chr(9), 1)[0]}")
    return int(result.written)


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

    rows = forecasting.all_dyad_event_rows(settings_module.load().kuzu_db_path)
    size = len(rows)
    done: list[str] = []
    for name in _pack_names():
        if calibration_module.is_current(name, size):
            continue
        if time.monotonic() >= deadline:
            return {"computed": done, "note": "slice spent", "archive_rows": size}
        calibration_module.remember(
            name, size, calibration_module.walk(rows, region_pack=name)
        )
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
