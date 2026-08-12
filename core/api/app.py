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
    events,
    forecasts,
    graph,
    network,
    packs,
    regimes,
)
from core.graph import kuzu_store

_WEB_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


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


def create_app() -> FastAPI:
    settings = settings_module.load()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Creating-and-applying at startup is what makes a fresh clone serve:
        # an empty graph with the right schema beats a 500. Failure is
        # RECORDED, not raised — /api/health stays 200 and names the problem.
        # The except is deliberately broad: ANY unhandled failure here would
        # otherwise leave the app unable to answer its own health check, and a
        # health check that 500s restart-loops a container that could have told
        # us what was wrong.
        try:
            settings.kuzu_db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = kuzu_store.connect(settings.kuzu_db_path)
            kuzu_store.apply_schema(conn)
            app.state.graph = conn
        except Exception as exc:  # noqa: BLE001 - see above
            app.state.graph = None
            app.state.graph_error = str(exc)
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

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "graph": "unavailable" if app.state.graph is None else "open",
            "graphError": app.state.graph_error,
            "disabled": settings.missing_capabilities(),
            "boot": _boot_status(),
        }

    for router in (graph.router, events.router, case_studies.router, network.router,
                   forecasts.router, regimes.router, packs.router):
        app.include_router(router, prefix="/api")

    if _WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=_WEB_DIST / "assets"), name="assets")

        # SPA catch-all LAST, and never for /api paths: an unknown API route
        # must 404 as JSON, not 200 as index.html — the MarketGraph mount
        # lesson, applied before it bites.
        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(status_code=404, detail=f"no such API route: /{path}")
            candidate = _WEB_DIST / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(_WEB_DIST / "index.html")

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=settings_module.load().port)


if __name__ == "__main__":
    main()
