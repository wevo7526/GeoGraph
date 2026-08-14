# GeoGraph — one image for the API + explorer; Postgres is a separate Railway
# service reached via DATABASE_URL.
#
# Stage 1 builds the TypeScript explorer; stage 2 serves the API and the built
# assets from one FastAPI process. ONE ORIGIN is the point: no CORS, one URL,
# one health check, and the frontend can never deploy at a version that
# disagrees with the API it calls.
#
# THE GRAPH IS A DIRECTORY INSIDE THE CONTAINER on a mounted volume. Kuzu is
# embedded — no graph service to provision. The volume at /data is what makes
# the graph survive a redeploy.

# ── stage 1: the explorer ────────────────────────────────────────────────────
FROM node:22-alpine AS web

WORKDIR /web

# Manifests first so a source-only change reuses the cached dependency layer.
# `npm ci` honours the lockfile exactly — a deploy reproduces the tested build.
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY web/ ./
RUN npm run build


# ── stage 2: the API + the built explorer ────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependency metadata before source, same caching reason. Installed from
# pyproject so the image and a local dev install cannot drift apart.
#
# THEN THE PROJECT ITSELF IS UNINSTALLED. `pip install .` over the one-file
# skeleton below also installs `core` into site-packages as JUST __init__.py —
# and a script run as `python /app/scripts/boot.py` has /app/scripts on
# sys.path, not /app, so `import core.packs` found the skeleton and the boot
# seed died with "cannot import name 'packs'" while the API served an empty
# graph. The dependencies stay; the package must resolve to /app/core (via
# PYTHONPATH below), not least because core/packs.py locates packs/ RELATIVE
# to core's own location.
# pyproject (plus the package skeleton) keys the dependency layer. README.md
# used to be COPYed here because pyproject references it — but that made a
# README edit rebuild the most expensive layer in the image, so an empty stub
# stands in for it (pip only needs the file to exist; the built package is
# uninstalled below anyway). core/__init__.py still keys this layer — it holds
# the package invariant and changes about never.
COPY pyproject.toml ./
RUN touch README.md && mkdir -p core
COPY core/__init__.py core/
# `ingest` and `analysis` are not optional in the image: boot loads the price
# panel with yfinance/FRED and computes NetworkMetric with networkx before the
# API starts. Without the extras those steps fail on import and the archive
# quietly serves less than it claims — "panel empty" and zero metrics read as
# facts about the world when they are facts about the wheel.
# `reasoning` ships in the image so ANTHROPIC_API_KEY is the ONLY gate on the
# agent in production — adding the key must not also require a dependency
# redeploy.
RUN pip install --no-cache-dir ".[api,panel,mcp,ingest,analysis,reasoning]" \
 && pip uninstall --yes geograph

ENV PYTHONPATH=/app

# Non-root at runtime — but deliberately NOT via `USER` (the MarketGraph
# volume lesson): a volume mounted at /data lands OVER the build-time
# directory, owned by root, and a USER-pinned container dies at startup with
# "Permission denied". The entrypoint starts as root, fixes the ownership of
# the mount that actually exists, and drops privileges before exec'ing.
# Created HERE, before the source COPYs, so each COPY can --chown as it lands
# — a trailing `chown -R /app` re-materialised ~78MB of already-copied layers
# on every build.
RUN useradd --create-home --uid 10001 geograph

# LEAST-CHANGED FIRST, so a code edit invalidates as little as possible. The
# derived GDELT artifacts (68MB — the kept raw lines of the backfills, so a
# boot loads a hundred thousand events in minutes instead of parsing
# sixty-one million lines) change on a re-harvest; the TRAINED ARTIFACTS
# (models/ — offline fits, committed precisely so the image can carry them;
# absent until 2026-08-14, which shipped the learned layer as dead code) on a
# re-fit; packs on a curation pass; core/scripts on every working day.
COPY --chown=geograph data/derived/ data/derived/
COPY --chown=geograph models/ models/
# The packs are INPUTS the seed needs, not build artifacts.
COPY --chown=geograph packs/ packs/
# The ontology and crosswalks are not documentation: kuzu_schema.py reads the
# YAML at runtime to generate the schema and validators — the image does not
# boot without them. (They live under core/; named here so nobody "slims"
# them out.)
COPY --chown=geograph core/ core/
COPY --chown=geograph scripts/ scripts/

COPY --from=web --chown=geograph /web/dist ./web/dist

# The graph lives here. Mount a Railway volume at /data and it survives a
# redeploy; leave it unmounted and the app reseeds on every boot.
ENV KUZU_DB_PATH=/data/geograph.kuzu
# Deep-tier raw downloads (COW, Shiller) cache on the same volume, so a
# redeploy re-loads from disk instead of re-fetching the archives.
ENV GEOGRAPH_RAW_DIR=/data/raw
RUN mkdir -p /data && chown geograph /data /app

COPY docker-entrypoint.py /usr/local/bin/docker-entrypoint.py

# Railway injects PORT; 8000 is the local default. Binding 0.0.0.0 is required
# — a localhost-only bind passes every local test and fails every health check.
ENV PORT=8000
EXPOSE 8000

ENTRYPOINT ["python", "/usr/local/bin/docker-entrypoint.py"]

# Boot sequence: the entrypoint fixes the volume's ownership and drops
# privileges, then boot.py seeds the stores and execs the API. Seeding has to
# happen HERE because Kuzu is single-writer — once the API holds the write
# lock there is no later moment to fill an empty volume. See scripts/boot.py.
CMD ["python", "/app/scripts/boot.py"]
