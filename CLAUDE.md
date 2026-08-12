# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## What this is

An applied-history engine: a 120-year knowledge graph of geopolitical actors,
relationships and events (1905 → present), a deterministic transmission layer
measuring how events moved markets, and a reasoning layer forecasting ~20
years forward in two modes. **`docs/build-spec.md` is the master spec and
every decision in it is LOCKED** — read Part II before changing anything
structural; cite the section when you deviate, and don't deviate silently.

Sibling of MarketGraph (`../marketgraph`), same foundation: LinkML ontology as
source of truth, embedded Kuzu graph, provenance invariant, Railway deploy.
MarketGraph's CLAUDE.md documents hard-won Kuzu and EDGAR lessons; the ones
that transfer are restated here so this repo stands alone.

## Commands

```bash
pip install -e ".[dev,api]"           # add ,panel,analysis,ingest,reasoning,mcp,gen as needed

pytest                                 # no DB or network needed
pytest tests/test_ontology.py -k sourced
ruff check .                           # E,F,I,UP,B,SIM; line-length 100
mypy .                                 # strict, minus disallow_any_expr

python -m core.api.app                 # THE APP — API + explorer on :8000
npm --prefix web install && npm --prefix web run dev    # Vite :5173, proxies /api
npm --prefix web run build             # tsc --noEmit && vite build → web/dist

python scripts/seed_pack.py mena       # sources → regimes → actors → markets → spine
python scripts/apply_schema.py         # Kuzu DDL + panel DDL (if DATABASE_URL)
python scripts/generate_ontology.py    # Pydantic + JSON Schema (needs .[gen])
python -m core.mcp.server              # MCP over stdio
```

## The ontology is the source of truth

`core/ontology/geograph.linkml.yaml` defines every node class, edge class and
enum. `core/ontology/kuzu_schema.py` reads it at process start and DERIVES the
Kuzu DDL, `validate_node`/`validate_edge`, `sourced_edges()` and
`traversable_edges()`. Nothing downstream keeps its own copy. Change the
model → change the YAML; everything else follows. `scripts/generate_ontology.py`
emits the Pydantic/JSON-Schema views for the ingestion boundary (gitignored
build artifacts).

## The invariant everything is built around

> Every sourced edge (`INITIATED_BY`, `DIRECTED_AT`, `RELATES_TO`, `AFFECTED`,
> `FLOW`) carries a `source_id` that resolves to a `Source` that exists.

Kuzu has no NOT NULL on rel properties, so enforcement is
`kuzu_schema.validate_edge`, called by `kuzu_store.merge_edges` — THE ONLY
edge-write path. `check_provenance()` is the backstop; `seed_pack.py` fails on
any violation. Sources are written BEFORE the edges that cite them — that
ordering is the foreign key being satisfied.

Corollaries:
- Loaders never infer a fact to tidy a parse failure — drop and count.
- Deep-tier records map through `core/ontology/crosswalks/` deterministically,
  never through the LLM.
- **The AI never originates a number** that lands in AFFECTED, NetworkMetric,
  or the deterministic part of a Forecast (build-spec §17). The reasoning
  layer narrates and argues; the deterministic core measures.

## The fidelity gradient is first-class

Every Event carries `fidelity_tier` + `temporal_resolution` + `source_scale`;
every AFFECTED edge carries the `resolution` it was measured at; every Market
carries `inception_date` and an era-keyed native frequency. Consequences:
- Transmission SKIPS markets that did not exist at event time (Gulf markets
  before founding), recording the skip.
- Deep-past effects (annual/monthly) are down-weighted, never treated as
  equal measurements to daily CAR.
- No dependency on historical intraday, ever (~60 days exist on yfinance).
- Reason by analogy ONLY within comparable regimes (`core/reasoning/regimes.py`
  — `comparable()` is an admissibility gate, not a similarity score).

## Two stores, one direction of flow

- **Kuzu** (embedded, `KUZU_DB_PATH`, volume on Railway): structure and
  provenance. SINGLE-WRITER — the API holds the lock; batch jobs write one at
  a time; `kuzu_store.connect` diagnoses the lock error explicitly.
- **Postgres** (`DATABASE_URL`, Railway service): the multi-frequency price
  panel + event-study working set (`core/panel/pg_store.py`), chosen because
  API and compute jobs need concurrent access.
- Numbers cross panel → graph in exactly one direction, through
  `core/transmission/effects.write_effects`. Nothing else writes AFFECTED.

## Kuzu behaviours that FAIL SILENTLY (inherited from MarketGraph — same engine)

| Do not write | Why | Instead |
|---|---|---|
| `count(DISTINCT x)` and `sum(y)` in one RETURN | sum is NULL | two queries, join in Python |
| `sum(CASE WHEN ... END)` | NULL | arithmetic identities |
| `MATCH (n:A\|B)` | unsupported | UNION ALL per label |
| `RETURN n` across a UNION | NODE types differ per table | explicit scalar columns |
| `sum(x)` over INT64 | returns Decimal → FastAPI serialises a JSON **string** | `kuzu_store._plain` normalises at the boundary — never per-query |

Also: `when` AND `end` are reserved words (loud parser errors — `end` is why
Regime carries `start_date`/`end_date`), **and that covers QUERY PARAMETER
names too**: `$end` is a parser error, so range filters use
`$start_date`/`$end_date`. Properties bind per label; the parser rejects `--`
comments.

**Closing a graph is not optional hygiene** (`kuzu_store.close`). Dropping the
Python reference releases neither the single-writer lock nor the database's
address-space reservation, and EACH open database reserves 8 TiB of virtual
memory — so one process can hold only about FIFTEEN graphs before
`kuzu.Database` fails with a buffer-manager error that names memory rather
than the real cause. Anything that opens graphs in a loop (a test suite, a
per-pack batch job) must close them; `connect` diagnoses that failure mode
explicitly because the raw message misdirects.

Postgres has the same class of trap: **`window` is reserved there**, so the
panel's event-study column is `effect_window`. A bare `window TEXT` is a
syntax error, not a style question. Classification edges (`OCCURRED_IN`,
`DERIVED_FROM`) are excluded from `traversable_edges` — every event points at
the same few Regime/Source nodes, so leaving them in makes any two events two
hops apart.

## Decisions that will look wrong without the reason

- **`core/mcp`, not a top-level `mcp/`**: the MCP SDK owns the `mcp` import
  name; a top-level package shadows it. As a subpackage it is safe.
- **Dates are STRINGS (ISO-8601) in the graph.** Deep-tier events may only
  know their year; ISO-8601 sorts lexically so range logic works at every
  resolution. Don't "fix" this to a date type.
- **`EventEscalation` slots live ON Event** (spec §8.2: "sits on the Event
  node") plus an `OF_DYAD` link; the LinkML class exists for typing only.
- **Escalation is relational** (`core/classifier/escalation.py`): per-dyad
  EWMA baseline, `magnitude = |score − baseline|`. A −6.0 event is routine
  for a rivalry and a rupture for an alliance — same score, different dyad,
  different classification. Feed events in time order.
- **Edge identity via `key_slots`** (read from the YAML by `merge_edges`,
  never a hardcoded rel-name test): `window` on AFFECTED, `as_of` on FLOW,
  `(relation_type, valid_from)` on RELATES_TO. Two values are two edges.
- **The actor set is time-varying**: COW state-system membership windows on
  Actor (`state_from`/`state_to`). Empires dissolve; queries at a date must
  respect the window.
- **Calendar handling is load-bearing** (`core/transmission/calendar.py`):
  Gulf Sun–Thu, US Mon–Fri, no shared session. Abqaiq (Sat 2019-09-14) is the
  canonical case: Tadawul reacts Sunday, US Monday — `first_mover` is real
  information. Holidays are a documented Phase 1 refinement.
- **Long-horizon output always carries the boundary statement**
  (`core/reasoning/structural.py::BOUNDARY_STATEMENT`): pressure over
  windows, never dated predictions. Near-term is Brier-scored; long-horizon
  is retrodicted — `calibration.score_forecast` REFUSES scenarios without
  likelihoods rather than mis-scoring them.
- **The market-as-sensor loop updates only from REALIZED outcomes** — never
  from the model's own predictions (§4). Estimate updates are NEW
  AttributeEstimate nodes (`method='sensor_update'`), not overwrites.
- **Region packs are a contract** (`core/packs.py`): seven YAMLs, core runs
  unchanged, nothing in `core/` may special-case a region name. `packs/china`
  is deliberately incomplete until Phase 6 — absent, not broken.
- **`/api/health` returns 200 even with no graph** — a health check that
  waits for data restart-loops a working Railway container. Unbuilt endpoints
  return 501 naming their phase, so an agent reports "not built" instead of
  inventing from an empty result.
- **MCP results are rows, capped, `truncated`-flagged** — never `{nodes,
  edges}` payloads (measured at 14K–20K tokens per call on MarketGraph).
- **Volume-over-directory entrypoint** (`docker-entrypoint.py`): a Railway
  volume mounts over `/data` owned by root; the entrypoint chowns then drops
  privileges. Do not replace with a `USER` directive — that is the exact bug.

## Phase status (build-spec §18)

Phase 0 COMPLETE: spine coded by Head B over the CAMEO→Goldstein crosswalk,
twelve-day-war case study end to end, container boot seeds/loads/measures
itself. Phase 1 COMPLETE: the transmission engine runs the whole spine at
boot (`run_event_study --all`), the panel guard is depth-based, and every
event×market pair is a measurement or a recorded skip. Phase 2 analytics
COMPLETE: `graph/analytics.py` computes centrality/brokerage/communities
over membership- and validity-windowed subgraphs, persists NetworkMetric
(decades + regime spans at boot), served at `/api/network/metrics` and the
MCP `network_metrics` tool. The explorer is a 3D no-scroll workspace
(react-force-graph-3d, MarketGraph Graph3D lineage) drawing the durable
RELATES_TO web (incl. Iran's proxy clients) under windowed event flow.
Not yet built — each stub names its phase: Phase 2 Head A validation,
3 (deep history), 4 (GDELT, 13F, explorer at 120 years), 5 (reasoning),
6 (China pack).
