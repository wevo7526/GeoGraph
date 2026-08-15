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
    from fastapi import FastAPI, HTTPException
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
