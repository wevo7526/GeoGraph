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
COPY pyproject.toml README.md ./
COPY core/__init__.py core/
RUN pip install --no-cache-dir ".[api,panel,mcp]"

COPY core/ core/
# The ontology and crosswalks are not documentation: kuzu_schema.py reads the
# YAML at runtime to generate the schema and validators — the image does not
# boot without them. (They live under core/, copied above; named here so
# nobody "slims" them out.)
# The packs are INPUTS the seed needs, not build artifacts.
COPY packs/ packs/
COPY scripts/ scripts/

COPY --from=web /web/dist ./web/dist

# The graph lives here. Mount a Railway volume at /data and it survives a
# redeploy; leave it unmounted and the app reseeds on every boot.
ENV KUZU_DB_PATH=/data/geograph.kuzu
RUN mkdir -p /data

# Non-root at runtime — but deliberately NOT via `USER` (the MarketGraph
# volume lesson): a volume mounted at /data lands OVER the build-time
# directory, owned by root, and a USER-pinned container dies at startup with
# "Permission denied". The entrypoint starts as root, fixes the ownership of
# the mount that actually exists, and drops privileges before exec'ing.
RUN useradd --create-home --uid 10001 geograph \
 && chown -R geograph /app /data

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
