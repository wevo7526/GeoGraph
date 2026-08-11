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

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core import settings as settings_module
from core.api.routers import events, forecasts, graph, network, packs, regimes
from core.graph import kuzu_store

_WEB_DIST = Path(__file__).resolve().parent.parent.parent / "web" / "dist"


def create_app() -> FastAPI:
    settings = settings_module.load()
    app = FastAPI(title="GeoGraph", version="0.0.1")
    app.state.settings = settings
    app.state.graph_error = None

    @app.on_event("startup")
    def _open_graph() -> None:
        # Creating-and-applying on startup is what makes a fresh clone serve:
        # an empty graph with the right schema beats a 500. Failure is
        # RECORDED, not raised — /api/health stays 200 and names the problem.
        try:
            settings.kuzu_db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = kuzu_store.connect(settings.kuzu_db_path)
            kuzu_store.apply_schema(conn)
            app.state.graph = conn
        except kuzu_store.GraphUnavailable as exc:
            app.state.graph = None
            app.state.graph_error = str(exc)

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "graph": "unavailable" if app.state.graph is None else "open",
            "graphError": app.state.graph_error,
            "disabled": settings.missing_capabilities(),
        }

    for router in (graph.router, events.router, network.router,
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
