#!/usr/bin/env python3
"""Seed the stores, then exec the API. The container's boot sequence.

WHY THIS EXISTS. Kuzu is single-writer, and the API process holds the write
lock for as long as it serves — so there is no moment *after* startup when the
graph can be seeded. Seeding has to happen before the API exists, and a
Railway volume starts empty, so without this step a fresh deployment serves an
empty graph forever and every endpoint honestly reports nothing.

THE SEED RUNS IN A CHILD PROCESS, deliberately. The child's exit is what
guarantees Kuzu has released the write lock before the API tries to take it —
`os.execvp` replaces the process image but does not promise to drop a lock the
old image was holding. A child that has exited holds nothing.

A FAILED SEED MUST NOT STOP THE CONTAINER. This is the same rule as
`/api/health` returning 200 over an empty graph: a boot that refuses to serve
because its data is wrong turns one bad pack into a restart loop, and a
restart loop tells you far less than a running API that reports what broke.
So every failure is recorded into GEOGRAPH_BOOT_STATUS, which the health
endpoint surfaces, and the API starts regardless.

  GEOGRAPH_SEED_ON_BOOT=0     skip seeding (a batch job needs the write lock)
  GEOGRAPH_SEED_PACKS=mena    which packs to seed; default is every pack that
                              satisfies the contract, so packs/china seeds
                              itself the day it becomes complete
  GEOGRAPH_LOAD_PANEL=0       never fetch prices at boot
  GEOGRAPH_STUDY_ON_BOOT=0    never run the transmission engine at boot
  GEOGRAPH_BACKTEST_ON_BOOT=0 never run the walk-forward paper backtest

The price fetch is CONDITIONAL on the panel being empty, because it is the one
step that reaches the network and it does not need repeating: Postgres survives
a redeploy, so a loaded panel stays loaded. The event study is not conditional
— it is pure computation over the panel, and the graph it writes into lives on
a volume that may have been rebuilt, so it runs every boot and re-derives the
same numbers.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_SEED_SCRIPT = _ROOT / "scripts" / "seed_pack.py"
_LOAD_PANEL_SCRIPT = _ROOT / "scripts" / "load_panel.py"
_STUDY_SCRIPT = _ROOT / "scripts" / "run_event_study.py"
_METRICS_SCRIPT = _ROOT / "scripts" / "run_network_metrics.py"
_FORECASTS_SCRIPT = _ROOT / "scripts" / "run_forecasts.py"
_GDELT_SCRIPT = _ROOT / "scripts" / "backfill_gdelt.py"
_BACKTEST_SCRIPT = _ROOT / "scripts" / "run_backtest.py"
_SCORE_SCRIPT = _ROOT / "scripts" / "score_forecasts.py"
_DERIVED_DIR = _ROOT / "data" / "derived"
_DEEP_TIER_SCRIPT = _ROOT / "scripts" / "load_deep_tier.py"
_LOAD_13F_SCRIPT = _ROOT / "scripts" / "load_13f.py"

#: What to exec when no command is given. Railway can override by setting a
#: start command; the default is the app.
_DEFAULT_APP = ["python", "-m", "core.api.app"]

#: A seed of the MENA pack takes ~1s. This ceiling exists so a hung driver
#: cannot hold the boot open indefinitely — the API is more useful than the
#: seed that was trying to fill it.
_SEED_TIMEOUT_SECONDS = 300

#: The price fetch talks to yfinance for every market, so it gets a longer
#: ceiling than the seed — but still a ceiling.
_LOAD_TIMEOUT_SECONDS = 900

#: The GDELT artifact load gets its OWN, much larger ceiling: it is ~100k
#: MERGEs into a graph that is already large, it happens ONCE per lens, and
#: an interrupted load is the expensive failure (the 2026-08-12 Eurasia
#: deploy hit the 900s price fetch ceiling partway through 95,070 events).
#: Every later boot skips in milliseconds, so this ceiling is paid once.
_GDELT_TIMEOUT_SECONDS = 2400

#: …but SHARED ACROSS PACKS, not paid per pack. A per-pack ceiling multiplies
#: by the number of regions, and three packs at 2400s each is 7200s of
#: pre-API work against a 5400s healthcheck window — the boot would be killed
#: for being thorough. The budget bounds the whole step instead, so adding a
#: fourth region cannot push the container past its own health check. Running
#: out of budget leaves the remaining lens short, which is REPORTED (the
#: count check below) rather than silently accepted, and the API still comes
#: up — a served archive that names its gap beats a dead container.
_GDELT_BUDGET_SECONDS = int(os.getenv("GEOGRAPH_GDELT_BUDGET", "5400"))

#: The archive-wide rescore's own ceiling, separate and much larger because it
#: is the one step here that CANNOT be resumed. Head B folds escalation per
#: dyad across every era at once; an interrupted run leaves nothing behind and
#: the next boot starts from the beginning, so a timeout that cuts it short
#: does not slow the archive down, it stops it converging at all.
#:
#: Sized from measurement AND bounded by the window it runs in. 456,711 events
#: rescored locally in ~29 minutes, about 262/sec; production will hold ~1.5M
#: across three lenses on slower hardware. It runs on a boot where loading is
#: already complete, so GDELT contributes nothing — but the other steps still
#: want ~1,975s, and 10800 − 1975 leaves 8,825. Taking 7800 keeps ~1,000s of
#: margin.
#:
#: It may well NOT FINISH in that on the first attempt, and that is survivable
#: precisely because the trigger is "are there unscored events" rather than
#: "did the archive grow": an interrupted rescore leaves them unscored, so the
#: next boot tries again and keeps trying until it converges. A count-based
#: trigger would have skipped forever after one timeout.
_RESCORE_TIMEOUT_SECONDS = int(os.getenv("GEOGRAPH_RESCORE_TIMEOUT", "7800"))

#: Below this the rescore declines rather than starts. It cannot be resumed,
#: so a pass that gets cut off produces nothing at all — and unlike the study,
#: "some progress" is not one of its outcomes. Measured at ~262 events/sec on
#: 456,711 events; 1800s is enough to be worth the attempt on a lens-sized
#: archive and short enough that declining is rare.
_RESCORE_MIN_SECONDS = 1800

#: SIZED AGAINST THE HEALTHCHECK WINDOW, and the arithmetic is the whole story.
#: Two deploys failed on 2026-08-13 — health checks giving up after 138
#: attempts — because the boot's worst case was 5,575s against a 5400s window:
#:
#:     seed 20 + deep tier 90 + GDELT + 13f 10 + study 1500
#:     + metrics 90 + forecasts 90 + score 25 + backtest 150
#:
#: With the window at 10800s (railway.json) everything except GDELT accounts
#: for ~4,200s once the study's own budget is counted properly, so this takes
#: 5400 and the worst case lands near 9,600 with ~1,200s of margin. Measured
#: on the 2026-08-13 boot: eurasia's remaining artifacts took ~1,750s and
#: mena's ~4,800s, so this is sized to finish a lens per boot rather than to
#: swallow the whole archive at once.
#:
#: The first boots still run out of budget and stop partway, on purpose — that
#: is the resumable design working: the loader skips ids already present, the
#: completeness check counts DISTINCT ids across every artifact, and each boot
#: resumes where the last stopped. What must not happen is this growing past
#: the window and killing a container that was working, which is what both
#: failed deploys did.

#: The full-archive study's ceiling PER PACK, and its budget ACROSS packs.
#: With the measured-events watermark only NEW events pay compute, so a normal
#: boot uses seconds of both — the ceilings exist for the first boots after a
#: large backfill, when there are a million unmeasured events.
#:
#: The budget is the lesson of 2026-08-13, when a deploy died at 209
#: health-check attempts. The per-pack ceiling was 1500s and the window
#: arithmetic counted 1500s total; three regions can spend 4,500s, and china
#: alone burned its full share before eurasia and mena had started. Any step
#: whose cost multiplies with the number of regions needs a budget, or adding
#: a fourth lens silently breaks the boot.
_STUDY_TIMEOUT_SECONDS = 1200

#: WHEN THIS BOOT STARTED. Every budget below is derived from this and the
#: window, rather than fixed in advance, and that is the point.
#:
#: Two deploys died on 2026-08-13 (138 and 209 health-check attempts) because
#: hand-arithmetic over fixed budgets was wrong — first about the window, then
#: about a ceiling that multiplies per pack. Fixed budgets encode a PREDICTION
#: of how long earlier steps take, and the prediction is what keeps being
#: wrong. Reading the clock cannot be wrong: whatever GDELT actually spends,
#: the rescore sees the truth, and whatever the rescore spends, the study sees
#: the truth.
#:
#: The second reason is throughput, which is why this landed tonight. A fixed
#: slice makes a boot stop early with an hour of its window unused, and
#: deferred work does not drain on its own — the boot runs ONCE per deploy, so
#: every deferral waits for a human to redeploy. Spending the whole window is
#: the difference between converging in one deploy and converging in three.
_BOOT_STARTED = time.monotonic()

#: The healthcheck window this boot must finish inside. Must agree with
#: railway.json's `healthcheckTimeout` — `test_boot_window_matches_railway_json`
#: is what keeps the two from drifting apart.
_WINDOW_SECONDS = int(os.getenv("GEOGRAPH_BOOT_WINDOW", "14400"))

#: What the steps AFTER the study need: metrics, forecasts, scores, backtest.
#: Measured at ~320s across the 2026-08-13 boots; 600 leaves margin for a
#: larger archive. This is the ONLY estimate left in the budget arithmetic.
_TAIL_RESERVE_SECONDS = 600

#: Never begin the study with less than this — a 90-second study measures a
#: handful of events and spends its startup cost for nothing. Below this the
#: step declines and says so, which is honest and costs the same.
_STUDY_MIN_SECONDS = 600

#: How far before the spine's EARLIEST event the panel must reach: the
#: estimation window (120 sessions) plus its gap and the measurement windows,
#: with slack for weekends and holidays. Matches run_event_study._LOOKBACK_DAYS.
_LOOKBACK_DAYS = 400


def _log(message: str) -> None:
    print(f"boot: {message}", flush=True)


def _remaining(reserve: float) -> float:
    """Seconds this boot can still spend, leaving `reserve` for what follows.

    The one primitive the budgeted steps share. `reserve` is what MUST still
    happen after the caller returns — never what might be nice to have — so a
    step that takes everything `_remaining` offers still leaves a boot that
    binds.
    """
    return max(0.0, _WINDOW_SECONDS - (time.monotonic() - _BOOT_STARTED) - reserve)


def _disabled() -> bool:
    return os.getenv("GEOGRAPH_SEED_ON_BOOT", "1").strip().lower() in {"0", "false", "no"}


def _pack_names() -> list[str]:
    configured = os.getenv("GEOGRAPH_SEED_PACKS", "").strip()
    if configured:
        return [name.strip() for name in configured.split(",") if name.strip()]
    # Import here, not at module scope: a broken pack must not stop the boot
    # before the status machinery below exists to report it.
    from core import packs

    return packs.available()


def _run_step(
    label: str, argv: list[str], *, timeout: int, echo: bool = True
) -> dict[str, Any]:
    """One boot step, in a CHILD PROCESS. Never raises — reports.

    The child boundary is load-bearing for anything that writes the graph: its
    exit is what releases Kuzu's single-writer lock before the API takes it.
    """
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        _log(f"{label}: TIMED OUT after {timeout}s")
        return {"step": label, "ok": False, "error": f"timed out after {timeout}s"}
    except OSError as exc:
        _log(f"{label}: could not start — {exc}")
        return {"step": label, "ok": False, "error": str(exc)}

    elapsed = round(time.monotonic() - started, 2)
    if echo:
        for line in (proc.stdout or "").splitlines():
            _log(f"  {line}")
    if proc.returncode == 0:
        _log(f"{label}: ok in {elapsed}s")
        return {"step": label, "ok": True, "seconds": elapsed}

    # The child's own message is the useful part — it names the file and the
    # rule. Keep it whole rather than summarising it away.
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    _log(f"{label}: FAILED — {' / '.join(detail[-3:]) or 'no output'}")
    return {"step": label, "ok": False, "error": "\n".join(detail[-8:])}


def _seed_one(name: str) -> dict[str, Any]:
    result = _run_step(
        f"seed {name}",
        [sys.executable, str(_SEED_SCRIPT), name],
        timeout=_SEED_TIMEOUT_SECONDS,
    )
    return {"pack": name, **{k: v for k, v in result.items() if k != "step"}}


def _panel_first_observation() -> tuple[bool, str | None]:
    """(reachable, earliest obs_date as ISO string or None when empty)."""
    from core import settings as settings_module
    from core.panel import pg_store

    settings = settings_module.load()
    if not settings.database_url:
        return False, None
    try:
        conn = pg_store.connect(settings)
    except pg_store.PanelUnavailable:
        return False, None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT min(obs_date) FROM market_observations")
            row = cur.fetchone()
        first = row[0] if row else None
        return True, first.isoformat() if first is not None else None
    finally:
        conn.close()


def _panel_is_empty() -> bool | None:
    """Does the panel hold any observation at all? None if it cannot be asked."""
    reachable, first = _panel_first_observation()
    if not reachable:
        return None
    return first is None


def _spine_needs_from(pack_names: list[str]) -> str | None:
    """The panel depth the SPINE requires: the earliest marquee event across
    the packs, minus the estimation lookback. None if no pack loads."""
    import datetime as dt

    from core import packs

    earliest: dt.date | None = None
    for name in pack_names:
        try:
            pack = packs.load(name)
        except Exception:  # noqa: BLE001 - a broken pack is the seed step's report
            continue
        for event in pack.marquee_events:
            date = dt.date.fromisoformat(str(event["date"])[:10])
            if earliest is None or date < earliest:
                earliest = date
    if earliest is None:
        return None
    return (earliest - dt.timedelta(days=_LOOKBACK_DAYS)).isoformat()


def _load_panel_if_shallow(pack_names: list[str]) -> dict[str, Any] | None:
    """Fetch prices when the panel cannot serve the whole spine.

    Postgres survives a redeploy, so the yfinance call is not repeated on
    every boot — but "holds some rows" is not the test that matters. Phase 1
    measures the WHOLE spine, so the guard is DEPTH: the panel must reach the
    estimation window of the earliest marquee event. Loading without --start
    pulls each market from its own inception (the full history), and every
    row is an upsert, so a deepening reload converges instead of duplicating.
    """
    if os.getenv("GEOGRAPH_LOAD_PANEL", "1").strip().lower() in {"0", "false", "no"}:
        return {"ok": True, "skipped": "disabled by GEOGRAPH_LOAD_PANEL"}
    reachable, first = _panel_first_observation()
    if not reachable:
        return None
    needed = _spine_needs_from(pack_names)
    if first is not None and (needed is None or first <= needed):
        _log(f"panel reaches {first} — deep enough for the spine ({needed})")
        return {"ok": True, "skipped": f"panel reaches {first}"}
    if first is not None:
        _log(f"panel starts {first} but the spine needs {needed} — deepening")
    results = [
        _run_step(
            f"load panel {name}",
            [sys.executable, str(_LOAD_PANEL_SCRIPT), name],
            timeout=_LOAD_TIMEOUT_SECONDS,
        )
        for name in pack_names
    ]
    return {"ok": all(r["ok"] for r in results), "packs": results}


def _run_study(pack_names: list[str]) -> dict[str, Any] | None:
    """The transmission engine over the WHOLE spine (Phase 1, build-spec §18).

    Runs every boot, unconditionally: it is pure computation over the panel,
    and the graph it writes into lives on a volume that may have been rebuilt.
    Re-deriving the same numbers is the cheapest way to keep the two stores
    consistent — and the engine being deterministic is what makes that safe.
    --all measures every marquee event; a market with no data at an event's
    date is a recorded skip, not an error, so the deep spine costs nothing
    but honesty.
    """
    if os.getenv("GEOGRAPH_STUDY_ON_BOOT", "1").strip().lower() in {"0", "false", "no"}:
        return {"ok": True, "skipped": "disabled by GEOGRAPH_STUDY_ON_BOOT"}
    if _panel_is_empty() is not False:
        _log("panel has no observations — nothing for the engine to measure")
        return {"ok": True, "skipped": "panel empty"}
    # A SHARED BUDGET, not a ceiling paid per pack — the same lesson GDELT's
    # own budget comment already records, which this step did not get. With a
    # per-pack timeout of 1500s three regions can spend 4,500s, and the boot
    # arithmetic that sized the healthcheck window counted 1,500. The deploy
    # of 2026-08-13 died at 209 attempts because of exactly that gap: china
    # alone burned its full 1500s and eurasia and mena were still to come.
    #
    # Running out is safe and expected: the engine works off a measured-events
    # watermark, so a truncated pass costs nothing and the next boot resumes
    # where it stopped. What is NOT safe is a step whose cost multiplies with
    # the number of regions inside a fixed window.
    #
    # THE STUDY IS LAST AMONG THE EXPENSIVE STEPS, so it takes every second the
    # window has left rather than a fixed slice. It is the right step to hand
    # the remainder to: it is the most resumable thing the boot does — a
    # watermark, per event — so an extra forty minutes here is forty minutes of
    # permanent progress, and stopping mid-pass costs nothing.
    results = []
    budget = _remaining(_TAIL_RESERVE_SECONDS)
    if budget < _STUDY_MIN_SECONDS:
        _log(f"event study: {int(budget)}s left in the window — too little to "
             f"be worth starting, deferred")
        return {"ok": True, "skipped": "window spent", "remaining": int(budget)}
    _log(f"event study: {int(budget)}s of window remaining, "
         f"{int(_STUDY_TIMEOUT_SECONDS)}s ceiling per pack")
    for name in pack_names:
        if budget <= 0:
            _log(f"event study {name}: out of budget — deferred to the next boot")
            results.append({"pack": name, "ok": True, "skipped": "study budget spent"})
            continue
        started = time.monotonic()
        results.append({
            "pack": name,
            **_run_step(
                f"event study {name}",
                [sys.executable, str(_STUDY_SCRIPT), name, "--all"],
                timeout=int(min(_STUDY_TIMEOUT_SECONDS, budget)),
                echo=False,
            ),
        })
        budget -= time.monotonic() - started
    return {"ok": all(r["ok"] for r in results), "packs": results}


def _load_deep_tier() -> dict[str, Any]:
    """The 1905+ deep tier (Phase 3): COW into the graph, Shiller into the
    panel, Head B rescored over the whole archive.

    Loaders are idempotent and the raw downloads cache on the volume
    (GEOGRAPH_RAW_DIR), so a boot with everything in place costs a few
    seconds of MERGE and one rescore. Runs BEFORE the study so deep events
    are there to measure, and before metrics so the deep network is scored.
    """
    if os.getenv("GEOGRAPH_DEEP_TIER", "1").strip().lower() in {"0", "false", "no"}:
        return {"ok": True, "skipped": "disabled by GEOGRAPH_DEEP_TIER"}
    # --skip-rescore, and this is what keeps a ROUTINE DEPLOY from costing
    # fifteen minutes. The deep-tier loaders are idempotent merges that cost
    # seconds once the volume has the data; the archive-wide rescore they used
    # to end with is what made this step take 643s on a 267k archive, and on
    # 1.5M it would exceed this timeout on EVERY deploy — burning the window,
    # failing the step, and leaving the scores no fresher than before. The
    # boot runs one rescore itself, after all loading is complete.
    result = _run_step(
        "deep tier",
        [sys.executable, str(_DEEP_TIER_SCRIPT), "--skip-rescore"],
        timeout=_LOAD_TIMEOUT_SECONDS,
        echo=False,
    )
    return {k: v for k, v in result.items() if k != "step"}


def _expected_events(artifacts: list[Path]) -> int:
    """DISTINCT event ids across a lens's artifacts, not the sum of their lines.

    Summing lines overcounts, and the overcount is fatal rather than cosmetic:
    GDELT's own ids recur across the year-boundary files, so mena's twenty-one
    artifacts hold 454,539 lines for 454,531 distinct events. Compared against
    a line-count total the graph is permanently EIGHT events short — the
    completeness check never passes, every boot re-attempts a finished load,
    and the rescore that waits on completeness never runs at all.

    Costs one pass over a few megabytes of gzip and a set of short strings.
    """
    seen: set[str] = set()
    for artifact in artifacts:
        with gzip.open(artifact, "rt", encoding="latin-1") as fh:
            for line in fh:
                seen.add(line.split("\t", 1)[0])
    return len(seen)


def _artifact_events(artifact: Path) -> int:
    """How many events the shipped artifact holds — one line each.

    The artifact was written by `parse_lines` with `keep_lines`, so it holds
    ONLY lines that already passed the filter: its line count IS the number
    of events a complete load produces. Counting costs ~0.1s on a 3 MB gz,
    which is what makes the completeness check below affordable every boot.
    """
    with gzip.open(artifact, "rb") as fh:
        return sum(1 for _ in fh)


def _loaded_dir() -> Path:
    """Where per-artifact load markers live — on the VOLUME, beside the graph.

    Boot bookkeeping, deliberately NOT a node class: the LinkML ontology is the
    source of truth for facts about the WORLD, and "this container finished
    loading gdelt-mena-2014" is a fact about this container. Beside the graph
    because the two must share a lifetime — a rebuilt volume loses both
    together, which is what the stale-marker guard in `_load_gdelt` relies on.
    """
    from core import settings as settings_module

    return settings_module.load().kuzu_db_path.parent / ".gdelt-loaded"


def _pending_artifacts(pack_name: str, artifacts: list[Path]) -> list[Path]:
    """The artifacts of this lens that have not yet loaded clean.

    THE ARTIFACT IS THE UNIT OF COMPLETENESS, NOT THE EVENT COUNT — and that
    correction is the whole reason this function exists. Comparing the graph's
    per-lens count against the artifacts' distinct ids looks airtight and is
    not, because `region_pack` is a property of the NODE and a GDELT id can
    appear in more than one lens's artifacts. Since `external_powers` moved
    onto the pack, every lens harvests the USA/RUS dyads, so the same wire
    events are written once and carry whichever lens merged them first
    (packs seed alphabetically, so: china). The 2026-08-14 boot measured the
    consequence exactly — eurasia 512,806/534,534 and mena 432,803/454,531,
    short by an IDENTICAL 21,728 — and the identity across two independent
    lenses is the tell that this is a shared set, not a partial load.

    Cost of believing the count: completeness is unreachable for every lens but
    the alphabetically-first one, so every boot re-merges a finished archive
    for ~13 minutes and the rescore that waits on completeness NEVER RUNS.

    A marker is also correct under the other explanation for a shortfall — a
    loader that legitimately drops records it cannot code. Dropping and
    counting is the archive's rule; a load that dropped is still a load that
    finished, and only the artifact knows that. Counts stay, as a diagnostic.
    """
    marker_dir = _loaded_dir()
    return [a for a in artifacts
            if not (marker_dir / f"{pack_name}-{a.stem}.done").exists()]


def _mark_artifact_loaded(pack_name: str, artifact: Path) -> None:
    """Record that this artifact loaded clean. Best-effort by design."""
    marker_dir = _loaded_dir()
    try:
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / f"{pack_name}-{artifact.stem}.done").write_text(
            artifact.name, encoding="utf-8")
    except OSError as exc:  # a read-only volume must not fail the boot
        _log(f"could not mark {artifact.name} loaded: {exc}")


def _clear_markers(pack_name: str) -> None:
    """Forget this lens's markers — the graph they described is gone."""
    for marker in _loaded_dir().glob(f"{pack_name}-*.done"):
        with contextlib.suppress(OSError):  # best effort, one file at a time
            marker.unlink()


def _graph_gdelt_count(pack_name: str) -> int | None:
    """How many GDELT events the graph holds for THIS lens. None if unaskable."""
    from core import settings as settings_module
    from core.graph import kuzu_store

    settings = settings_module.load()
    if not settings.kuzu_db_path.exists():
        return 0
    try:
        conn = kuzu_store.connect(settings.kuzu_db_path, read_only=True)
    except kuzu_store.GraphUnavailable:
        return None
    try:
        rows = kuzu_store.query(
            conn,
            "MATCH (e:Event) WHERE e.node_id STARTS WITH 'event:gdelt-' "
            "AND e.region_pack = $pack RETURN count(e) AS n",
            {"pack": pack_name},
        )
        return int(rows[0]["n"]) if rows else 0
    finally:
        kuzu_store.close(conn)


def _load_gdelt(pack_names: list[str]) -> dict[str, Any]:
    """The GDELT backfill from the SHIPPED ARTIFACTS, one per region lens
    (Phase 4, credential-free). The image carries the filtered lines
    (data/derived/gdelt-<pack>-*.tsv.gz); loading is minutes ONCE per pack —
    the volume graph keeps the events, so every later boot skips in
    milliseconds.

    THE SKIP IS A COUNT, NOT AN EXISTENCE CHECK, and the difference is a bug
    the 2026-08-12 Eurasia deploy paid for. The test used to be "does the
    graph hold ANY event for this lens" (LIMIT 1), so when the load hit its
    ceiling partway through 95,070 events, the partial result satisfied the
    check and every later boot skipped the rest — the lens would have stayed
    silently short forever, which is exactly the failure mode the archive's
    honesty rules exist to prevent. Comparing against the artifact's own line
    count makes an incomplete load visible and self-healing: the loader skips
    ids already present, so a resumed load costs only what is left.
    """
    if os.getenv("GEOGRAPH_GDELT_ON_BOOT", "1").strip().lower() in {"0", "false", "no"}:
        return {"ok": True, "skipped": "disabled by GEOGRAPH_GDELT_ON_BOOT"}
    results = []
    # Its own ceiling OR what the window can spare, whichever is smaller. The
    # ceiling still matters: GDELT runs FIRST among the expensive steps, so
    # without it a large backfill would eat the window and starve the rescore
    # that has to follow it.
    budget = min(float(_GDELT_BUDGET_SECONDS),
                 _remaining(_TAIL_RESERVE_SECONDS + _STUDY_MIN_SECONDS))
    for name in pack_names:
        artifacts = sorted(_DERIVED_DIR.glob(f"gdelt-{name}-*.tsv.gz"))
        if not artifacts:
            results.append({"pack": name, "ok": True,
                            "skipped": "no derived artifact in the image"})
            continue
        # EVERY artifact, not the last one. The modern-era backfill ships one
        # file PER YEAR (gdelt-mena-2014.tsv.gz …), because the daily era is
        # thousands of downloads and a harvest that cannot checkpoint between
        # years cannot finish. Reading only artifacts[-1] here would have
        # loaded 2026 alone and — worse — compared the graph's whole count
        # against that one year's, so the completeness check would pass
        # immediately and the other twenty years would never load. That is the
        # same silent-shortfall bug the docstring above describes, one level up.
        held = _graph_gdelt_count(name)
        pending = _pending_artifacts(name, artifacts)
        # Markers describe a graph. If that graph is gone — a rebuilt volume,
        # a wiped database — they describe nothing, and trusting them would
        # skip a load that has to happen. The graph is the authority on
        # whether it is empty; the markers are only the authority on how far a
        # non-empty one got.
        if held == 0 and len(pending) < len(artifacts):
            _log(f"gdelt {name}: markers survive an empty graph — reloading all")
            _clear_markers(name)
            pending = list(artifacts)
        if not pending:
            results.append({"pack": name, "ok": True, "held": held,
                            "skipped": f"all {len(artifacts)} artifacts loaded"})
            continue
        if budget <= 0:
            _log(f"gdelt {name}: out of budget — "
                 f"{len(artifacts) - len(pending)}/{len(artifacts)} artifacts, deferred")
            results.append({"pack": name, "ok": True, "held": held,
                            "pending": len(pending), "skipped": "GDELT budget spent"})
            continue
        # The count is now a DIAGNOSTIC and never a gate, so it is computed
        # only on a boot that is loading anyway — a pass over a few megabytes
        # of gzip that a finished lens no longer pays for at all.
        expected = _expected_events(artifacts)
        _log(f"gdelt {name}: {len(pending)}/{len(artifacts)} artifacts to load "
             f"(graph holds {held}/{expected} events)")
        steps = []
        for artifact in pending:
            if budget <= 0:
                _log(f"gdelt {name}: budget spent at {artifact.name} — deferred")
                break
            started = time.monotonic()
            # --skip-rescore on EVERY artifact. Head B folds escalation across
            # the whole archive in time order, so a rescore between artifacts
            # computes baselines from a partial archive and is overwritten by
            # the next one — wasted, and wrong while it lasts. It rewrites
            # every event in the graph, so twenty-one of them over a
            # million-event archive is the difference between a boot that
            # finishes and one that dies on its healthcheck. Run once, below.
            step = _run_step(
                f"gdelt backfill {name} {artifact.stem}",
                [sys.executable, str(_GDELT_SCRIPT), name,
                 "--from-filtered", str(artifact), "--skip-rescore"],
                timeout=int(min(_GDELT_TIMEOUT_SECONDS, budget)),
                echo=False,
            )
            steps.append(step)
            # ONLY on success. A timed-out or failed artifact stays pending, so
            # the next boot retries exactly it and nothing else — which is the
            # resumability the count was supposed to provide and could not.
            if step["ok"]:
                _mark_artifact_loaded(name, artifact)
            budget -= time.monotonic() - started
        after = _graph_gdelt_count(name)
        still_pending = _pending_artifacts(name, artifacts)
        results.append({
            "pack": name, "expected": expected, "held": after,
            "artifacts": len(artifacts), "loaded": len(steps),
            "pending": len(still_pending),
            "ok": all(s["ok"] for s in steps) if steps else True,
        })
        if still_pending:
            _log(f"gdelt {name}: {len(still_pending)} artifacts still pending")
        elif after is not None and after < expected:
            # Every artifact loaded and the graph is still short. NOT a failure
            # and no longer a reason to reload: the difference is ids this lens
            # shares with a lens that merged them first, plus records the loader
            # dropped rather than guessed at. Said out loud, because a silent
            # gap is the thing this archive refuses to have.
            _log(f"gdelt {name}: complete — {after}/{expected} events held; "
                 f"{expected - after} are shared with another lens or dropped")

    # (the rescore moved out to _rescore_if_new_events, so it can also cover
    #  the deep tier and can see whether the archive actually grew)
    return {"ok": all(r["ok"] for r in results), "packs": results}


def _unscored_events() -> int | None:
    """Events Head B has not scored. None if the graph is unreadable.

    THE RESCORE'S TRIGGER. GDELT's loader writes events with no escalation
    fields — those are Head B's to fill — so this count is exactly "how much
    work is outstanding", and it falls to zero only when the rescore has
    actually finished.
    """
    from core import settings as settings_module
    from core.graph import kuzu_store

    settings = settings_module.load()
    if not settings.kuzu_db_path.exists():
        return None
    conn = None
    try:
        conn = kuzu_store.connect(settings.kuzu_db_path, read_only=True)
        rows = kuzu_store.query(
            conn,
            "MATCH (e:Event) WHERE e.escalation_direction IS NULL "
            "OR e.escalation_direction = '' RETURN count(e) AS n",
        )
        return int(rows[0]["n"]) if rows else None
    except Exception:  # noqa: BLE001 - an unreadable graph is the caller's problem
        return None
    finally:
        kuzu_store.close(conn)


def _rescore_if_new_events(pack_names: list[str]) -> dict[str, Any]:
    """The archive-wide Head B rescore — ONCE, and only when it is needed.

    THIS IS THE STEP THAT DECIDES WHETHER A ROUTINE DEPLOY IS FAST. Escalation
    folds per dyad over every event in time order, so it costs hours on a
    1.5M-event archive and cannot be resumed. Both loaders used to run one
    inline — the deep tier unconditionally, on every boot — which is why that
    step took 643s against a 267k archive and would have exceeded its timeout
    on every future deploy for no benefit at all.

    So: skip unless the event stream actually changed. A frontend or model
    deploy adds no events, the count is identical, and this returns in
    milliseconds. Only a boot that loaded something pays, and it pays once.
    """
    # TRIGGERED BY THE CONDITION ITSELF, not by a proxy for it. An earlier
    # version compared the event count before and after loading, which is
    # wrong in the case that matters: if the rescore times out, the NEXT boot
    # loads nothing, sees an unchanged count, and skips forever — leaving
    # every backfilled event permanently unscored. Asking "are there events
    # Head B has not scored?" retries automatically until it succeeds, and
    # clears itself the moment it has.
    unscored = _unscored_events()
    if unscored is None:
        return {"ok": True, "skipped": "graph unreadable"}
    if unscored == 0:
        return {"ok": True, "skipped": "every event is scored"}

    # Still loading: rescoring a partial archive is work that the next boot's
    # load invalidates, and this step cannot be resumed to make up for it.
    incomplete = []
    for name in pack_names:
        artifacts = sorted(_DERIVED_DIR.glob(f"gdelt-{name}-*.tsv.gz"))
        if not artifacts:
            continue
        pending = _pending_artifacts(name, artifacts)
        if pending:
            incomplete.append(
                f"{name} {len(artifacts) - len(pending)}/{len(artifacts)} artifacts")
    if incomplete:
        _log("rescore deferred — still loading: " + ", ".join(incomplete))
        return {"ok": True, "skipped": "archive still loading",
                "incomplete": incomplete}

    # Its own ceiling OR what the window can spare. Unlike the study this step
    # cannot be resumed, so being cut short wastes the whole pass — but running
    # it with 200 seconds left wastes the same pass AND the window. Declining
    # early is strictly better: the trigger is "are there unscored events", so
    # the next boot simply tries again.
    budget = _remaining(_TAIL_RESERVE_SECONDS + _STUDY_MIN_SECONDS)
    timeout = min(float(_RESCORE_TIMEOUT_SECONDS), budget)
    if timeout < _RESCORE_MIN_SECONDS:
        _log(f"rescore deferred — {int(budget)}s of window left, needs "
             f"{_RESCORE_MIN_SECONDS}s to be worth starting")
        return {"ok": True, "skipped": "not enough window", "unscored": unscored}

    _log(f"rescore: {unscored:,} events unscored, folding Head B once "
         f"({int(timeout)}s available)")
    result = _run_step(
        "rescore (archive-wide, once, not resumable)",
        [sys.executable, str(_GDELT_SCRIPT), pack_names[0], "--rescore-only"],
        timeout=int(timeout),
        echo=False,
    )
    return {"unscored_before": unscored,
            "unscored_after": _unscored_events(),
            **{k: v for k, v in result.items() if k != "step"}}


def _load_13f() -> dict[str, Any]:
    """SWF capital flows from EDGAR (Phase 4's credential-free half).

    Idempotent quarterly merges, ~20 polite requests to SEC per run — and
    the volume graph keeps them, so a redeploy re-merges the same facts."""
    if os.getenv("GEOGRAPH_13F_ON_BOOT", "1").strip().lower() in {"0", "false", "no"}:
        return {"ok": True, "skipped": "disabled by GEOGRAPH_13F_ON_BOOT"}
    result = _run_step(
        "13f flows",
        [sys.executable, str(_LOAD_13F_SCRIPT)],
        timeout=_LOAD_TIMEOUT_SECONDS,
        echo=False,
    )
    return {k: v for k, v in result.items() if k != "step"}


def _run_network_metrics() -> dict[str, Any]:
    """NetworkMetric over the standard windows (Phase 2, build-spec §12).

    Pure graph computation — no panel, no network — so it runs every boot for
    the same reason the study does: the volume may have been rebuilt, and
    re-deriving deterministic numbers is how the graph stays consistent."""
    if os.getenv("GEOGRAPH_METRICS_ON_BOOT", "1").strip().lower() in {"0", "false", "no"}:
        return {"ok": True, "skipped": "disabled by GEOGRAPH_METRICS_ON_BOOT"}
    result = _run_step(
        "network metrics",
        [sys.executable, str(_METRICS_SCRIPT)],
        timeout=_SEED_TIMEOUT_SECONDS,
        echo=False,
    )
    return {k: v for k, v in result.items() if k != "step"}


def _apply_panel_schema() -> dict[str, Any] | None:
    """Panel DDL, if Postgres is configured. Concurrent-safe, so no child
    process is needed — nothing here can hold a lock the API wants."""
    from core import settings as settings_module
    from core.panel import pg_store

    settings = settings_module.load()
    if not settings.database_url:
        return None
    try:
        conn = pg_store.connect(settings)
        pg_store.apply_schema(conn)
        conn.close()
    except pg_store.PanelUnavailable as exc:
        _log(f"panel schema NOT applied: {exc}")
        return {"ok": False, "error": str(exc)}
    _log("panel schema applied")
    return {"ok": True}


def _freeze_forecasts() -> dict[str, Any]:
    """Freeze both forecast modes (Phase 5). Deterministic payloads, one
    clock stamp at persistence — see scripts/run_forecasts.py."""
    if os.getenv("GEOGRAPH_FORECASTS_ON_BOOT", "1").strip().lower() in {"0", "false", "no"}:
        return {"ok": True, "skipped": "disabled by GEOGRAPH_FORECASTS_ON_BOOT"}
    result = _run_step(
        "freeze forecasts",
        [sys.executable, str(_FORECASTS_SCRIPT)],
        timeout=_SEED_TIMEOUT_SECONDS,
        echo=False,
    )
    return {k: v for k, v in result.items() if k != "step"}


def _run_backtest() -> dict[str, Any]:
    """The walk-forward paper backtest (Phase 5's ledger). Reads the graph
    read-only and writes only to Postgres, so it can run beside the API; it
    re-runs every boot because the ledger is a function of (archive, panel,
    books) and any of the three may have moved."""
    if os.getenv("GEOGRAPH_BACKTEST_ON_BOOT", "1").strip().lower() in {"0", "false", "no"}:
        return {"ok": True, "skipped": "disabled by GEOGRAPH_BACKTEST_ON_BOOT"}
    result = _run_step(
        "paper backtest",
        [sys.executable, str(_BACKTEST_SCRIPT)],
        timeout=_LOAD_TIMEOUT_SECONDS,
        echo=False,
    )
    return {k: v for k, v in result.items() if k != "step"}


def _score_forecasts() -> dict[str, Any]:
    """Calibration over the frozen trail (Phase 5): Brier where horizons have
    resolved, retrodiction on the long-horizon nodes. Needs the write lock,
    so it runs as a boot child like the freeze itself."""
    if os.getenv("GEOGRAPH_SCORE_ON_BOOT", "1").strip().lower() in {"0", "false", "no"}:
        return {"ok": True, "skipped": "disabled by GEOGRAPH_SCORE_ON_BOOT"}
    result = _run_step(
        "score forecasts",
        [sys.executable, str(_SCORE_SCRIPT)],
        timeout=_SEED_TIMEOUT_SECONDS,
        echo=False,
    )
    return {k: v for k, v in result.items() if k != "step"}


def _reset_graph_if_asked() -> dict[str, Any] | None:
    """Delete the graph so the boot rebuilds it. OPT-IN, ONE VARIABLE, LOUD.

    THE RECOVERY PATH FOR A FULL VOLUME, and it is safe for one specific
    reason: NOTHING ON THIS VOLUME IS AN ORIGINAL. The packs, the deep tier and
    the GDELT artifacts all ship inside the image; the price panel lives in
    Postgres on a different volume; AFFECTED, NetworkMetric and every Forecast
    are computed from those two. The graph is a cache of a deterministic
    function, so deleting it costs a rebuild and never a fact.

    Why it is needed at all: Kuzu has no VACUUM. Space from rewritten rows is
    not reclaimed, and until the marker fix landed every boot re-merged the
    whole archive — so the file grew with each deploy while the data did not.
    A rebuilt graph is the compaction step the engine does not offer.

    Deliberately awkward: it must be set for the ONE deploy that rebuilds and
    unset afterwards, or every future boot throws the archive away and reloads
    it. The log says so, twice, because a destructive default that is quiet is
    how an archive dies.
    """
    if os.getenv("GEOGRAPH_RESET_GRAPH", "").strip().lower() not in {"1", "true", "yes"}:
        return None

    from core import settings as settings_module

    path = settings_module.load().kuzu_db_path
    _log("=" * 68)
    _log("GEOGRAPH_RESET_GRAPH is set — DELETING the graph and rebuilding it")
    _log("UNSET IT after this deploy, or every boot reloads from zero")
    _log("=" * 68)

    removed: list[dict[str, Any]] = []
    for target in (path, path.with_name(path.name + ".wal"),
                   path.with_name(path.name + ".tmp")):
        if not target.exists():
            continue
        size = (target.stat().st_size if target.is_file()
                else sum(f.stat().st_size for f in target.rglob("*") if f.is_file()))
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed.append({"path": str(target), "bytes": size})
            _log(f"removed {target.name} ({size / 1e9:.2f} GB)")
        except OSError as exc:
            _log(f"could NOT remove {target.name}: {exc}")
            return {"ok": False, "error": str(exc)}

    # The markers describe the graph that was just deleted. Leaving them would
    # make the very next step skip the load that has to happen.
    for name in _pack_names():
        _clear_markers(name)
    _log("markers cleared — every artifact will reload")
    freed = sum(int(r["bytes"]) for r in removed)
    return {"ok": True, "removed": removed, "freed_gb": round(freed / 1e9, 2)}


def _boot_status() -> dict[str, Any]:
    if _disabled():
        _log("seeding disabled by GEOGRAPH_SEED_ON_BOOT")
        return {"seeded": False, "reason": "disabled by GEOGRAPH_SEED_ON_BOOT"}

    # BEFORE anything opens the graph — a reset that runs after the seed has
    # taken the single-writer lock deletes a database out from under a live
    # connection.
    reset = _reset_graph_if_asked()

    status: dict[str, Any] = {
        "seeded": True, "packs": [], "panel": None, "prices": None, "study": None,
    }
    if reset is not None:
        status["reset"] = reset
    try:
        names = _pack_names()
    except Exception as exc:  # noqa: BLE001 - a broken pack must not stop the boot
        _log(f"cannot list packs: {exc}")
        return {"seeded": False, "reason": f"cannot list packs: {exc}"}

    if not names:
        _log("no pack satisfies the contract — nothing to seed")
        return {"seeded": False, "reason": "no complete pack found"}

    for name in names:
        status["packs"].append(_seed_one(name))

    # Each later step is independently guarded: a failing panel must not stop
    # the study from reporting why it could not run, and neither may stop the
    # API from coming up.
    steps: tuple[tuple[str, Callable[[], dict[str, Any] | None]], ...] = (
        ("deep", _load_deep_tier),
        ("gdelt", lambda: _load_gdelt(names)),
        ("rescore", lambda: _rescore_if_new_events(names)),
        ("flows", _load_13f),
        ("panel", _apply_panel_schema),
        ("prices", lambda: _load_panel_if_shallow(names)),
        ("study", lambda: _run_study(names)),
        ("metrics", _run_network_metrics),
        ("forecasts", _freeze_forecasts),
        ("scores", _score_forecasts),
        ("backtest", _run_backtest),
    )
    for key, step in steps:
        try:
            status[key] = step()
        except Exception as exc:  # noqa: BLE001 - report, keep booting
            status[key] = {"ok": False, "error": str(exc)}
            _log(f"{key}: FAILED — {exc}")
    return status


def main() -> None:
    argv = sys.argv[1:] or _DEFAULT_APP
    try:
        status = _boot_status()
    except Exception as exc:  # noqa: BLE001 - the API still has to come up
        status = {"seeded": False, "reason": f"unhandled boot error: {exc}"}
        _log(status["reason"])

    # Handed to the app through the environment rather than a status file:
    # execvp carries os.environ across, and there is no leftover file to go
    # stale or to be read from a previous deployment.
    os.environ["GEOGRAPH_BOOT_STATUS"] = json.dumps(status)
    _log(f"exec {' '.join(argv)}")
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
