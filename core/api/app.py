"""The GeoGraph API — FastAPI serving queries, meta and the built explorer.

`python -m core.api.app` on a fresh clone starts the app and creates the
graph file with the ontology's schema applied — the MarketGraph zero-config
property, kept. ONE ORIGIN: the Vite build (web/dist) is served by this same
process, so the frontend can never be deployed at a version that disagrees
with the API it calls.

/api/health RETURNS 200 EVEN WHILE THE GRAPH IS EMPTY. A health check that
waited for data would restart-loop a working container on Railway. The
payload carries what is actually available; the check only proves the process
serves.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException, Response
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
except ModuleNotFoundError as exc:
    # `python -m core.api.app` from a shell whose `python` is the system
    # interpreter, not the project venv — the raw traceback names fastapi and
    # misdirects toward "pip install fastapi" into the wrong Python.
    import sys

    _venv = Path(__file__).resolve().parents[2] / ".venv"
    _venv_python = _venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if _venv_python.exists():
        _fix = (
            f"a venv already exists; run:\n\n    {_venv_python} -m core.api.app\n\n"
            "or activate it first."
        )
    else:
        _fix = (
            "create one:\n\n    python3.12 -m venv .venv\n"
            "    .venv/bin/pip install -e '.[dev,api]'"
        )
    raise SystemExit(
        f"'{exc.name}' is not installed in this Python ({sys.executable}).\n"
        f"That is the system interpreter, not the project venv; {_fix}"
    ) from exc

from core import settings as settings_module
from core.api.routers import (
    case_studies,
    dyads,
    events,
    forecasts,
    games,
    graph,
    impact,
    network,
    packs,
    precedent,
    reasoning,
    regimes,
    trading,
)
from core.graph import kuzu_store

_WEB_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


class _ImmutableStaticFiles(StaticFiles):
    """The Vite bundles under /assets are content-hashed — a changed file is a
    changed URL — so they are safe to cache forever. Without this header every
    page load revalidates every bundle (a 304 per asset per visit)."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _boot_status() -> dict[str, Any] | None:
    """What scripts/boot.py did before exec'ing this process.

    A seed that failed leaves the API serving a thin graph, which otherwise
    looks exactly like a graph nobody has seeded yet. This is how the two are
    told apart without reading container logs.
    """
    raw = os.getenv("GEOGRAPH_BOOT_STATUS", "").strip()
    if not raw:
        return None
    try:
        return dict(json.loads(raw))
    except (ValueError, TypeError):
        return {"error": "GEOGRAPH_BOOT_STATUS is not readable JSON"}


def _open_graph(app: FastAPI, settings: Any) -> None:
    """Open the graph in write mode and apply the ontology's schema. Failure
    is RECORDED, not raised — /api/health stays 200 and names the problem."""
    try:
        settings.kuzu_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = kuzu_store.connect(settings.kuzu_db_path)
        kuzu_store.apply_schema(conn)
        app.state.graph = conn
        app.state.graph_error = None
    except Exception as exc:  # noqa: BLE001 - see docstring
        app.state.graph = None
        app.state.graph_error = str(exc)
    _start_jobs(app, settings)


def _start_jobs(app: FastAPI, settings: Any) -> None:
    """THE CONVERGENCE LOOP (core/api/jobs.py), started once the graph is open.

    This process holds the single-writer lock, so it is the only place that
    can do the archive's recurring work without a deploy — and a deploy is
    downtime here, because the volume mounts to one instance. Off with
    GEOGRAPH_JOBS=0; individual jobs with GEOGRAPH_JOB_<NAME>=0.
    """
    if os.getenv("GEOGRAPH_JOBS", "1").strip().lower() in {"0", "false", "no"}:
        app.state.jobs = None
        return
    if getattr(app.state, "jobs", None) is not None or app.state.graph is None:
        return
    try:
        from core.api import jobs as jobs_module
        from core.api import work

        # Writes in a SERVING process are sized for read latency: the graph
        # lock is FIFO, so one statement is what a reader ever waits for.
        kuzu_store.BATCH_ROWS = jobs_module.SERVING_BATCH_ROWS

        scheduler = jobs_module.Scheduler(app, settings, [
            # FIRST, deliberately. The scheduler runs due jobs in list order,
            # so anything behind a 45s write slice lands minutes after a
            # restart — and /api/stats cold-scans twenty tables (19.7s at a
            # million events), which is exactly what the front page opens
            # with. The cheap warmers go before the writers so the first
            # reader after a deploy is never the one who pays.
            jobs_module.Job(
                name="counts", every=240.0, run=work.counts,
                enabled=jobs_module._enabled("counts"),
                slice_seconds=120.0,
            ),
            jobs_module.Job(
                name="games", every=60.0, run=work.games,
                enabled=jobs_module._enabled("games"),
                slice_seconds=240.0,  # one region's solve is ~70s and atomic
            ),
            # Cadences are about the writer's share of the process, not about
            # urgency: a slice every few minutes converges a hundred-thousand
            # event archive in days while staying invisible to a reader.
            # `child=True` means "this job MAY return a plan for the
            # scheduler to run as a child" — not that it always does. The
            # study runs in-process now that AFFECTED writes go through
            # `write_edges` (no MERGE adjacency scan), and falls back to a
            # child for the process's life if Kuzu's storage assertion ever
            # returns. The child rate-limits itself, because its slice takes
            # the graph dark; the in-process path costs nothing, so this
            # cadence is the ordinary one.
            jobs_module.Job(
                name="study", every=120.0, run=work.study,
                enabled=jobs_module._enabled("study"),
                slice_seconds=60.0, child=True,
            ),
            # The wire load is the heaviest writer (~145 events/sec into Kuzu)
            # and the one that was a downtime decision: mena's 450k artifact
            # events never reached the graph after the 2026-08-15 rebuild, so
            # its roster dyads held 351 events and nothing could be measured
            # against them. A 50% duty cycle converges it in a few hours with
            # the site up.
            # Head B over what the wire loaded. Runs close behind it, because
            # an event in the graph without escalation fields is an event the
            # dyad pages and the structural layer read as a null.
            jobs_module.Job(
                name="rescore", every=120.0, run=work.rescore,
                enabled=jobs_module._enabled("rescore"),
                slice_seconds=60.0,
            ),
            # The frozen calls, re-frozen as the archive converges. Expensive
            # (minutes) and gated on real growth, so it runs rarely.
            jobs_module.Job(
                name="forecasts", every=1800.0, run=work.forecasts,
                enabled=jobs_module._enabled("forecasts"),
                slice_seconds=600.0,
            ),
            # What has resolved, scored; what cannot be, retrodicted. Follows
            # the freeze, since it reads what the freeze wrote.
            jobs_module.Job(
                name="scores", every=1800.0, run=work.scores,
                enabled=jobs_module._enabled("scores"),
                slice_seconds=300.0,
            ),
            # The explorer's windowed network, and the paper book. Both are
            # functions of an archive that is now moving under them.
            jobs_module.Job(
                name="metrics", every=3600.0, run=work.metrics,
                enabled=jobs_module._enabled("metrics"),
                slice_seconds=300.0,
            ),
            jobs_module.Job(
                name="backtest", every=3600.0, run=work.backtest,
                enabled=jobs_module._enabled("backtest"),
                slice_seconds=300.0,
            ),
            # The scoreboard: seconds per region, and it must not be the first
            # reader's problem — the archive read alone grows with the wire.
            jobs_module.Job(
                name="calibration", every=1800.0, run=work.calibration,
                enabled=jobs_module._enabled("calibration"),
                slice_seconds=180.0,
            ),
            jobs_module.Job(
                name="wire", every=60.0, run=work.wire,
                enabled=jobs_module._enabled("wire"),
                slice_seconds=60.0,
            ),
        ])
        scheduler.start()
        app.state.jobs = scheduler
    except Exception as exc:  # noqa: BLE001 - the API serves with or without it
        app.state.jobs = None
        app.state.jobs_error = str(exc)


def _run_boot_behind_the_api(app: FastAPI, settings: Any) -> None:
    """The API-first boot's background half (scripts/boot.py sets
    GEOGRAPH_RUN_BOOT_IN_APP and execs this process immediately).

    Runs every boot step — each a child process that takes and releases the
    Kuzu write lock — while THIS process holds no graph connection at all
    (Kuzu allows one writer or many readers across processes, never both).
    Only when the last write child has exited does the API take the write
    lock itself and start serving graph reads. Until then the corpus-first
    surfaces work and graph endpoints answer 503 naming the boot.
    """
    import importlib.util

    try:
        boot_path = Path(__file__).resolve().parents[2] / "scripts" / "boot.py"
        spec = importlib.util.spec_from_file_location("geograph_boot", boot_path)
        assert spec and spec.loader
        boot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(boot)
        status: dict[str, Any] = boot._boot_status()
    except Exception as exc:  # noqa: BLE001 - the API must keep serving
        status = {"seeded": False, "reason": f"background boot error: {exc}"}
    app.state.boot = {"running": False, **status}
    _open_graph(app, settings)


def create_app() -> FastAPI:
    settings = settings_module.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # API-FIRST BOOT: when scripts/boot.py exec'd us before running its
        # steps, the steps run on a background thread BEHIND the bound port —
        # this process must hold no graph connection while the write children
        # work, so the graph opens when the thread finishes. Otherwise (a
        # plain `python -m core.api.app`, or the legacy serialised boot) the
        # graph opens here at startup, which is what makes a fresh clone
        # serve: an empty graph with the right schema beats a 500.
        if os.getenv("GEOGRAPH_RUN_BOOT_IN_APP", "").strip() == "1":
            import threading

            app.state.graph_error = (
                "boot in progress — the graph opens when the boot's write "
                "steps release the single-writer lock (watch /api/health)"
            )
            app.state.boot = {"running": True}
            threading.Thread(
                target=_run_boot_behind_the_api,
                args=(app, settings),
                daemon=True,
                name="geograph-boot",
            ).start()
        else:
            _open_graph(app, settings)
        # Warm the corpus's serving tables NOW, off the artifacts in the image,
        # so the parse cost (~20s for three lenses) lands in startup instead of
        # on a user's first click at the dyad ledger. Record, never raise — a
        # corpus that cannot warm leaves the routers on their graph fallback,
        # which is degraded and says so, not dead.
        try:
            from core.wire import serving

            serving.warm()
        except Exception as exc:  # noqa: BLE001 - see above
            # Its OWN field: in API-first mode graph_error already holds the
            # truthy boot-in-progress note, so `graph_error or ...` dropped
            # the corpus failure — and _open_graph later cleared the field
            # anyway. The corpus is the primary serving path for the dyad,
            # games and precedent surfaces; its failure must survive to
            # /api/health.
            app.state.corpus_error = f"corpus: {exc}"
        try:
            yield
        finally:
            # Stop the convergence loop BEFORE the database goes: a job mid-
            # write against a closed database is the one way this could
            # corrupt something, and the loop's own bound makes waiting short.
            if getattr(app.state, "jobs", None) is not None:
                # Blocks until the batch in flight commits (bounded). A write
                # killed mid-transaction is the one way this loop could damage
                # the volume, and a deploy is exactly when that would happen.
                app.state.jobs.stop()
            # Hand the write lock back on shutdown, so a batch job can take it
            # without waiting for the process to be reaped.
            kuzu_store.close(app.state.graph)
            app.state.graph = None

    app = FastAPI(title="GeoGraph", version="0.0.1", lifespan=lifespan)
    app.state.settings = settings
    # Set BEFORE startup runs, so a handler reached during startup — or after a
    # failed one — reads a defined state instead of raising AttributeError.
    app.state.graph = None
    app.state.graph_error = None
    app.state.corpus_error = None
    app.state.jobs = None
    app.state.jobs_error = None

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "graph": "unavailable" if app.state.graph is None else "open",
            "graphError": app.state.graph_error,
            "corpusError": app.state.corpus_error,
            "disabled": settings.missing_capabilities(),
            # Live state when the boot runs behind this API (api-first),
            # else whatever the serialised boot handed over in the env.
            "boot": getattr(app.state, "boot", None) or _boot_status(),
            # The boot is now a small part of the story: the archive converges
            # in the background (core/api/jobs.py) rather than per deploy.
            "jobs": (
                app.state.jobs.status() if getattr(app.state, "jobs", None)
                else {"running": False, "error": app.state.jobs_error}
            ),
        }

    @app.get("/api/jobs")
    def jobs_status() -> dict[str, Any]:
        """What the convergence loop has done and what is left.

        A background process nobody can watch is a background process nobody
        trusts — and the honest number here (how much of the archive is still
        unmeasured) is one a reader is entitled to before believing a coverage
        figure anywhere else on the surface.
        """
        if getattr(app.state, "jobs", None) is None:
            return {
                "running": False,
                "error": app.state.jobs_error,
                "note": (
                    "the convergence loop is off (GEOGRAPH_JOBS=0) or the graph "
                    "is not open yet; recurring work then happens only on a boot"
                ),
                "jobs": [],
            }
        return app.state.jobs.status()

    @app.get("/api/ready")
    def ready(response: Response) -> dict[str, Any]:
        """THE RAILWAY HEALTHCHECK TARGET (railway.json) since 2026-08-15:
        200 only once the graph is open, 503 while the boot's write steps
        hold the lock. Railway keeps the PREVIOUS deployment serving until
        the new one passes its healthcheck, so gating readiness on the graph
        turns every routine deploy's graph-dark minute — and a measuring
        deploy's ten — into zero user-visible downtime: the old container
        answers while the new one boots. /api/health stays 200-always for
        the UI's own status banner. GEOGRAPH_READY_IGNORES_GRAPH=1 restores
        the old always-200 readiness for a deploy whose boot cannot open the
        graph inside the healthcheck window (a bulk GDELT load) — that is
        the one case where a waiting healthcheck would kill a working boot."""
        import os

        ignore = os.getenv("GEOGRAPH_READY_IGNORES_GRAPH", "0").strip().lower() in {
            "1", "true", "yes",
        }
        open_ = app.state.graph is not None
        if not (open_ or ignore):
            response.status_code = 503
        return {
            "ready": open_ or ignore,
            "graph": "open" if open_ else "unavailable",
            "graphError": app.state.graph_error,
        }

    for router in (graph.router, events.router, case_studies.router, network.router,
                   forecasts.router, regimes.router, packs.router, trading.router,
                   reasoning.router, dyads.router, precedent.router,
                   games.router, impact.router):
        app.include_router(router, prefix="/api")

    if _WEB_DIST.exists():
        app.mount("/assets", _ImmutableStaticFiles(directory=_WEB_DIST / "assets"), name="assets")

        # SPA catch-all LAST, and never for /api paths: an unknown API route
        # must 404 as JSON, not 200 as index.html — the MarketGraph mount
        # lesson, applied before it bites.
        #
        # index.html (and every non-hashed root file) is no-cache: without an
        # explicit Cache-Control, browsers apply HEURISTIC caching and keep a
        # stale index.html across deploys — which then references content-hashed
        # bundles the new container no longer ships, and the page breaks until a
        # hard refresh. no-cache means revalidate-every-time; the ETag makes
        # that a 304, so it stays cheap while deploys become visible instantly.
        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(status_code=404, detail=f"no such API route: /{path}")
            # Containment check: a percent-encoded '..' (%2e%2e) survives HTTP
            # normalisation and reaches this handler literally, so a bare
            # `_WEB_DIST / path` would serve any file the process can read —
            # source, the graph on /data, secrets. Resolve, then require the
            # result to still live under dist; anything else is a SPA route.
            candidate = (_WEB_DIST / path).resolve()
            inside = candidate.is_relative_to(_WEB_DIST.resolve())
            if not (path and inside and candidate.is_file()):
                candidate = _WEB_DIST / "index.html"
            return FileResponse(candidate, headers={"Cache-Control": "no-cache"})

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=settings_module.load().port)


if __name__ == "__main__":
    main()
