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

import gzip
import json
import os
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
_GDELT_BUDGET_SECONDS = 3600

#: The archive-wide rescore's own ceiling, separate and much larger because it
#: is the one step here that CANNOT be resumed. Head B folds escalation per
#: dyad across every era at once; an interrupted run leaves nothing behind and
#: the next boot starts from the beginning, so a timeout that cuts it short
#: does not slow the archive down, it stops it converging at all.
#:
#: Sized from measurement: 456,711 events rescored locally in ~29 minutes,
#: about 262/sec. Production will hold roughly 1.5M across three lenses, and
#: Railway is slower than a laptop, so the honest budget is hours. It only
#: runs on the boot where loading is already complete, so it is not competing
#: with the load for the window.
_RESCORE_TIMEOUT_SECONDS = int(os.getenv("GEOGRAPH_RESCORE_TIMEOUT", "9000"))

#: WHAT THIS BUDGET MEANS AFTER THE 2006–2026 BACKFILL, stated so nobody reads
#: a deferred load as a broken one. The modern-era harvest adds roughly a
#: million events across the three lenses; at the ~110 events/sec measured on
#: Railway that is about 2.7 hours of merging, against an hour of budget and a
#: 5400s healthcheck window the API has to bind inside.
#:
#: So the FIRST boots after that backfill will run out of budget and stop
#: partway, on purpose. That is the resumable design working: the loader skips
#: ids already present, the completeness check compares against the SUM of
#: every artifact, and each boot picks up where the last stopped — expect two
#: or three deploys before a lens reports its full count. Raising this to
#: swallow it in one go would push the boot past the healthcheck and kill a
#: container that was working, which is the exact failure of 2026-08-12.

#: The full-archive study's ceiling. With the measured-events watermark only
#: NEW events pay compute, so a normal boot uses seconds of this — the
#: ceiling exists for the first boot after a large backfill.
_STUDY_TIMEOUT_SECONDS = 1500

#: How far before the spine's EARLIEST event the panel must reach: the
#: estimation window (120 sessions) plus its gap and the measurement windows,
#: with slack for weekends and holidays. Matches run_event_study._LOOKBACK_DAYS.
_LOOKBACK_DAYS = 400


def _log(message: str) -> None:
    print(f"boot: {message}", flush=True)


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
    results = [
        _run_step(
            f"event study {name}",
            [sys.executable, str(_STUDY_SCRIPT), name, "--all"],
            timeout=_STUDY_TIMEOUT_SECONDS,
            echo=False,
        )
        for name in pack_names
    ]
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
    result = _run_step(
        "deep tier",
        [sys.executable, str(_DEEP_TIER_SCRIPT)],
        timeout=_LOAD_TIMEOUT_SECONDS,
        echo=False,
    )
    return {k: v for k, v in result.items() if k != "step"}


def _artifact_events(artifact: Path) -> int:
    """How many events the shipped artifact holds — one line each.

    The artifact was written by `parse_lines` with `keep_lines`, so it holds
    ONLY lines that already passed the filter: its line count IS the number
    of events a complete load produces. Counting costs ~0.1s on a 3 MB gz,
    which is what makes the completeness check below affordable every boot.
    """
    with gzip.open(artifact, "rb") as fh:
        return sum(1 for _ in fh)


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
    budget = float(_GDELT_BUDGET_SECONDS)
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
        expected = sum(_artifact_events(artifact) for artifact in artifacts)
        held = _graph_gdelt_count(name)
        if held is not None and held >= expected:
            results.append({"pack": name, "ok": True,
                            "skipped": f"graph holds {held}/{expected}"})
            continue
        if budget <= 0:
            _log(f"gdelt {name}: out of budget — {held}/{expected}, deferred")
            results.append({"pack": name, "ok": True, "expected": expected,
                            "held": held, "skipped": "GDELT budget spent"})
            continue
        if held:
            _log(f"gdelt {name}: graph holds {held}/{expected} — resuming the rest")
        steps = []
        for artifact in artifacts:
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
            steps.append(_run_step(
                f"gdelt backfill {name} {artifact.stem}",
                [sys.executable, str(_GDELT_SCRIPT), name,
                 "--from-filtered", str(artifact), "--skip-rescore"],
                timeout=int(min(_GDELT_TIMEOUT_SECONDS, budget)),
                echo=False,
            ))
            budget -= time.monotonic() - started
        after = _graph_gdelt_count(name)
        results.append({
            "pack": name, "expected": expected, "held": after,
            "artifacts": len(artifacts), "loaded": len(steps),
            "ok": all(s["ok"] for s in steps) if steps else True,
        })
        if after is not None and after < expected:
            _log(f"gdelt {name}: STILL SHORT — {after}/{expected}")

    # ONE rescore, and ONLY once every lens is fully loaded.
    #
    # Escalation is relational and archive-wide — a dyad's baseline depends on
    # events from other lenses and other eras — so this cannot be split per
    # pack, and it is NOT RESUMABLE: an interrupted run leaves nothing behind
    # and the next boot starts over.
    #
    # That combination is why it waits for completeness. The 2006-2026 backfill
    # takes two or three boots to load, and rescoring after each of them would
    # spend an hour rewriting an archive that is about to grow again, be killed
    # by its own timeout, and never finish. Waiting means it runs on the boot
    # where loading is already skipped — so the whole window is available for
    # the one job that needs an uninterrupted stretch.
    #
    # Measured locally: 456,711 events in ~29 minutes, ~262/sec. Production
    # will hold roughly 1.5M across three lenses, so budget hours rather than
    # minutes and see _RESCORE_TIMEOUT_SECONDS.
    complete = [
        r for r in results
        if r.get("expected") is not None and r.get("held") is not None
    ]
    short = [
        r for r in complete
        if int(str(r["held"])) < int(str(r["expected"]))
    ]
    if short:
        _log(
            "rescore deferred — "
            + ", ".join(f"{r['pack']} {r['held']}/{r['expected']}" for r in short)
            + " (it is archive-wide and not resumable; it runs when loading ends)"
        )
        results.append({"pack": "*", "step": "rescore", "ok": True,
                        "skipped": "archive still loading"})
        return {"ok": all(r["ok"] for r in results), "packs": results}

    loaded_any = any(r.get("loaded") for r in results)
    if loaded_any:
        rescore = _run_step(
            "gdelt rescore (archive-wide, once, not resumable)",
            [sys.executable, str(_GDELT_SCRIPT), pack_names[0], "--rescore-only"],
            timeout=int(_RESCORE_TIMEOUT_SECONDS),
            echo=False,
        )
        results.append({"pack": "*", "step": "rescore", **{
            k: v for k, v in rescore.items() if k != "step"
        }})
    return {"ok": all(r["ok"] for r in results), "packs": results}


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


def _boot_status() -> dict[str, Any]:
    if _disabled():
        _log("seeding disabled by GEOGRAPH_SEED_ON_BOOT")
        return {"seeded": False, "reason": "disabled by GEOGRAPH_SEED_ON_BOOT"}

    status: dict[str, Any] = {
        "seeded": True, "packs": [], "panel": None, "prices": None, "study": None,
    }
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
