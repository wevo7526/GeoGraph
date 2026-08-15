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

## The wire corpus is NOT the graph (2026-08-13, cited deviation from §5/§18)

The 1.33M-event GDELT wire lives in `core/wire/` as a CORPUS — a pure
function of the artifacts in `data/derived/` (git) through the shared parser
(`ingestion/gdelt.parse_lines`), `escalation.dyad_id` and Head B — because
every bulk reader of it (`models/panel`, `reasoning/forecasting`,
`games/transition`, the dyad/games/precedent routers) is a GROUP BY dyad
ORDER BY time, not a traversal. Merging it into Kuzu ran at ~145 events/sec
behind the single-writer lock, which is why 2026-08-13 was a four-hour outage;
`corpus.load` parses and scores a pack in ~5s. Rules that keep this honest:

- **Same ontology.** `pg_schema.py` derives the Postgres wire table from the
  SAME LinkML Event class `kuzu_schema.py` derives the node table from;
  `test_the_wire_table_is_derived_from_the_ontology` refuses drift. Provenance
  there is a real FK (`source_id NOT NULL REFERENCES wire_source`) — stronger
  than `validate_edge`, not weaker.
- **Consumers read corpus-first, graph-fallback** — and the two forecast
  readers (`structural`, `forecasting.all_dyad_event_rows`) read the UNION of
  both stores by event id, because the deep tier lives only in the graph and
  the wire only in the corpus. Eitheror is the 55-event trap: a rebuilt
  volume held 817k events but only the spine's 55 OF_DYAD edges, and every
  dyad page served empty while forecasts froze off nearly nothing.
- **Process-lifetime caches are CORRECT here** (`corpus._loaded`,
  `wire/serving`): the corpus is immutable for the life of a process — its
  source ships in the image. `serving.warm()` runs at app startup (~20s) so
  the parse cost never lands on a user's click.
- **Offline fits read the corpus, never a live store** (`fit_game.py`,
  `train_forecaster.py`): a committed artifact must be reproducible from the
  commit alone. All three regions carry `models/game-<region>.json` and the
  intensity gate passes trained on the pooled corpus (still scored WITHIN
  dyad).
- **The graph keeps what is genuinely graph-shaped**: actors, regimes,
  RELATES_TO, the curated spine, AFFECTED (still written ONLY by
  `write_effects`), NetworkMetric, Forecast nodes, and measured effects reads.
  The explorer is untouched.
- `scripts/load_wire.py` materialises the corpus into Postgres (COPY + an
  exact EWMA aggregate reading `DEFAULT_ALPHA`/`STABLE_BAND` off
  `escalation.py`) for SQL consumers; serving does not depend on it.

## The 2026-08-14 rebuild (as-of walk, ML→game bridge, API-first boot)

- **`forecasting.AsofArchive` IS `forecast_from_rows`'s body**: columnar
  arrays built once, evaluated at any cutoff in ~1-2ms. That is what made the
  walk-forward backtest boot-viable again (426 corpus-scale cutoffs in <1s per
  region vs a 900s ceiling it used to burn without finishing) while keeping
  the locked "never a backtest-only estimator" rule — the rule locks the code
  path, not statelessness. Only fully-entered books compound (partial books
  are recorded skips), and the row contract's `baseline` is now PER EVENT and
  AS OF that event (reading the Dyad node's standing scalar at historical
  cutoffs was oos-spec leak 1).
- **The measuring boot steps are OPT-IN, so a routine deploy's graph opens in
  seconds** (fixed 2026-08-14, the second half of the API-first move). API-first
  binds the port in ~20s, but the graph endpoints answer 503 until the last
  write-child exits — Kuzu is one writer OR many readers across processes, so
  the API opens its connection only when the background boot thread finishes
  (`core/api/app.py::_run_boot_behind_the_api`). The study, the forecast freeze
  and the backtest are write-children that re-derive data ALREADY persisted on
  the volume, and the study never converged inside its 600s budget — so it
  burned ~600s of graph-dark time on EVERY deploy re-measuring the archive,
  and forecasts/backtest added ~175s more. They now default OFF
  (`GEOGRAPH_STUDY_ON_BOOT` / `GEOGRAPH_FORECASTS_ON_BOOT` /
  `GEOGRAPH_BACKTEST_ON_BOOT`, opt-in like GDELT and the rescore): a routine
  deploy runs `seed → open graph` and serves every measurement it had a moment
  ago; a measuring deploy sets the vars and pays the downtime deliberately. The
  healthcheck already passed at ~20s, so this never risked the container — only
  the graph half of the API. `test_the_measuring_steps_are_opt_in_so_the_graph_opens_fast`
  pins the defaults.
- **The ML→game bridge exists** (`core/games/bridge.py`): the frozen model
  mode's per-dyad trajectory tilts that dyad's transition kernel
  (exponential tilt, bounded by `TILT_SCALE`, η from predicted drift over
  the model's own residual spread), audited on every solve it touched with
  the artifact's name@hash. Opening states are DATA, not defaults
  (`core/games/opening.py`): capability off the actors' CINC estimates,
  beliefs filtered from the dyad's observed actions through the game's own
  Bayes rule. `/games/explore` with no overrides is now THE BASELINE
  (`baseline: true` — the frozen sequence forecast's construction); the
  counterfactual label appears only when a lever actually moved.
- **Path enumeration keeps the kernel's stochasticity** (`games/paths.py`):
  each step branches over the bands the row puts real mass on, and the
  marginal fan accumulates across ALL branches before the top-N cut. Taking
  each row's modal band had made every period's marginal identical — the fan
  was not a fan.
- **`structural.py` is region-filtered** (roster + `region_pack`), so the
  three lenses stop freezing one archive-wide number under three labels, and
  `calibration.retrodict` stands at MANY anchors (`PressureArchive` makes
  them ~free) — the single-anchor retrodiction had flagged nothing anywhere
  and verified nothing, in every region.
- **The boot is API-FIRST** (`GEOGRAPH_API_FIRST`, default on): boot.py execs
  the API immediately; the steps run on a background thread behind the bound
  port while the API holds NO graph connection (Kuzu: one writer or many
  readers, never both across processes); the graph opens when the last write
  child exits. Dark time per deploy is the corpus warm (~20s), not the boot.
  Steps carry INPUT FINGERPRINTS on the volume
  (`.boot-fingerprints.json`): metrics/forecasts/scores/deep-tier/backtest
  skip in ms when nothing they read changed (fingerprints are stored
  POST-run, because several steps move their own inputs' facets), the study
  records only a CLEAN pass (a deferred backlog must not strand), and 13F is
  at most weekly (EDGAR is quarterly). `GEOGRAPH_SKIP_GUARDS=0` disables.
- **Effects reads reconstruct dyad membership from the ACTOR EDGES**
  (`precedent._effects_for`): every event carries INITIATED_BY/DIRECTED_AT
  (the provenance invariant) and `escalation.dyad_id` IS the sorted pair, so
  the reasoning page's market panel no longer requires OF_DYAD — production
  holds 278k+ AFFECTED edges beside the spine's 55 OF_DYAD edges, and the
  hard join served "no measured effects" for nearly every dyad while the
  measurements sat unreachable.

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
- **UNEVEN DENSITY IS THE ARCHIVE'S DEFINING HAZARD, and two estimators were
  silently wrecked by it.** The SHAPE has changed — the modern harvest landed
  2026-08-13, so the corpus now runs through 2026 and mena measures ~77%
  post-2005 (the old "98% in 1979–2005, 155 events in the last twenty years"
  era is over) — but the LESSON has not: density is coverage, not history,
  and it now tilts toward the recent years instead of away from them. Check
  the sample behind every count. Consequences enforced:
  - `structural.py` drops any trailing window under `_MIN_WINDOW_SAMPLE` (30
    coded events) and computes the composite ONLY for years holding every
    component. Percentile-ranking a six-event window against a five-thousand-
    event one had pinned conflict_intensity at 1.0 and, once the capability
    series ended in 2022, quietly turned a mean of four components into a mean
    of the two noisiest — printing 0.93, an all-time high, for 2025. Years
    short of full coverage are reported in `coverage`, never averaged over
    fewer terms.
  - `forecasting.py` counts an episode only when a dyad-quarter holds a
    departure in the top decile of in-regime departures from that dyad's OWN
    baseline (`_SIGNIFICANCE_PERCENTILE`, read off the archive and frozen in
    the payload), and shrinks each dyad's own rate toward the pooled rate by
    beta-binomial method of moments instead of handing every dyad the pool.
    Counting every escalating event pooled across all dyads had answered "is
    this dyad chronically in the wire?" — 0.9347, identical for three
    unrelated dyads. Focal dyads must clear an evidence bar BEFORE ranking,
    or a dyad with zero episodes leads the forecast wearing the pooled prior.
  - Frozen payloads now carry `evidence_span`: when a likelihood's evidence is
    from, which is not when the archive ends.
- **THE LOCAL GRAPH IS A SAMPLE, NOT THE ARCHIVE — check `/api/stats` before
  calling anything missing.** `data/geograph.kuzu` on a dev machine holds
  whatever was last loaded there; the deployed volume holds the real thing.
  This cost real work on 2026-08-13: an empty `AFFECTED` table locally was
  written into `docs/game-spec.md` as the game layer's blocking dependency,
  when production had **382,736 measured effects** and the boot had been
  running the event study over the whole spine since Phase 1. The panel, the
  transmission engine and the volume only exist where they are deployed —
  `curl https://geograph.up.railway.app/api/stats` settles it in one call.
- **The learned layer is `core/models/`, and its gate is WITHIN-DYAD**
  (docs/ml-spec.md). A pooled score on this archive is not evidence: the
  label's variance is 70% within dyad and 30% between, so a model that only
  knows which dyad it is looking at scores AUC 0.92 pooled while ranking that
  dyad's own quarters BACKWARDS (0.35). Three consequences that will look
  strange without the reason:
  - **The target is a DEVIATION from the dyad's running baseline, and so are
    the features.** Demeaning features but not the target was the bug —
    least squares then spends the deviations explaining between-dyad level and
    lands on the wrong sign within a dyad.
  - **The shipped model uses three of the nine features it computes.**
    Measured, not chosen: every other feature made out-of-sample within-dyad
    ordering worse. The other six stay because the ablation reads them.
  - **Persistence is not the baseline, it is the signal** (+0.4253 within
    dyad; nothing beat it). So the gate asks the model to KEEP persistence's
    ordering and beat its error — `passes_gate` carries the record of the gate
    moving, on evidence, in its own docstring. The model's claim is magnitude,
    and the frozen payload says so.
  Training is OFFLINE (`scripts/train_forecaster.py` → a hashed JSON artifact
  in `models/`, committed); the boot does a forward pass and freezes a THIRD
  Forecast mode (`model`). A model whose gate failed is never frozen, and the
  two counted forecasts do not depend on it existing.
- **Region packs are a contract** (`core/packs.py`): seven YAMLs, core runs
  unchanged, nothing in `core/` may special-case a region NAME. Three packs
  now — `mena`, `china`, `eurasia` — and an incomplete pack directory is
  simply not listed (absent, not broken). The contract permits the core to
  learn a GENERAL capability a new region needs: `external_powers` moved from
  a hardcoded `{USA, RUS}` in the GDELT loader onto the pack (Eurasia's spine
  IS the Washington–Moscow dyad), with the old constant preserved as the
  default. That is the contract holding, not bending — the test to apply is
  "would a fourth region need this too?", not "does this mention a region?".
- **A pack's KEY is not its caption.** `Pack.label` (declared as
  `region_label` in the pack's actors.yaml, defaulting to the pack name) is
  what the surface shows; `pack.name` is what every `region=` parameter takes
  and what every record carries in `region_pack`. `packs/china` is captioned
  ASIA — the roster reaches Taiwan, Japan and Korea — while staying keyed
  `china`, because renaming the key is a data migration: it is written into
  the GDELT artifact filenames and into the deployed volume, and the boot's
  resume check counts events BY that key. `/api/packs` serves both fields;
  `web/src/regions.ts` is the single fetch behind every header.
- **Shared market nodes must be described identically by every pack that
  names them.** Packs seed alphabetically, so two descriptions of
  `market:brent` is one description plus a silent loser — and the
  transmission engine reads `inception_date` and `native_frequency`, so the
  loser changes measurements. `test_shared_market_nodes_agree_across_packs`
  refuses the divergence (it caught a live one in `packs/china`).
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
Phase 3 landed (COW states/MIDs/CINC/alliances/IGO + Shiller monthly;
ICB/ICOW deferred — severity files are JS-gated). Phase 4's credential-free
half landed: GDELT from the FREE RAW FILES (data.gdeltproject.org — no
BigQuery project needed; scripts/backfill_gdelt.py), 13F FLOW edges from
EDGAR, the explorer walks 120 years with windowed casts. Phase 5's
deterministic core landed end to end: structural pressure + retrodiction,
regime-gated analogy, sensor loop, near-term base-rate scenarios — FROZEN
into Forecast nodes at boot, served at /api/forecasts, rendered in the
explorer when the slider crosses "now", with the PAPER BOOK
(/api/forecasts/{id}/paper) marking each frozen call's implications to
market on $1M notional. Phase 6 COMPLETE — and then some: `packs/china`
proved the contract (no `core/` diff accompanied it), and `packs/eurasia`
(Russia, Europe and the stepland) is region three, which cost the core only
the `external_powers` generalization noted above. Still stubbed: the LLM
halves (agent.py narration, vector-index analogy — need ANTHROPIC_API_KEY),
and the BigQuery transport (an alternative to the raw files, which work).

## The surface is set on paper (2026-08-12)

**A deliberate, cited deviation from build-spec §15**, which specifies a
near-black ground with parchment text. The landing was reset as a broadsheet
front page and the contrast against a dark app read as two products, so the
print language now carries the whole surface: parchment ground, ink text,
masthead rules, dot-leader ledgers. §15's actual requirement — restrained,
serious, one accent, nothing decorative — is honoured in full; its ground and
text are inverted. The long comment at the top of `web/src/styles.css` is the
citation. **Do not "restore" the dark theme as a bug fix.**

Two consequences worth knowing before touching colour:
- **The 3D canvas stays dark** (`--plate`). The categorical actor colours were
  validated against a dark surface and are drawn nowhere else; repainting for
  paper would break a correct encoding.
- **`--accent` and `--alert` carry the SIGN of a number** (gain/loss,
  de-escalation/escalation), so they are a diverging pair, not decoration.
  They were chosen by running the dataviz skill's `validate_palette.js`, not
  by eye — the first pick failed the lightness band and chroma floor. Re-run
  it against the `#f2ecdd` ground before changing either.

## The 2026-08-15 rebuild (LP equilibrium, scenario maps, white surface)

- **The game solves under TWO stage concepts** (`core/games/solve.py`
  `solver="qre"|"lp"`): the fitted quantal response (the estimator's concept,
  the default for `/games/explore` and the freeze) and the correlated
  equilibrium (`core/games/equilibrium.py`). The CE reports `nash_gap` — total
  variation of its joint from the product of its marginals; 0 means it sat ON a
  Nash point, and in practice ~98% of stage games do. That number is what
  "toward the BNE" means on the surface; it is stated, never assumed. Both
  concepts share the recursion, the path walk (`paths.py`, which now emits
  per-step beliefs) and the ML tilt. **The selection is ENTROPY-REGULARISED,
  not a bare welfare LP** (fixed later the same day, see below).
- **The scenario map** (`core/games/scenarios.py`): every active dyad in a
  region solved at its data-driven opening state under both concepts, courses
  of play NAMED as scenarios with likelihoods, priced to the measured market
  map, aggregated to a region future-event map, and EXPLAINED by a template
  over the payload's own fields (§17 holds — no sentence a number cannot
  substantiate). One region-context builder (`core/games/context.py`) is
  shared by the router, the map and `scripts/solve_games.py`.
- **Solutions persist in Postgres** (`game_solutions`: region aggregate +
  per-dyad rows, replaced whole per region) written by the opt-in boot step
  `GEOGRAPH_GAMES_ON_BOOT` (~3 min for three regions × 12 dyads, fingerprint-
  guarded on events/affected/estimates/forecasts + image). `/api/games/region`
  and `/api/games/dyad` are persisted-first with a live fallback flagged
  `persisted: false`. `paper_backtest_runs` keeps each walk's skips and
  summary — a region whose every quarter was a skip used to read as unrun.
- **The panel guard is per ticker** (boot `_missing_tickers`): a pack whose
  markets hold ZERO rows gets them loaded before the global depth/freshness
  check, and `_panel_edge()` carries ticker breadth so the backtest guard
  wakes when the panel gains a series. Found because china's `^TWII`/`^HSI`
  and eurasia's indices were never loaded and 425 quarters per region were
  "1 of 3 legs" skips.
- **Registration**: `/api/impact/coverage` registers measured-vs-total events
  per dyad per pack (the market-movement trace); `/api/dyads` honours
  `region`; the dyad timeline carries name/score/direction/first mover;
  `pricing.measured_effects` keeps deep-tier (`region_pack=''`) effects;
  DGS3MO effects are FRED-sourced; `/api/case-studies/dynamic` composes a
  study for any dyad or event in the worked-study shape.
- **The surface is WHITE** (styles.css citation): white ground, black ink,
  white knowledge-graph plate with a 2px black border; Graph3D's WebGL
  constants inverted with it and the state-series gold re-validated
  (`#c48a12`). Six tabs: Explorer, Relationships, Game theory, Markets,
  Watchlist, Case studies. Chart primitives live in `charts/Charts.tsx` +
  `charts/Kit.tsx` (inline SVG on the tokens, hover layers, no library).
- **After a volume reset the GDELT load is alphabetical and window-bound**:
  the 2026-08-15 `recover` rebuild merged china and eurasia and never
  reached mena, so mena's wire was corpus-only in the graph and its measured
  effects empty. `/api/impact/coverage?region=mena` is the check.

## The 2026-08-15 repairs (the spine is measured first, a payload carries its shape, the CE stops claiming certainty)

Three failures the surface wore at once, all of them the same species: a
component was right and the thing that fed it was stale, last in line, or
degenerate.

- **The curated spine is measured on EVERY boot** (`run_event_study.py
  --spine`, boot step `spine`, always on). The full study walks the archive in
  DATE ORDER and takes a budgeted slice, which is correct for a hundred
  thousand events and exactly wrong for the ~40 the packs name: the case
  studies, the marquee episodes and everything a narrated page reads are the
  most RECENT events in the archive, so a truncated walk reaches them last.
  Production held 632,586 measured effects, had walked as far as 2003, and
  served "this study has a spine and no numbers" on all three case studies.
  Two fixes, both cheap: `--all` now sorts curated-first-then-date (same work,
  different order for a pass that gets cut off), and the curated set is
  measured on every boot, watermarked **against the GRAPH** rather than the
  panel. That second half matters independently — the two stores fail
  independently, so after `GEOGRAPH_RESET_GRAPH` the panel's
  `event_study_runs` still says "measured" for events the rebuilt graph has no
  edges for, and the engine would skip them forever.
- **A persisted computation outlives the code that wrote it, so it carries its
  shape** (`scenarios.PAYLOAD_VERSION`, checked in `pg_store.game_solution`).
  The ranking metric was renamed `escalation_probability` →
  `sharp_departure_probability` an hour after the games boot step last ran;
  the step is opt-in, nothing re-solved, and Postgres kept serving the old
  shape to a frontend reading the new names — **every probability on the
  game-theory page rendered `NaN%`**, beside courses named at 100% (those rows
  also predate the belief ceiling). A version mismatch is now a cache MISS and
  the endpoint solves live. `pct()` in `charts/Kit.tsx` renders "—" for
  anything non-finite: a missing field is absent data, not a quantity.
- **The CE selection is entropy-regularised** (`equilibrium.solve_stage_lp`).
  Maximising welfare alone over the CE polytope is an LP, so its optimum is a
  VERTEX — one joint action at certainty, whichever one HiGHS reached among
  ties. The objective now adds the joint's entropy at the quantal response's
  own temperature (λ read on the stage's welfare SPREAD, not in raw payoff
  units — a flat 1/precision against discounted continuation values is no
  regularisation at all), solved in the dual: `p(μ) = softmax((welfare −
  Gᵀμ)/λ)` with L-BFGS-B over the multipliers. Same polytope, so it is still
  an exact CE (`ce_violation` says how exactly, and the HiGHS vertex is the
  fallback when the dual does not clear); strictly concave, so ties are kept
  instead of resolved into certainty. It is also **14× faster** (0.8s vs 11.4s
  per dyad solve), which is what makes a live region map viable as a fallback.
  The limit is honest and tested: under a dominant action the polytope is a
  POINT, the solution is pure, and no regularisation can or should spread it.
- **A scenario is one KIND of course, not one course** (`scenarios_for`).
  `scenario_name` is `kind:dyad` and was therefore not unique — one
  distribution split across four rows of the region's escalatory list, each
  labelled the same — and a single sequence's mass answers a question about
  the enumeration's resolution rather than about the world. Pooled by kind:
  unique names, likelihoods that still sum to the retained mass, `courses`
  saying how many were pooled and `lead_likelihood` the modal one's own share.
