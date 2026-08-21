# AGENTS.md

## Cursor Cloud specific instructions

GeoGraph is a single product: a FastAPI backend that serves both the JSON API and
the prebuilt React/Vite explorer from one origin on **port 8000**. Standard
setup/run/verify commands live in `README.md` ("Quick start" and "Verification");
prefer those and only rely on the non-obvious notes below.

### Environment layout
- Python deps live in a project virtualenv at `.venv/` (gitignored). Invoke tools
  as `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/ruff`, `.venv/bin/mypy`
  (or activate the venv). The startup update script recreates it.
- Node is v22; the web workspace is `web/` (`npm --prefix web ...`).
- A gitignored `.env` sets `DATABASE_URL` for the local Postgres panel. Every
  other setting is optional (see `.env.example`); unset ones just disable one
  capability, reported at `/api/health`.

### PostgreSQL (the "panel") is required for full end-to-end use
- Postgres is installed but **not auto-started** (no systemd). Start it each boot:
  `sudo pg_ctlcluster 16 main start`.
- Local dev credentials: role `geograph` / password `geograph`, database
  `geograph` → `DATABASE_URL=postgresql://geograph:geograph@127.0.0.1:5432/geograph`.
- The app still boots without Postgres, but the transmission/event-study engine,
  persisted game solutions, and the paper book are disabled without it.

### Kuzu graph is embedded and SINGLE-WRITER — the key gotcha
- The graph is just a directory at `data/geograph.kuzu` (gitignored); there is no
  graph server to run. `KUZU_DB_PATH` overrides its location.
- Only ONE process may hold the graph write lock. The running API holds it, so you
  **cannot run any write script** (`scripts/seed_pack.py`, `apply_schema.py`,
  `run_event_study.py`, `load_panel.py`, `backfill_gdelt.py`, etc.) while
  `python -m core.api.app` is running. Stop the API first, run the script, then
  restart the API. Read-only scripts (e.g. `run_backtest.py`) are fine.

### Running the app locally (not via the container boot)
1. `sudo pg_ctlcluster 16 main start`
2. `.venv/bin/python scripts/apply_schema.py`  (Kuzu DDL + panel DDL)
3. `.venv/bin/python scripts/seed_pack.py mena`  (or `china` / `eurasia`; a broken
   graph write here usually means the API is still holding the lock)
4. Optional but needed to measure market impact:
   `.venv/bin/python scripts/load_panel.py mena` (yfinance, needs network), then
   `.venv/bin/python scripts/run_event_study.py mena --spine` (writes `AFFECTED`)
5. `.venv/bin/python -m core.api.app`  → serves on `:8000`.
- **Startup warms the ~1.3M-event wire corpus before it answers**: uvicorn logs
  "Waiting for application startup" for ~30–40s; `/api/ready` returns 200 when
  ready. This is not a hang.
- `scripts/boot.py` is the *container* entrypoint (Railway). It runs API-first and
  gates heavy loads behind `GEOGRAPH_*_ON_BOOT` flags; you generally don't need it
  for local dev — run the scripts above directly.

### Convergence-loop background jobs
- Once the API is up, in-process jobs (see `/api/jobs`) compute the Markets story,
  region game solutions, forecasts, etc. Until the first pass finishes, the
  **Markets** page shows "the markets story is being written" and **Game theory**
  shows "Solving the region…". This is expected right after startup, not a bug.

### Frontend
- The API serves the prebuilt bundle from `web/dist`. After changing web sources,
  rebuild with `npm --prefix web run build`.
- For hot-reload dev use `npm --prefix web run dev` (Vite on `:5173`, proxies
  `/api` to `:8000` — keep the API running too).

### Verify (from README "Verification")
- Tests: `.venv/bin/pytest` — full suite passes (~4 min; no DB/network needed).
- Lint: `.venv/bin/ruff check .`  ·  Types: `.venv/bin/mypy .`. Note: the pinned
  ranges resolve to newer `ruff`/`mypy` majors that surface a few pre-existing
  findings in the current tree; these are not caused by environment setup.
