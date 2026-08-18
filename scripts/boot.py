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

WHAT THE BOOT IS ALLOWED TO COST. In API-first mode (the default) the health
check passes in ~20s — the API execs immediately and these steps run on a
BACKGROUND THREAD behind the bound port. But the graph endpoints stay dark
until that thread finishes, because Kuzu is one writer OR many readers across
processes: the API opens its connection only once the last write-child exits
(core/api/app.py::_run_boot_behind_the_api). So the boot's real budget is still
DOWNTIME — of the GRAPH half of the API — and the rule that follows is
unchanged: a step belongs before graph-open only if the API is WRONG without
it. Seeding qualifies (an unseeded graph serves nothing). Measuring the archive
does not: AFFECTED, NetworkMetric, Forecast nodes and the walk-forward ledger
are a deterministic function of inputs that all outlive the container (packs
and corpus in the image, panel in Postgres), so they persist on the volume and
a routine deploy serves every one it had a moment ago while adding none.

The measuring steps are therefore OPT-IN. The study earned this the hard way:
it never converged inside its budget, so it burned ~600s of graph-dark time on
EVERY deploy re-measuring what was already on the volume (see _run_study). Turn
them on together for a deploy whose job is measuring; leave them off for one
whose job is shipping code, and the graph opens seconds after the seed.

  GEOGRAPH_SEED_ON_BOOT=0      skip seeding (a batch job needs the write lock)
  GEOGRAPH_SEED_PACKS=mena     which packs to seed; default is every pack that
                               satisfies the contract, so packs/china seeds
                               itself the day it becomes complete
  GEOGRAPH_LOAD_PANEL=0        never fetch prices at boot
  GEOGRAPH_STUDY_ON_BOOT=1     opt IN to the transmission engine over the whole
                               archive (off by default: it held the graph dark
                               ~600s/deploy). The CURATED SPINE is measured on
                               every boot regardless — it is ~40 events and it
                               is what every narrated surface reads.
  GEOGRAPH_STUDY_BUDGET=600    seconds the study may spend ACROSS packs
  GEOGRAPH_SPINE_TIMEOUT=180   ceiling per pack for that always-on spine run
  GEOGRAPH_FORECASTS_ON_BOOT=1 opt IN to re-freezing the Forecast nodes (off by
                               default; the last freeze persists on the volume)
  GEOGRAPH_GAMES_ON_BOOT=1     opt IN to the region scenario-map solve (Postgres
                               game_solutions; off by default, same reason)
  GEOGRAPH_BACKTEST_ON_BOOT=1  opt IN to the walk-forward paper backtest (off by
                               default; the ledger persists in Postgres)
  GEOGRAPH_GDELT_ON_BOOT=1     opt IN to loading the wire (hours; off by default)
  GEOGRAPH_RESCORE_ON_BOOT=1   opt IN to the archive-wide rescore (hours, and
                               un-resumable; off by default)
  GEOGRAPH_RESET_GRAPH=1       delete and rebuild the graph, ONCE per value
  GEOGRAPH_BOOT_WINDOW=2700    bounds the background steps; equals railway.json's
                               healthcheckTimeout (the ceiling on a measuring
                               boot's thread, not on the health check itself)

The price fetch is CONDITIONAL on the panel being empty or stale, because it is
the one step that reaches the network and does not need repeating: Postgres
survives a redeploy, so a loaded panel stays loaded. The measuring steps, when
opted in, re-derive into a graph that may have been rebuilt, resuming from a
watermark across boots.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
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
_GAMES_SCRIPT = _ROOT / "scripts" / "solve_games.py"
_SCORE_SCRIPT = _ROOT / "scripts" / "score_forecasts.py"
_DERIVED_DIR = _ROOT / "data" / "derived"
_DEEP_TIER_SCRIPT = _ROOT / "scripts" / "load_deep_tier.py"
_LOAD_13F_SCRIPT = _ROOT / "scripts" / "load_13f.py"
_REBUILD_AFFECTED_SCRIPT = _ROOT / "scripts" / "rebuild_affected.py"

#: Filename of the reset ledger, written beside the graph on the VOLUME — the
#: only place that outlives a container and so the only place that can answer
#: "have I already done this?". A sibling of the database, not a child, so
#: deleting the database does not delete the record that it was deleted.
_RESET_LEDGER = ".graph-reset-honoured"

#: The same shape for the AFFECTED repair: which GEOGRAPH_REBUILD_AFFECTED
#: value has already been acted on. Same reasoning — an env var is sticky, and
#: a repair that re-ran on every restart would drop a table for nothing.
_REBUILD_AFFECTED_LEDGER = ".affected-rebuild-honoured"

#: The repair's ceiling. A probe is seconds; a full re-projection of a
#: million-edge table from the panel is ~10 minutes at batch rate, and the
#: refill is resumable from its marker if this is ever hit.
_REBUILD_AFFECTED_TIMEOUT_SECONDS = int(os.getenv("GEOGRAPH_REBUILD_AFFECTED_TIMEOUT", "2700"))

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
#: "some progress" is not one of its outcomes. Sized against the CURRENT
#: archive, not a lens: ~1.33M events at the measured ~262 events/sec is
#: ~5,100s, so anything under an hour is a pass that starts doomed. The old
#: 1800s floor dated from a 456k single-lens archive; against today's it
#: guaranteed exactly the wasted pass it exists to prevent. A serving boot's
#: default window cannot clear this — which is correct: rescoring is a job for
#: a boot whose window was raised for it.
_RESCORE_MIN_SECONDS = 3600

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
_STUDY_TIMEOUT_SECONDS = int(os.getenv("GEOGRAPH_STUDY_PACK_CEILING", "300"))

#: …and the TOTAL the study may spend across every pack, which is what actually
#: bounds the boot. The per-pack ceiling alone does not: three packs at 1200s
#: is 3600s of downtime, and `event study china: TIMED OUT after 1200s` in the
#: 2026-08-13 logs is the step reliably taking every second it was offered.
#:
#: Small on purpose. The engine works off a measured-events watermark, so a
#: truncated pass costs NOTHING — it resumes exactly where it stopped on the
#: next boot. That makes the study the one expensive step that can be metered
#: rather than finished, and metering it is what turns a four-hour boot into a
#: six-minute one. It converges across boots instead of inside one.
_STUDY_BUDGET_SECONDS = int(os.getenv("GEOGRAPH_STUDY_BUDGET", "600"))

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
#:
#: 2700, DOWN FROM 14400, and the reduction is the point rather than a tuning.
#: A four-hour window was the honest size for a boot that LOADED THE ARCHIVE
#: before binding: 5400s of GDELT merging at ~145 events/sec, then an
#: un-resumable rescore that wanted another two hours. Both are now deliberate
#: jobs (`GEOGRAPH_GDELT_ON_BOOT`, `GEOGRAPH_RESCORE_ON_BOOT`, default off), so
#: what remains before the API binds is seeding, a cached deep tier, the 13F
#: flows and a BOUNDED study — minutes, not hours.
#:
#: Sizing a window is choosing which failure you want. Too short kills a
#: container that was working (2026-08-12). Too long is not free either, and
#: that is the lesson of 2026-08-13: a four-hour ceiling let a boot spend four
#: hours, so the site was down for four hours while a step that did not have to
#: run at startup ran at startup. The window is a ceiling on PATIENCE, and
#: patience for work the boot should not be doing is just downtime.
#:
#: 2700, not the realistic ~1,100s a boot actually spends, because the window
#: must cover the CEILINGS, not the averages: study 600 + forecasts 600 +
#: metrics/scores/backtest ceilings sum near 2,000 in the worst case, and a
#: window the worst case can graze kills a container that was working — the
#: 2026-08-12 failure, 30 seconds over. The margin costs nothing: a healthy
#: boot binds when it binds, and the window only decides how long a genuinely
#: broken one takes to be declared broken.
_WINDOW_SECONDS = int(os.getenv("GEOGRAPH_BOOT_WINDOW", "2700"))

#: What the steps AFTER the study need: metrics, forecasts, scores, backtest.
#: Measured at ~320s across the 2026-08-13 boots; 600 leaves margin for a
#: larger archive. This is the ONLY estimate left in the budget arithmetic.
_TAIL_RESERVE_SECONDS = 600

#: Never begin the study with less than this — a study shorter than its own
#: startup cost measures a handful of events for nothing. Below this the step
#: declines and says so, which is honest and costs the same.
#:
#: 120, down from 600, because the study is now METERED rather than run to
#: completion: with a 600s total budget a 600s floor would make "enough window
#: to bother" and "the whole budget" the same number, so any step that ran
#: slightly long ahead of it would silently skip the study entirely.
_STUDY_MIN_SECONDS = 120

#: How old the panel's NEWEST close may be before a boot refreshes it. Two
#: trading days of slack: weekends produce legitimate 2-3 day gaps, and a
#: refresh that fires on every Monday for no reason is noise. Beyond this the
#: forward paper book cannot mark — its entry date trails the newest close —
#: which is a page reading $0, not a style question.
_PANEL_STALE_DAYS = 4

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


# ── input fingerprints: unchanged inputs cost milliseconds ───────────────────
#
# Metrics, forecasts, scores, the deep tier and the (converged) study used to
# run UNCONDITIONALLY every boot — ~290s of recomputation whose inputs are a
# pure function of (image content, graph content, panel edge), none of which
# change on a frontend-only push. Each guarded step records a fingerprint of
# exactly what it reads, on the volume beside the graph (so a volume wipe
# invalidates every fingerprint with the data), and skips in milliseconds when
# nothing moved. GEOGRAPH_SKIP_GUARDS=0 disables all of it.

_FINGERPRINTS_FILE = ".boot-fingerprints.json"


def _guards_enabled() -> bool:
    return os.getenv("GEOGRAPH_SKIP_GUARDS", "1").strip().lower() not in {"0", "false", "no"}


def _fingerprints_path() -> Path:
    from core import settings as settings_module

    return settings_module.load().kuzu_db_path.parent / _FINGERPRINTS_FILE


def _load_fingerprints() -> dict[str, str]:
    try:
        return dict(json.loads(_fingerprints_path().read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return {}


def _save_fingerprint(step: str, value: str) -> None:
    try:
        stored = _load_fingerprints()
        stored[step] = value
        path = _fingerprints_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stored, indent=1), encoding="utf-8")
    except OSError as exc:
        _log(f"fingerprint for {step} not recorded: {exc}")


def _image_fingerprint() -> str:
    """The shipped inputs AND the code that derives from them: corpus
    artifacts, model artifacts, packs, and the ingestion/ontology modules.
    Constant for the life of an image — changes exactly when a build changes
    them.

    MUST be stable ACROSS PROCESSES, and builtin `hash()` is not: Python salts
    the hash of str/tuple per interpreter via PYTHONHASHSEED (unset here), so
    `str(hash(tuple(parts)))` returned a different value on every boot — which
    silently defeated EVERY fingerprint guard, since a stored fingerprint from
    boot N never matched boot N+1 and `deep`/`metrics`/`scores` recomputed in
    full on every routine deploy. A content digest is process-stable, so the
    guards skip when the shipped inputs are byte-identical, as designed.
    """
    digest = hashlib.sha256()
    # THE CODE THAT READS THE INPUTS IS AN INPUT. Without this the guard is
    # blind to a loader bug being FIXED: production carried COW alliances with
    # no end date — the graph believed Britain and Russia were allies on the
    # strength of a 1915 treaty, and the United States and Iran on a 1958 one,
    # both alongside the rivalries that actually characterise them. The source
    # had the terminations all along (1917-11-08, 1979-03-12) and the loader
    # parses them correctly today; the edges were written by an older version
    # and the fingerprint matched forever, so they were never re-derived.
    #
    # Only `core/ingestion` and `core/ontology`: those are what turn a raw
    # file into graph rows. Widening this to all of `core/` would invalidate
    # every guard on every code push, which is the cost the guards exist to
    # avoid.
    for directory in (_DERIVED_DIR, _ROOT / "models", _ROOT / "packs",
                      _ROOT / "core" / "ingestion", _ROOT / "core" / "ontology"):
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                # CONTENT, not name:size — a same-length edit to a pack YAML
                # or a re-fit model artifact of identical byte count must
                # invalidate the guards. ~70MB once per boot, well under a
                # second.
                digest.update(str(path.relative_to(_ROOT)).encode("utf-8"))
                digest.update(path.read_bytes())
    return digest.hexdigest()


#: The graph facets a step can declare as INPUTS. A step's fingerprint must
#: cover what it READS and exclude what it WRITES, or the guard never sticks:
#: the freeze writes Forecast nodes, so `frozen` in its own fingerprint would
#: re-trigger it every boot forever.
_GRAPH_FACETS = {
    "events": "MATCH (e:Event) RETURN count(e) AS n",
    "latest": "MATCH (e:Event) RETURN max(e.event_time) AS n",
    "affected": "MATCH ()-[a:AFFECTED]->() RETURN count(a) AS n",
    "relates": "MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS n",
    "estimates": "MATCH (s:AttributeEstimate) RETURN count(s) AS n",
    "forecasts": "MATCH (f:Forecast) RETURN count(f) AS n",
    "frozen": "MATCH (f:Forecast) RETURN max(f.generated_at) AS n",
}


def _graph_fingerprint(*facets: str) -> str:
    """The named facets of the graph's content edge, cheap counts and stamps.
    Computed fresh at each guarded step, because earlier steps move it."""
    from core import settings as settings_module
    from core.graph import kuzu_store

    settings = settings_module.load()
    if not settings.kuzu_db_path.exists():
        return "no-graph"
    try:
        conn = kuzu_store.connect(settings.kuzu_db_path, read_only=True)
        try:
            parts = []
            for label in facets:
                rows = kuzu_store.query(conn, _GRAPH_FACETS[label])
                parts.append(f"{label}={rows[0]['n'] if rows else None}")
            return ";".join(parts)
        finally:
            kuzu_store.close(conn)
    except Exception as exc:  # noqa: BLE001 - an unreadable graph is a fingerprint too
        return f"unreadable:{exc}"


def _panel_edge() -> str:
    """The panel's newest close AND its ticker breadth. Breadth joined the
    facet on 2026-08-15: loading a pack's never-loaded tickers moves no date,
    and the backtest/study guards would have slept through the panel gaining
    the very series their books trade."""
    reachable, latest = _panel_latest_observation()
    if not reachable:
        return "panel=unreachable"
    from core import settings as settings_module
    from core.panel import pg_store

    breadth = "?"
    try:
        conn = pg_store.connect(settings_module.load())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT count(DISTINCT market_ticker) FROM market_observations")
                row = cur.fetchone()
            breadth = str(row[0] if row else "?")
        finally:
            conn.close()
    except pg_store.PanelUnavailable:
        pass
    return f"panel={latest};tickers={breadth}"


def _raw_listing() -> str:
    raw = Path(os.getenv("GEOGRAPH_RAW_DIR", str(_ROOT / "data" / "raw")))
    if not raw.exists():
        return "no-raw"
    return ",".join(
        f"{p.name}:{p.stat().st_size}" for p in sorted(raw.glob("*")) if p.is_file()
    )


def _guarded(
    step: str,
    fingerprint_of: Callable[[], str],
    runner: Callable[[], dict[str, Any] | None],
    *,
    complete: Callable[[dict[str, Any] | None], bool] | None = None,
) -> dict[str, Any] | None:
    """Skip `runner` when its recorded input fingerprint matches; record the
    POST-run fingerprint after a complete run.

    Post-run, because several steps move their own inputs' facets (the study
    writes AFFECTED, the freeze writes Forecast) — a pre-run fingerprint for
    those can never match again and the guard would never stick. A failed or
    partial run records nothing, so the next boot retries.
    """
    if not _guards_enabled():
        return runner()
    if _load_fingerprints().get(step) == fingerprint_of():
        _log(f"{step}: inputs unchanged — skipped (fingerprint match)")
        return {"ok": True, "skipped": "inputs unchanged (fingerprint match)"}
    result = runner()
    # A SKIPPED run is not a complete run: opt-in runners return
    # {"ok": True, "skipped": ...} when their variable is off, and recording a
    # fingerprint for that would make a later explicit opt-in silently no-op
    # (fingerprint matches, step never actually ran). The study's callback and
    # _load_13f_weekly already refused skips; the default must too.
    is_complete = (
        complete(result)
        if complete is not None
        else result is None
        or (bool(result.get("ok")) and "error" not in result and "skipped" not in result)
    )
    if is_complete:
        _save_fingerprint(step, fingerprint_of())
    return result


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


def _panel_latest_observation() -> tuple[bool, str | None]:
    """The panel's NEWEST close — the freshness half of the guard."""
    from core import settings as settings_module
    from core.panel import pg_store

    settings = settings_module.load()
    try:
        conn = pg_store.connect(settings)
    except pg_store.PanelUnavailable:
        return False, None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT max(obs_date) FROM market_observations")
            row = cur.fetchone()
        last = row[0] if row else None
        return True, last.isoformat() if last is not None else None
    finally:
        conn.close()


def _missing_tickers(pack_names: list[str]) -> dict[str, list[str]]:
    """pack → its declared tickers with ZERO panel rows (any frequency)."""
    from core import packs
    from core import settings as settings_module
    from core.panel import pg_store

    settings = settings_module.load()
    try:
        conn = pg_store.connect(settings)
    except pg_store.PanelUnavailable:
        return {}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT market_ticker FROM market_observations")
            held = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()
    out: dict[str, list[str]] = {}
    for name in pack_names:
        try:
            pack = packs.load(name)
        except Exception:  # noqa: BLE001 - a broken pack is the seed step's report
            continue
        missing = [m["ticker"] for m in pack.markets if m["ticker"] not in held]
        if missing:
            out[name] = missing
    return out


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
    # A THIRD FAILURE, FOUND 2026-08-15: A PACK WHOSE TICKERS WERE NEVER LOADED.
    # Depth and freshness are measured over the WHOLE table, so a panel that
    # reaches 1871 on mena's tickers and is current on them passed both halves
    # while ^TWII, ^HSI, ^GDAXI and every other pack-unique ticker held zero
    # rows — china's and eurasia's paper books marked one leg of three, and
    # 425 quarters each were recorded skips ("1 of 3 legs have panel closes").
    # Coverage is per ticker: any declared market with no rows gets its full
    # history, once, before the depth/freshness guard is consulted.
    missing = _missing_tickers(pack_names)
    loaded_missing: list[dict[str, Any]] = []
    for name, tickers in missing.items():
        _log(f"panel holds no rows for {name}'s {', '.join(tickers)} — loading them")
        loaded_missing.append(_run_step(
            f"load panel {name} ({len(tickers)} missing tickers)",
            [sys.executable, str(_LOAD_PANEL_SCRIPT), name, "--tickers", ",".join(tickers)],
            timeout=_LOAD_TIMEOUT_SECONDS,
        ))
    if first is not None and (needed is None or first <= needed):
        # DEEP ENOUGH IS ONLY HALF THE GUARD. The other failure is STALENESS,
        # and it took the paper book down on 2026-08-14: the panel reached
        # 1871 so this branch skipped loading on every boot, while its newest
        # close stayed frozen at the last full load — and a forward book
        # entered after that date had nothing to mark against, so every
        # position was a recorded skip and the page read $0. Depth serves the
        # SPINE; freshness serves the BOOK. A stale panel triggers a windowed
        # fetch from just before its own edge — seconds of yfinance, upserted,
        # not the full-history reload the depth branch pays for.
        import datetime as dt

        _, latest = _panel_latest_observation()
        stale_days = (
            (dt.date.today() - dt.date.fromisoformat(latest)).days
            if latest else None
        )
        if latest is not None and stale_days is not None and stale_days <= _PANEL_STALE_DAYS:
            _log(f"panel reaches {first} and is current to {latest} — nothing to load")
            if loaded_missing:
                return {"ok": all(r["ok"] for r in loaded_missing),
                        "missing_loaded": loaded_missing}
            return {"ok": True, "skipped": f"panel reaches {first}, current to {latest}"}
        if latest is not None:
            _log(f"panel is deep but STALE — newest close {latest} "
                 f"({stale_days}d old); refreshing the recent window")
            start = (dt.date.fromisoformat(latest) - dt.timedelta(days=7)).isoformat()
            results = [
                _run_step(
                    f"refresh panel {name} (from {start})",
                    [sys.executable, str(_LOAD_PANEL_SCRIPT), name, "--start", start],
                    timeout=_LOAD_TIMEOUT_SECONDS,
                )
                for name in pack_names
            ]
            return {"ok": all(r["ok"] for r in results + loaded_missing),
                    "refreshed_from": start, "packs": results,
                    "missing_loaded": loaded_missing}
        _log(f"panel reaches {first} — deep enough for the spine ({needed})")
        if loaded_missing:
            return {"ok": all(r["ok"] for r in loaded_missing), "missing_loaded": loaded_missing}
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


#: Per pack, for the curated spine. A dozen events against a dozen markets;
#: the ceiling is here to bound a pathological panel read, not to ration.
_SPINE_TIMEOUT_SECONDS = int(os.getenv("GEOGRAPH_SPINE_TIMEOUT", "180"))


def _run_spine_study(pack_names: list[str]) -> dict[str, Any]:
    """The transmission engine over the events THE PACKS NAME — always on.

    The full study is opt-in and rightly so: it is a hundred thousand events
    and it walks them in date order, so a boot's slice measures the deep past
    and stops. That is the correct trade for the archive and the wrong one for
    the SPINE: the case studies, the marquee episodes and everything the front
    page is written around are the most recent events in the archive, i.e. the
    last ones a truncated walk would ever reach. On 2026-08-15 production had
    632,586 measured effects and served "this study has a spine and no
    numbers" on all three case studies, because the walk had reached 2003.

    So the curated set is measured on EVERY boot, watermarked against the
    GRAPH rather than the panel (`run_event_study.py --spine`) so it also
    heals after a volume rebuild, which the panel-side watermark cannot see.
    It is ~40 events across three packs: seconds when there is work, and a
    graph query plus an early exit when there is not.

    Deliberately NOT fingerprint-guarded. Every other guarded step re-derives
    something already persisted; this one is the check that what should be
    persisted IS, and a guard that skipped it would be a guard on the smoke
    detector.
    """
    if _panel_is_empty() is not False:
        return {"ok": True, "skipped": "panel empty"}
    results = []
    for name in pack_names:
        results.append({
            "pack": name,
            **{k: v for k, v in _run_step(
                f"spine study {name}",
                [sys.executable, str(_STUDY_SCRIPT), name, "--spine"],
                timeout=_SPINE_TIMEOUT_SECONDS,
                echo=False,
            ).items() if k != "step"},
        })
    return {"ok": all(r["ok"] for r in results), "packs": results}


def _run_study(pack_names: list[str]) -> dict[str, Any] | None:
    """The transmission engine over the WHOLE spine (Phase 1, build-spec §18).

    --all measures every marquee event; a market with no data at an event's
    date is a recorded skip, not an error, so the deep spine costs nothing
    but honesty.

    OFF BY DEFAULT SINCE 2026-08-14 — because THIS is the step that held the
    site's graph endpoints dark for ~16 minutes on every deploy, and the same
    lesson GDELT and the rescore already learned applies to it exactly.

    The API-first boot (core/api/app.py) opens the API's Kuzu connection ONLY
    after the last write-child exits — Kuzu is one writer OR many readers,
    never both across processes — so every write step here runs while the API
    holds no graph connection and every graph endpoint answers 503. The study
    is a write-child, and it never CONVERGED inside its budget: china and
    eurasia each timed out at ~300s and mena was deferred, so it burned its
    full 600s on every boot without finishing, never recorded a clean
    fingerprint to skip on, and re-ran in full the next boot — 600s of graph
    downtime, forever, for measurements that were already persisted on the
    volume. "A step that cannot finish inside a boot does not belong in one"
    (aa585d1, for GDELT and the rescore). It is now that same opt-in job.

    Deferring it is safe for the same reason deferring GDELT is: the graph is a
    cache of a deterministic function, the panel it reads lives in Postgres,
    and AFFECTED already written stays on the volume — so a routine deploy
    serves every measurement it had a second ago and simply adds none. Run it
    with GEOGRAPH_STUDY_ON_BOOT=1 on a deploy whose job IS measuring (the
    healthcheck already passed in ~20s; the background thread can take as long
    as its budget), or out-of-band via scripts/run_event_study.py.
    """
    if os.getenv("GEOGRAPH_STUDY_ON_BOOT", "0").strip().lower() not in {"1", "true", "yes"}:
        return {"ok": True, "skipped": "not a measuring boot (GEOGRAPH_STUDY_ON_BOOT)"}
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
    # THE STUDY TAKES A FIXED SLICE, NOT THE REMAINDER — reversed on
    # 2026-08-13, and the reversal is the whole reason the boot is minutes now.
    #
    # Handing it "every second the window has left" is correct arithmetic and
    # the wrong objective: it optimises for progress per boot when what a
    # deployment owes its users is a bound on TIME TO SERVE. Because the engine
    # resumes from a per-event watermark, progress is fungible across boots and
    # downtime is not — an extra forty minutes here is forty minutes of
    # permanent progress AND forty minutes of a dark site, and only one of those
    # is recoverable. So it takes its slice, and the rest converges next boot.
    results = []
    budget = min(float(_STUDY_BUDGET_SECONDS), _remaining(_TAIL_RESERVE_SECONDS))
    if budget < _STUDY_MIN_SECONDS:
        _log(f"event study: {int(budget)}s left in the window — too little to "
             f"be worth starting, deferred")
        return {"ok": True, "skipped": "window spent", "remaining": int(budget)}
    _log(f"event study: {int(budget)}s of window remaining, fair share across "
         f"{len(pack_names)} packs, {int(_STUDY_TIMEOUT_SECONDS)}s ceiling per pack")
    # FAIR SHARE, not first-come-first-served. The old loop handed each pack
    # `min(ceiling, whatever-is-left)` IN ORDER, so china took its ceiling, then
    # eurasia took most of the rest, and mena — always last, alphabetically —
    # inherited a one-second slice and measured nothing. Every measuring boot
    # therefore refreshed china's effects and never mena's, so the flagship
    # region's market dynamics went stale while the arithmetic looked fine.
    # Now each pack gets an equal cut of the REMAINING budget (so the first pack
    # cannot eat the whole thing), a pack that finishes under its share leaves
    # the surplus for the rest, and the last pack inherits whatever is left. The
    # watermark still makes progress fungible across boots, so a pack that runs
    # out of its slice resumes next boot where it stopped.
    packs_left = len(pack_names)
    for name in pack_names:
        fair = budget / packs_left
        packs_left -= 1
        # A FLOOR, not `budget > 0`: a slice too small to measure anything is a
        # deferral, and saying so keeps `study: ok` honest (a one-second slice
        # that times out instantly is not a failure of the boot).
        if fair < _STUDY_MIN_SECONDS:
            _log(f"event study {name}: {int(fair)}s fair share — too little to "
                 f"measure, deferred to the next boot")
            results.append({"pack": name, "ok": True, "skipped": "study budget spent"})
            continue
        started = time.monotonic()
        results.append({
            "pack": name,
            **_run_step(
                f"event study {name}",
                [sys.executable, str(_STUDY_SCRIPT), name, "--all"],
                timeout=int(min(_STUDY_TIMEOUT_SECONDS, fair)),
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
    # OFF BY DEFAULT SINCE 2026-08-13 — this step is why boots took hours.
    #
    # Merging the wire into Kuzu runs at ~145 events/sec and SLOWS as the graph
    # grows (china loaded 340,445 into an empty graph at 353/sec; eurasia's
    # identical years then cost 4-6x each). At 1.33M events that is hours of
    # pre-API work, and because Kuzu is single-writer it is hours with the site
    # DARK. The archive is not urgent; serving is.
    #
    # Nothing is lost by deferring it: the artifacts ship in the image, the
    # loader skips ids already present, and the markers make every load
    # resumable. Turn it on deliberately (`GEOGRAPH_GDELT_ON_BOOT=1`) for a
    # deploy whose job IS loading, and leave it off for every deploy whose job
    # is shipping code.
    if os.getenv("GEOGRAPH_GDELT_ON_BOOT", "0").strip().lower() not in {"1", "true", "yes"}:
        return {"ok": True, "skipped": "not a loading boot (GEOGRAPH_GDELT_ON_BOOT)"}
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
    # OFF BY DEFAULT SINCE 2026-08-13, for a stronger reason than GDELT's.
    #
    # This is the one step here that CANNOT be resumed — it folds escalation per
    # dyad across every era in a single pass, so an interrupted run leaves
    # nothing behind. That makes it the worst possible thing to put in front of
    # a health check: it needs hours, it cannot be metered, and being cut short
    # wastes the whole pass. A boot cannot both bound its time to serve and host
    # a step that is all-or-nothing at that scale.
    #
    # It belongs in a job that can take as long as it needs. Run it deliberately
    # with GEOGRAPH_RESCORE_ON_BOOT=1 on a deploy whose job is convergence, or
    # out-of-band against the corpus. The trigger below is still "are there
    # unscored events", so whenever it does run it retries until it succeeds.
    if os.getenv("GEOGRAPH_RESCORE_ON_BOOT", "0").strip().lower() not in {"1", "true", "yes"}:
        return {"ok": True, "skipped": "not a rescoring boot (GEOGRAPH_RESCORE_ON_BOOT)"}

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


def _load_13f_weekly() -> dict[str, Any]:
    """The 13F fetch, at most weekly. Its input is LIVE EDGAR — the one boot
    input that is off-box — but 13F filings are quarterly, so hitting SEC on
    every deploy is politeness spent for nothing. The last successful fetch
    date lives in the fingerprint store; a volume wipe forgets it, which is
    the correct failure (the graph forgot the flows too)."""
    import datetime as dt

    if _guards_enabled():
        last = _load_fingerprints().get("flows", "")
        today = dt.date.today()
        try:
            if last and (today - dt.date.fromisoformat(last)).days < 7:
                _log(f"13f flows: fetched {last} — skipped (filings are quarterly)")
                return {"ok": True, "skipped": f"fetched {last}; EDGAR is quarterly"}
        except ValueError:
            pass
    result = _load_13f()
    if _guards_enabled() and result.get("ok") and not result.get("skipped"):
        _save_fingerprint("flows", dt.date.today().isoformat())
    return result


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
        # PROVENANCE, STAMPED ON MEASUREMENTS RECORDED BEFORE THE COLUMN
        # EXISTED. Exact rather than inferred: `runner.effect_source` is a pure
        # function of a row's resolution and ticker, so the same rule in SQL
        # reproduces what the graph edge was stamped with. Idempotent — it
        # touches only rows with no source — so it costs one indexed scan on
        # every boot after the first.
        stamped = pg_store.backfill_effect_sources(conn)
        unsourced = pg_store.unsourced_effects(conn)
        conn.close()
    except pg_store.PanelUnavailable as exc:
        _log(f"panel schema NOT applied: {exc}")
        return {"ok": False, "error": str(exc)}
    _log("panel schema applied")
    if stamped:
        _log(f"  stamped provenance on {stamped:,} measurements")
    if unsourced:
        # The invariant's backstop, in the store that now holds the numbers.
        _log(f"  WARNING: {unsourced:,} measurements carry no source")
    return {"ok": True, "sourced": stamped, "unsourced": unsourced}


def _freeze_forecasts() -> dict[str, Any]:
    """Freeze both forecast modes (Phase 5). Deterministic payloads, one
    clock stamp at persistence — see scripts/run_forecasts.py."""
    # OPT-IN SINCE 2026-08-14, for the same reason the study is: the freeze is a
    # write-child, so it runs before the API opens the graph and adds to the
    # graph-dark window on every deploy (~135s measured). The frozen Forecast
    # nodes persist on the volume, so /api/forecasts serves the last freeze; a
    # routine deploy refreshes nothing and dark-serves nothing. Refresh with
    # GEOGRAPH_FORECASTS_ON_BOOT=1 on a measuring deploy (pair it with the study,
    # whose new AFFECTED the forecasts read), or run scripts/run_forecasts.py.
    if os.getenv("GEOGRAPH_FORECASTS_ON_BOOT", "0").strip().lower() not in {"1", "true", "yes"}:
        return {"ok": True, "skipped": "not a measuring boot (GEOGRAPH_FORECASTS_ON_BOOT)"}
    result = _run_step(
        "freeze forecasts",
        [sys.executable, str(_FORECASTS_SCRIPT)],
        # Its own ceiling, not the seed's 300s: since the wire moved to the
        # corpus the freeze parses ~1.33M events (once, cached in-process) and
        # unions them with the graph per region — measured at ~37s per region
        # locally, so 600 covers three regions on slower hardware with room.
        timeout=600,
        echo=False,
    )
    return {k: v for k, v in result.items() if k != "step"}


def _run_backtest() -> dict[str, Any]:
    """The walk-forward paper backtest (Phase 5's ledger).

    ON BY DEFAULT AGAIN SINCE 2026-08-14: the walk was off from 2026-08-13
    (the night the corpus landed) because recomputing the live estimator at
    every quarter end cost a full pass over 1.31M rows per cutoff, and the
    first corpus boot ground on it for its full 900s ceiling while the site
    was dark. `forecasting.AsofArchive` removed that arithmetic — the archive
    builds once and each of the ~425 cutoffs evaluates in ~1-2ms (the whole
    three-region forecast half measures ~2s; the locked "never a
    backtest-only estimator" principle holds, because the archive IS
    `forecast_from_rows`'s body). What remains per boot is corpus parse +
    panel reads + marking, well inside the ceiling. Opt out with
    GEOGRAPH_BACKTEST_ON_BOOT=0.
    """
    # OPT-IN SINCE 2026-08-14. Readmitted to the boot the same day AsofArchive
    # made the walk cheap (dd3bbec), which fixed its COMPUTE cost but not the
    # cost that actually matters here: it runs on the boot's background thread
    # before the API opens the graph, so its ~40s land squarely inside the
    # graph-dark window. The ledger it writes lives in Postgres and survives a
    # redeploy, so a routine boot serves the last walk and adds none. Run it
    # with GEOGRAPH_BACKTEST_ON_BOOT=1, or beside the API via
    # scripts/run_backtest.py (read-only on the graph — it never needs the boot).
    if os.getenv("GEOGRAPH_BACKTEST_ON_BOOT", "0").strip().lower() not in {"1", "true", "yes"}:
        return {"ok": True, "skipped": "not a measuring boot (GEOGRAPH_BACKTEST_ON_BOOT)"}
    result = _run_step(
        "paper backtest",
        [sys.executable, str(_BACKTEST_SCRIPT)],
        timeout=_LOAD_TIMEOUT_SECONDS,
        echo=False,
    )
    return {k: v for k, v in result.items() if k != "step"}


def _solve_games() -> dict[str, Any]:
    """The region scenario maps (core/games/scenarios.py) — every active
    dyad solved under the LP correlated equilibrium and the fitted QRE,
    priced, named and explained, persisted to Postgres `game_solutions`.

    OPT-IN like the other measuring steps: it reads the graph read-only
    (CINC, the frozen model, AFFECTED) on the boot thread, so its ~3-4
    minutes would land inside the graph-dark window on every boot; the
    solutions live in Postgres and survive a redeploy, so a routine boot
    serves the last solve. GEOGRAPH_GAMES_ON_BOOT=1 runs it; the fingerprint
    guard then skips it until an input moves.
    """
    if os.getenv("GEOGRAPH_GAMES_ON_BOOT", "0").strip().lower() not in {"1", "true", "yes"}:
        return {"ok": True, "skipped": "not a measuring boot (GEOGRAPH_GAMES_ON_BOOT)"}
    result = _run_step(
        "solve games",
        [sys.executable, str(_GAMES_SCRIPT)],
        timeout=_LOAD_TIMEOUT_SECONDS,
        echo=True,
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

    ONE-SHOT, AND ENFORCED RATHER THAN REQUESTED. The variable used to mean
    "reset on every boot you are set for", with a log line asking twice to
    unset it afterwards. That is not a safeguard, it is a note — and on
    2026-08-13 it was not enough. An env var is STICKY: setting it survives
    every restart, and `restartPolicyMaxRetries: 3` means a container that
    fails for an unrelated reason comes back and wipes the archive again. The
    variable also cannot be set "for the next deploy": setting it IS a deploy,
    which fired it against an image that had not yet shipped the code to honour
    it — one wasted deploy before the real one.

    So the boot now records which value it has already acted on, in a file
    beside the graph, and a value it has seen before is INERT. Leaving the
    variable set forever is now harmless; reset again by giving it a different
    truthy value (`yes` after `1`) or deleting the ledger. The destructive
    action stays opt-in and loud, and it additionally became repeat-proof.
    """
    requested = os.getenv("GEOGRAPH_RESET_GRAPH", "").strip()
    if requested.lower() not in {"1", "true", "yes"}:
        return None

    from core import settings as settings_module

    path = settings_module.load().kuzu_db_path
    ledger = path.with_name(_RESET_LEDGER)
    try:
        honoured = ledger.read_text(encoding="utf-8").strip()
    except OSError:
        # Unreadable or absent. Absent is the normal first-run case; unreadable
        # must not become a permanent lock on a recovery path a human asked
        # for, so both mean "not yet honoured".
        honoured = ""
    if honoured == requested:
        _log(f"GEOGRAPH_RESET_GRAPH={requested} already honoured — graph kept")
        return {"ok": True, "skipped": "already honoured", "token": requested}

    _log("=" * 68)
    _log("GEOGRAPH_RESET_GRAPH is set — DELETING the graph and rebuilding it")
    _log("this fires ONCE for this value; the graph is a cache, not an original")
    _log("=" * 68)

    removed: list[dict[str, Any]] = []
    # Everything Kuzu leaves beside the database, and the repair's own
    # markers: the shadow file, quarantined WALs (`.wal.broken-*`, kept as
    # evidence until a reset), the refill marker and the rebuild ledger. A
    # 5 GB volume has no room for evidence.
    siblings = [path, path.with_name(path.name + ".wal"), path.with_name(path.name + ".tmp"),
                path.with_name(path.name + ".shadow"),
                path.with_name(".affected-refill.json"),
                path.with_name(_REBUILD_AFFECTED_LEDGER)]
    siblings += sorted(path.parent.glob(path.name + ".wal.broken-*"))
    for target in siblings:
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

    # So do the step fingerprints. The `deep` fingerprint in particular reads
    # only the raw cache and the image — neither changes on a reset — so a
    # surviving ledger would skip the deep tier forever and the rebuilt graph
    # would silently lack COW windows, CINC estimates and alliance edges.
    with contextlib.suppress(OSError):
        _fingerprints_path().unlink()
    _log("fingerprints cleared — every guarded step will re-run")

    # THE MEASURED EFFECTS COME BACK FROM THE PANEL, not from the study: the
    # study's watermark says they are measured, so it would never re-measure
    # them. This file asks the `refill` job to project them back once the
    # wire's lean copy is in (core/api/work.py).
    with contextlib.suppress(OSError):
        path.with_name(".affected-refill-pending").write_text("reset", encoding="utf-8")
    _log("refill requested — the refill job projects the panel's effects back after the wire")

    # Record it BEFORE returning, and unconditionally — including when there
    # was nothing to delete. The ledger's claim is "this value has been acted
    # on", not "this value freed bytes"; a reset that found an empty volume has
    # still been acted on, and retrying it every boot is the exact loop this
    # exists to stop. A failure to write is logged and not raised: the rebuild
    # already happened, and a boot that dies here would be a worse outcome than
    # one that might reset twice.
    try:
        ledger.write_text(requested, encoding="utf-8")
    except OSError as exc:
        _log(f"WARNING: could not record the reset ledger ({exc}) — "
             f"unset GEOGRAPH_RESET_GRAPH by hand so this does not repeat")

    freed = sum(int(r["bytes"]) for r in removed)
    return {"ok": True, "removed": removed, "freed_gb": round(freed / 1e9, 2),
            "token": requested}


#: Which GEOGRAPH_DROP_AFFECTED value has already been acted on.
_DROP_AFFECTED_LEDGER = ".affected-drop-honoured"

#: Free bytes below which the boot reclaims what it can from the volume before
#: touching the database. Kuzu needs somewhere to put a WAL record to do
#: ANYTHING — including DROP.
_EMERGENCY_FLOOR_BYTES = 64 << 20


def _free_reclaimable_files() -> dict[str, Any] | None:
    """Delete what the volume holds that is not data, when it is FULL.

    THE DEADLOCK THIS BREAKS. On 2026-08-17 the volume reached 0 bytes free
    with the graph at 4.77 GB of 4.84 GB, and the container entered a crash
    loop in which nothing could get it out: every boot step died with

        IO exception: Cannot write to file. path: /data/geograph.kuzu.wal
        numBytesToWrite: 12 … No space left on device

    — and that included the DROP that exists to free space. A database with no
    room to write a twelve-byte log record cannot delete anything either.

    So the way out has to come from OUTSIDE the database. What the volume
    holds beside the graph is evidence, not data: quarantined write-ahead logs
    (`*.wal.broken-*`, ~20 MB each) left by `kuzu_store.connect` after a
    crashed open, and the raw deep-tier files, which ship in the image and are
    re-fetchable. Deleting the quarantined tails buys the few tens of
    megabytes Kuzu needs to open, drop, and check point — after which the
    dropped table's pages are reusable INSIDE the file, which is what actually
    ends the crash loop.
    """
    from core import settings as settings_module
    from core.graph import kuzu_store

    db_path = settings_module.load().kuzu_db_path
    usage = kuzu_store.disk_usage(db_path)
    if usage is None or usage["free"] > _EMERGENCY_FLOOR_BYTES:
        return None

    _log("=" * 68)
    _log(f"volume has {usage['free'] / 2**20:.0f} MB free — reclaiming what is "
         "not data before touching the graph")
    _log("=" * 68)
    result = kuzu_store.reclaim_non_data(db_path)
    removed = result.get("removed") or []
    freed = int(result.get("freed_bytes") or 0)
    after = result.get("disk") or {}
    _log(f"reclaimed {freed / 2**20:.0f} MB from {len(removed)} file(s): "
         f"{', '.join(removed) or 'nothing to remove'}")
    if after:
        _log(f"volume now: {after.get('free', 0) / 2**20:.0f} MB free")
    return {"ok": True, "freed_bytes": freed, "removed": removed,
            "free_after": after.get("free")}


def _drop_affected_if_asked() -> dict[str, Any] | None:
    """Free the volume by dropping AFFECTED — ONCE per value of the variable.

    THE FAILURE THIS ANSWERS. On 2026-08-17 the study job filled a 5 GB
    Railway volume: 271,886 events and 1,515,105 AFFECTED edges, and
    `disk_free_gb` reached 0.0. A full volume is the uncatchable kill — Kuzu
    aborts on a failed checkpoint, the API dies, the container restart-loops —
    and the Hobby plan's volume ceiling IS 5 GB, so there is no more disk to
    buy without a plan change.

    AFFECTED is the one large table that costs nothing to lose: it is a
    PROJECTION of the panel's `event_study_runs`, which holds every computed
    effect's numbers in Postgres. Dropping it frees the space now; the
    `refill` job puts it back, in slices, with the site up.

    Distinct from GEOGRAPH_REBUILD_AFFECTED, which probes first and drops only
    a table that cannot be WRITTEN. This one drops a table that is perfectly
    healthy and simply too large for the disk it sits on, so it asks no
    questions — and it runs FIRST in the boot, because every step after it
    needs somewhere to write.
    """
    requested = os.getenv("GEOGRAPH_DROP_AFFECTED", "").strip()
    if not requested or requested.lower() in {"0", "false", "no", "off"}:
        return None

    from core import settings as settings_module

    path = settings_module.load().kuzu_db_path
    ledger = path.with_name(_DROP_AFFECTED_LEDGER)
    try:
        honoured = ledger.read_text(encoding="utf-8").strip()
    except OSError:
        honoured = ""
    if not path.exists():
        return {"ok": True, "skipped": "no graph", "token": requested}
    if honoured == requested:
        _log(f"GEOGRAPH_DROP_AFFECTED={requested} already honoured — skipped")
        return {"ok": True, "skipped": "already honoured", "token": requested}

    _log("=" * 68)
    _log("GEOGRAPH_DROP_AFFECTED is set — dropping the AFFECTED table to free "
         "the volume. The panel keeps every measurement; the refill job "
         "projects them back once there is room.")
    _log("=" * 68)
    # `--budget-seconds 0` drops and recreates, then stops the refill at its
    # first chunk boundary: the point is the free space, and refilling here
    # would hand it straight back.
    dropped = _run_step(
        "affected drop",
        [sys.executable, str(_REBUILD_AFFECTED_SCRIPT), "--rebuild",
         "--budget-seconds", "0"],
        timeout=900,
    )
    with contextlib.suppress(OSError):
        ledger.write_text(requested, encoding="utf-8")
    usage = None
    with contextlib.suppress(Exception):
        from core.graph import kuzu_store

        usage = kuzu_store.disk_usage(path)
    if usage:
        _log(f"volume after the drop: {usage['free'] / 2**30:.2f} GB free")
    return {"ok": bool(dropped.get("ok")), "token": requested, "drop": dropped,
            "disk": usage}


def _rebuild_affected_if_asked() -> dict[str, Any] | None:
    """Repair the AFFECTED rel table — ONCE per value of GEOGRAPH_REBUILD_AFFECTED.

    THE FAILURE THIS ANSWERS. On 2026-08-16 every AFFECTED write in production
    died with SIGSEGV — in the API's process and in a child alike — after a
    kill mid-write earlier that day, while every other writer wrote clean and
    AFFECTED itself read clean. The study could not add a measurement without
    taking the site down, so it was switched off, and the market half of every
    surface froze at 1,051,722 edges.

    The repair is `scripts/rebuild_affected.py --repair`: PROBE the table with
    the actual failing operation (a SET and a CREATE through the one writer);
    if the probe returns clean nothing is dropped; if the child dies with a
    signal, DROP and recreate the table and RE-PROJECT it from the panel's
    `event_study_runs`, which holds every computed effect's numbers — minutes,
    not the day and a half a re-measurement would take. Resumable from a
    marker if the ceiling is hit; a second boot with the same value is inert
    (the ledger), so the variable can stay set.

    Runs BEFORE the seeds, while nothing holds the graph — the probe and the
    rebuild both need the single-writer lock, and a seed running first would
    take it.
    """
    requested = os.getenv("GEOGRAPH_REBUILD_AFFECTED", "").strip()
    if not requested or requested.lower() in {"0", "false", "no", "off"}:
        return None

    from core import settings as settings_module

    path = settings_module.load().kuzu_db_path
    ledger = path.with_name(_REBUILD_AFFECTED_LEDGER)
    # The refill's own resume marker (scripts/rebuild_affected.py MARKER_NAME).
    # While it exists a refill was started and not finished — a container
    # killed mid-refill (the healthcheck window, an OOM) leaves a half-filled
    # table on which the PROBE would pass, so the marker outranks the probe
    # and the ledger: resume first, ask questions after.
    marker = path.with_name(".affected-refill.json")
    try:
        honoured = ledger.read_text(encoding="utf-8").strip()
    except OSError:
        honoured = ""
    if not path.exists():
        _log("GEOGRAPH_REBUILD_AFFECTED set but there is no graph yet — nothing to repair")
        return {"ok": True, "skipped": "no graph", "token": requested}
    if marker.exists():
        # NOT IN THE BOOT. A refill is ~200 edges/s (measured 2026-08-16),
        # so a million edges is over an hour of graph-dark time in a boot; the
        # `refill` job finishes it with the site up.
        _log("an AFFECTED refill is unfinished — the refill job completes it behind the API")
        return {"ok": True, "skipped": "refill in progress (the refill job)", "token": requested}
    if honoured == requested:
        _log(f"GEOGRAPH_REBUILD_AFFECTED={requested} already honoured — skipped")
        return {"ok": True, "skipped": "already honoured", "token": requested}

    _log("=" * 68)
    _log("GEOGRAPH_REBUILD_AFFECTED is set — probing the AFFECTED table, "
         "rebuilding from the panel if it cannot be written")
    _log("=" * 68)
    started = time.monotonic()
    # THE PROBE FIRST, ON ITS OWN. If the table is damaged the child dies with
    # a signal — that is the diagnosis, and it must not be confused with a
    # refill that failed for an ordinary reason.
    probe = _run_step(
        "affected probe",
        [sys.executable, str(_REBUILD_AFFECTED_SCRIPT), "--probe"],
        timeout=600,
    )
    outcome: dict[str, Any] = {"token": requested, "probe": probe}
    if probe["ok"]:
        _log("affected probe: the table takes writes — nothing to rebuild")
        outcome["ok"] = True
        outcome["rebuilt"] = False
    else:
        _log("affected probe: FAILED — dropping and re-projecting from the panel")
        rebuilt = _run_step(
            "affected rebuild",
            [sys.executable, str(_REBUILD_AFFECTED_SCRIPT), "--rebuild",
             "--budget-seconds", str(_REBUILD_AFFECTED_TIMEOUT_SECONDS - 120)],
            timeout=_REBUILD_AFFECTED_TIMEOUT_SECONDS,
        )
        outcome["rebuild"] = rebuilt
        outcome["rebuilt"] = True
        # And PROVE it: the same probe against the rebuilt table.
        again = _run_step(
            "affected probe (after rebuild)",
            [sys.executable, str(_REBUILD_AFFECTED_SCRIPT), "--probe"],
            timeout=600,
        )
        outcome["probe_after"] = again
        outcome["ok"] = bool(rebuilt["ok"] and again["ok"])
    outcome["seconds"] = round(time.monotonic() - started, 1)

    # Recorded once the refill is COMPLETE (its marker is gone). An unfinished
    # refill leaves the marker, and the branch above resumes it on the next
    # boot whatever the ledger says; a probe that passed records straight away.
    if not marker.exists():
        try:
            ledger.write_text(requested, encoding="utf-8")
        except OSError as exc:
            _log(f"WARNING: could not record the rebuild ledger ({exc}) — "
                 f"unset GEOGRAPH_REBUILD_AFFECTED by hand so this does not repeat")
    else:
        _log("affected refill did not finish inside its budget — the next boot resumes it")
    return outcome


def _log_disk() -> None:
    """What the volume holds and how much is left — printed once per boot,
    because on 2026-08-16 the answer was 'nothing' and the container
    restart-looped for want of the number."""
    from core import settings as settings_module
    from core.graph import kuzu_store

    path = settings_module.load().kuzu_db_path
    usage = kuzu_store.disk_usage(path)
    if usage:
        _log(f"volume: {usage['used'] / 1e9:.2f} GB used of {usage['total'] / 1e9:.2f} GB, "
             f"{usage['free'] / 1e9:.2f} GB free")
    try:
        entries = []
        for entry in sorted(path.parent.iterdir()):
            try:
                size = (entry.stat().st_size if entry.is_file()
                        else sum(f.stat().st_size for f in entry.rglob("*") if f.is_file()))
            except OSError:
                continue
            if size >= 10_000_000:
                entries.append(f"{entry.name} {size / 1e9:.2f} GB")
        if entries:
            _log("volume holds: " + ", ".join(entries))
    except OSError:
        pass


def _boot_status() -> dict[str, Any]:
    if _disabled():
        _log("seeding disabled by GEOGRAPH_SEED_ON_BOOT")
        return {"seeded": False, "reason": "disabled by GEOGRAPH_SEED_ON_BOOT"}

    # THE VOLUME, FIRST: a full one is the failure nothing below can survive.
    _log_disk()
    # BEFORE anything opens the graph — a reset that runs after the seed has
    # taken the single-writer lock deletes a database out from under a live
    # connection.
    reset = _reset_graph_if_asked()
    # BEFORE THE REPAIR AND BEFORE THE SEEDS: every step after this one needs
    # somewhere to write, and on a full volume there is nowhere. The file
    # sweep comes first because on a FULL volume even the drop cannot run —
    # Kuzu needs room for a WAL record to delete anything.
    reclaimed = _free_reclaimable_files()
    freed = _drop_affected_if_asked()
    repaired = _rebuild_affected_if_asked()

    status: dict[str, Any] = {
        "seeded": True, "packs": [], "panel": None, "prices": None, "study": None,
    }
    if reclaimed is not None:
        status["reclaimed_files"] = reclaimed
    if freed is not None:
        status["dropped_affected"] = freed
    if reset is not None:
        status["reset"] = reset
    if repaired is not None:
        status["affected_rebuild"] = repaired
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
    # API from coming up. The fingerprint wrappers make unchanged inputs cost
    # milliseconds — each names exactly the facets its step READS.

    image = _image_fingerprint()
    steps: tuple[tuple[str, Callable[[], dict[str, Any] | None]], ...] = (
        ("deep", lambda: _guarded(
            "deep", lambda: f"{_raw_listing()}|{image}", _load_deep_tier,
        )),
        # ALWAYS ON, and cheap: the deep tier above is fingerprint-guarded, so
        # a prune that lived only inside it ran when the inputs moved and not
        # otherwise — 489 of 754 off-roster actors survived the first pass on
        # 2026-08-16. Idempotent; seconds when there is nothing to remove.
        ("prune", lambda: _run_step(
            "prune off-roster actors",
            [sys.executable, str(_DEEP_TIER_SCRIPT), "--prune-only"],
            timeout=600,
        )),
        ("gdelt", lambda: _load_gdelt(names)),
        ("rescore", lambda: _rescore_if_new_events(names)),
        ("flows", _load_13f_weekly),
        ("panel", _apply_panel_schema),
        ("prices", lambda: _load_panel_if_shallow(names)),
        # Before the full study, and independent of it: the packs' own events
        # are what every narrated surface reads, and they are cheap.
        ("spine", lambda: _run_spine_study(names)),
        ("study", lambda: _guarded(
            "study",
            lambda: (
                f"{_graph_fingerprint('events', 'latest', 'affected')}"
                f"|{_panel_edge()}|{image}"
            ),
            lambda: _run_study(names),
            # Only a CLEAN pass records: a timed-out or budget-deferred study
            # left a backlog, and a stored fingerprint would strand it.
            complete=lambda r: (
                r is not None and bool(r.get("ok"))
                and all(
                    p.get("ok") and not p.get("skipped")
                    for p in r.get("packs", [])
                )
                and not r.get("skipped")
            ),
        )),
        # metrics / forecasts / scores / backtest / games USED TO RUN HERE.
        # They are jobs now (core/api/work.py), which is the whole point of
        # the convergence loop: the boot's copies re-derived work the running
        # API can do without a deploy, and — because the archive now moves
        # continuously under them — their fingerprints missed on EVERY deploy,
        # adding ~2 minutes of graph-dark time to each one for numbers the
        # loop would refresh within the hour anyway. Run them by hand with
        # scripts/run_network_metrics.py, run_forecasts.py, score_forecasts.py,
        # run_backtest.py, solve_games.py when the API is stopped.
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

    # API-FIRST, THE DEFAULT SINCE 2026-08-14. The old order — every step,
    # THEN exec the API — meant the site was dark for the whole boot (~15
    # minutes on a study boot), because nothing listened on the port until
    # the write steps released the Kuzu lock. Inverted: exec the API NOW; its
    # lifespan sees GEOGRAPH_RUN_BOOT_IN_APP and runs `_boot_status()` on a
    # background thread, holding NO graph connection until the last write
    # child exits (Kuzu allows one writer OR readers, never both across
    # processes). The corpus-first surfaces serve immediately; graph
    # endpoints answer 503 naming the boot until it opens. Dark time drops
    # from the boot's length to the corpus warm (~20s).
    # GEOGRAPH_API_FIRST=0 restores the old serialised order.
    api_first = os.getenv("GEOGRAPH_API_FIRST", "1").strip().lower() not in {
        "0", "false", "no",
    }
    if api_first:
        os.environ["GEOGRAPH_RUN_BOOT_IN_APP"] = "1"
        _log(f"api-first: exec {' '.join(argv)} — boot runs behind the bound port")
        os.execvp(argv[0], argv)

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
