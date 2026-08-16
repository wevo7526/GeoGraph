"""The jobs themselves — what the API converges in the background.

Each is a bounded, resumable slice of something that used to happen only in a
boot. They are defined here rather than in `jobs.py` so the scheduler stays a
scheduler: cadence, deadlines, status and failure backoff live there; what the
platform actually owes itself lives here.

Order of value, which is also the order they were written:

  * `study` — the measurement backlog. The engine walks the archive with a
    per-event watermark; production had reached 2003 and ~10% of the wire
    because each pass got one 600s slice per DEPLOY. Here it gets a slice
    every ten minutes, forever, and the same watermark makes it converge.
    It is the ONE job that runs as a child process (see `study` below).
  * `games` — the region scenario maps. They read the graph and write only
    Postgres, so they were never a reason to hold anyone's lock; as a boot
    step they cost ~3.5 minutes of container downtime per re-solve, which is
    what made a PAYLOAD_VERSION bump expensive enough to skip.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
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


#: Events handed to one tick. The DEADLINE is the real bound; this only stops
#: a tick from preloading a panel span for work it will never reach — so it
#: has to be comfortably more than a slice can measure, or it becomes the
#: bound by accident. Measured 2026-08-16: a tick measured 400 events in 23.6s
#: of a 45s slice, i.e. the cap was doing the stopping and half the slice went
#: unused, against a backlog of 564,596 events.
STUDY_EVENTS_PER_TICK = int(os.getenv("GEOGRAPH_STUDY_EVENTS_PER_TICK", "4000"))

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


def forget_archive() -> None:
    """Drop the cached archive. The scheduler calls this under memory
    pressure: it is ~1.07M lean rows plus their parsed dates, the largest
    rebuildable thing this process owns after the corpus, and rebuilding it
    is one graph scan."""
    _archive_cache.update({"count": None, "events": None, "dates": None})


#: How long a study CHILD may run when one is needed, and how much graph-dark
#: time that costs. See `study` for when a child is needed at all.
STUDY_CHILD_SECONDS = float(os.getenv("GEOGRAPH_STUDY_CHILD_SECONDS", "90"))

#: Set once, for the life of the process, if an in-process AFFECTED write ever
#: dies inside Kuzu's storage. From then on the study runs as a child, which
#: costs availability but always worked.
_PREFER_CHILD = os.getenv("GEOGRAPH_STUDY_CHILD", "") == "1"

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
    """Measure the next unmeasured events, one pack at a time.

    IN-PROCESS, WITH A CHILD AS THE FALLBACK — and the order matters, because
    the child costs the graph's availability (~90s of 503 per slice) while the
    in-process path costs nothing.

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
    global _PREFER_CHILD
    if _PREFER_CHILD:
        return _study_child_plan(deadline)
    try:
        return _study_in_process(conn, deadline)
    except RuntimeError as exc:
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


def _study_in_process(conn: Any, deadline: float) -> dict[str, Any]:
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
        pending_now = [a for a in artifacts if not _marker(name, a).exists()]
        if not pending_now:
            # Marked complete — return before building the id set, which is a
            # few hundred thousand strings per pack and pure waste here.
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
        pending = pending_now
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


# ── the frozen forecasts ───────────────────────────────────────────────────


#: The archive must move by this share before the freeze is worth re-running.
#: A freeze is minutes of solving, and 5,000 new wire events do not change a
#: base rate counted over a million — but 500,000 of them do, which is exactly
#: what the wire and study jobs are adding.
FREEZE_GROWTH = 0.05

#: Event count at the last freeze, per process. The Forecast nodes carry their
#: own `generated_at`, so a restart simply re-freezes once — cheap insurance
#: against a stale call, which is the failure that matters here.
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

    settings = settings_module.load()
    done: list[dict[str, Any]] = []
    for name in _pack_names():
        was = _frozen_at.get(name)
        if was is not None and was > 0 and abs(size - was) / was < FREEZE_GROWTH:
            continue
        if time.monotonic() >= deadline:
            return {"frozen": done, "skipped": "slice spent", "archive": size}
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
        "f.boundary_statement AS boundary_statement ORDER BY f.node_id",
    )
    if not rows:
        return {"skipped": "no frozen forecasts"}

    archive_rows = forecasting.rows_from_conn(conn)
    latest = max((str(r["event_time"]) for r in archive_rows), default="")
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
        base = {k: (v if v is not None else "") for k, v in row.items()}
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

    if not updates:
        return {"note": "nothing newly scoreable", "forecasts": len(rows)}
    kuzu_store.merge_nodes(conn, "Forecast", updates)
    return {"updated": len(updates), "brier_scored": scored}


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

    done: list[str] = []
    for name in _pack_names():
        was = _backtest_at.get(name)
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


# ── the counts behind /api/stats ───────────────────────────────────────────


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
