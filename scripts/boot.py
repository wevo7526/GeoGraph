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

The price fetch is CONDITIONAL on the panel being empty, because it is the one
step that reaches the network and it does not need repeating: Postgres survives
a redeploy, so a loaded panel stays loaded. The event study is not conditional
— it is pure computation over the panel, and the graph it writes into lives on
a volume that may have been rebuilt, so it runs every boot and re-derives the
same numbers.
"""

from __future__ import annotations

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
_DEEP_TIER_SCRIPT = _ROOT / "scripts" / "load_deep_tier.py"

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
            timeout=_SEED_TIMEOUT_SECONDS,
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
        ("panel", _apply_panel_schema),
        ("prices", lambda: _load_panel_if_shallow(names)),
        ("study", lambda: _run_study(names)),
        ("metrics", _run_network_metrics),
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
