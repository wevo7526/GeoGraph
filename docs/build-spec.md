# GeoGraph: Master Build Specification

Version 2.0. Single source of truth for the build. It fuses the conceptual map (the why, the what, the audience, the intellectual frames) with the technical specification (the how: stack, schema, engines, data, phases). It supersedes all prior documents.

The horizon is a 120-year archive: build a structured record of geopolitical events and their market effects from 1905 to the present, and forecast roughly 20 years forward. The data has an explicit fidelity gradient. The deep past is event-rich but market-data-poor and coarse; fidelity and frequency rise toward the present. That gradient is modeled as a first-class property, not smoothed over.

Persistence reuses MarketGraph's own foundation: the ontology in LinkML, the knowledge graph in Kuzu, and the tabular market panel in a Railway-hosted Postgres. All data sources are open.

Read Part I for the why. Build against Part II. Every decision is locked.

---

# PART I. CONCEPT

## 1. What GeoGraph is

An applied-history engine for geopolitics and markets, built as a portfolio piece, not a product for a buyer. The audience is the macro-history reader: quantitatively literate but not quant-native, moved by historical perspective, network thinking, well-reasoned contingency, and elegant presentation. Niall Ferguson's firm Greenmantle is the archetype for that reader, a potential audience member, not a client.

GeoGraph is three things at once:

1. A 120-year network archive of geopolitical actors, their relationships, and the events that pass between them, built to the same provenance discipline as MarketGraph: every edge traces to a source.
2. A transmission layer that measures how those events actually moved markets, at whatever resolution the era allows, so the geopolitics-to-money link is shown, not asserted.
3. A reasoning layer that reads the present against a deep memory of the past and produces forward assessments as reasoned scenarios with their logic on display, in two modes for two horizons.

One-line pitch: a system that operationalizes applied history at the scale of the longue durée. It reasons about the present from a structured memory of the last 120 years, thinks in networks rather than headlines, conditions on structural regime, and is honest about uncertainty.

GeoGraph is a separate repository with its own front door, bootstrapped from the MarketGraph scaffold, cross-linked to MarketGraph as a sibling. Built by Will and a colleague from the William and Mary MBA.

## 2. The intellectual frames it speaks in

Each maps to a real component.

- Networks over hierarchies (Ferguson, The Square and the Tower). The knowledge graph is this: actors are nodes, relationships are edges, and you run real network analysis (centrality, brokerage, coalitions, structural holes) across 120 years of shifting alliance and IGO structure. The strongest hook.
- Applied history at the longue durée (Ferguson and Allison). A 120-year corpus reasoned over by analogy and base rates within comparable structural regimes.
- Structural forecasting (Turchin's cliodynamics). The long-horizon mode maps the accumulation of systemic pressure and crisis probability over a window, explicitly distinguished from dated prophecy.
- Counterfactual reasoning (Ferguson, Virtual History). The engine can hold a branch point and reason about the roads not taken.
- Geopolitics to money (Ferguson, The Ascent of Money). The transmission layer tells this story with measured data across the full span.
- Realist strategic logic (Kissinger, the bargaining and power-transition traditions). Actors modeled by interests, resolve, and capability, the last seeded from real capability data over the deep past.

## 3. Decisions, locked

1. Forecast output: reasoned scenarios with likelihoods where the horizon allows, each with a market-direction implication, historical analogues, and a rationale traced to graph evidence. Not a single number, not a raw signal.
2. Latent variables: model actor resolve, capability, and salience as distributions with visible uncertainty, updated by an agent that reasons explicitly about surprises. Capability and salience are seeded from real data (CINC, ICOW) over the deep past.
3. Historical horizon: a 120-year archive from 1905 to the present, forecasting roughly 20 years forward, with an explicit fidelity gradient (section 5).
4. Two forecasting modes. Near-term (0 to 3 years): calibrated probabilistic, the temporal-graph and game-theoretic layer. Long-horizon (5 to 20 years): structural forecasting on slow variables, scenario-space with crisis-probability windows, never dated point predictions.
5. Regime conditioning: reason by analogy within comparable structural regimes (monetary order and polarity epoch), never naively across the span.
6. Effect resolution: compute each effect at the finest frequency the era allows (annual, monthly, daily, or intraday), and record that resolution. Daily CAR is the workhorse for the modern era; the deep past is annual to monthly.
7. Market universe: US equities and rates, Saudi (TASI), Abu Dhabi (ADX), Dubai (DFM), plus Brent and gold. Each market carries an inception date; Gulf markets have no pre-founding data.
8. Region strategy: MENA is region one and proves the model. China and the Taiwan Strait is the flagship second region.
9. Corpus: structured historical datasets for the deep tier, GDELT plus a curated marquee spine for the modern tier.

## 4. The market-as-sensor idea

The true mechanisms are private information and signals intelligence we do not have. Game theory needs those hidden variables as inputs, so we estimate them (position, salience, clout, resolve) from observable proxies, hold them as distributions, and reason over the uncertainty. The market reaction is a second sensor: the residual between the expected and the realized market move is our estimate of the private information the open sources could not show, and it updates the latent estimates. The learning loop is powered only by realized outcomes, never by the model's own predictions.

---

# PART II. TECHNICAL SPECIFICATION

## 5. Data sources and the fidelity gradient

All open. Organized by era, because the source and the resolution change as the archive moves toward the present. Every record is tagged with a source tier and a temporal resolution, and the reasoning layer conditions on both.

### 5.1 Deep tier, roughly 1905 to 1979, structured historical data

Events and actors:
- Correlates of War (COW). Militarized Interstate Disputes and interstate, intra-state, and extra-state wars, 1816 to 2014, with hostility levels and onset locations. National Material Capabilities, which yields CINC, a per-state per-year power index, used to seed actor `clout`. Formal Alliances and IGO memberships, used to populate `RELATES_TO` edges over the deep past. Issue Correlates of War territorial claims, 1816 to 2001, which carry a salience measure used to seed actor `salience`.
- International Crisis Behavior (ICB): interstate crises from 1918, with severity and violence measures.
- V-Dem or Polity: regime and institutional data over the long run.

These are structured datasets, not free text, so they enter the ontology through deterministic crosswalks (section 10), not the LLM coder.

Market data (annual to monthly, advanced economies, no Gulf):
- Jordà-Schularick-Taylor Macrohistory Database: annual equity, bond, bill, and housing returns plus rates, credit, and public debt for 18 advanced economies since 1870. Free.
- Shiller: monthly US equity index and long rates since 1871. Free.
- Long-run oil (annual, from the nineteenth century) and gold. Free.

### 5.2 Modern tier, roughly 1979 to the present

Events, via GDELT on BigQuery (`gdelt-bd.gdeltv2.events`): daily, CAMEO-coded, Goldstein-scored. UCDP and ACLED for conflict validation. The GPR index (Caldara-Iacoviello) as a regime overlay.

Market data (daily, then intraday recently):
- yfinance: `^GSPC` and sector ETFs; `^TASI.SR` (Saudi), `DFMGI.AE` (Dubai), `FADX15.FGI` (Abu Dhabi); `BZ=F` (Brent), `GC=F` (gold). Verify each ticker's depth on ingest.
- FRED: `DGS2`, `DGS10` for Treasury yields.
- SEC EDGAR 13F for SWF flows (PIF, Mubadala, ADIA file quarterly; US-listed long equity only, 45-day lag; reuses MarketGraph's ingestion).

### 5.3 The fidelity gradient, stated

- Event resolution: deep tier is crisis-and-dispute level at yearly-to-monthly time granularity; modern tier is daily coded events. Tag each event with `fidelity_tier` and `temporal_resolution`.
- Market coverage: each market has an `inception_date` and an era-varying native frequency (annual JST, monthly Shiller, daily yfinance, intraday recent). Gulf markets have no data before founding (Tadawul 1985, DFM and ADX 2000). Deep-past transmission runs on US equities, US rates, oil, and gold only.
- Intraday is recent-only (about 60 days on yfinance). Build no dependency on historical intraday.
- The reasoning layer down-weights coarse deep-past effects relative to fine modern ones rather than treating them as equal measurements.

## 6. Stack and environment

- Language and API: Python 3.12, FastAPI, Pydantic v2 (generated from LinkML).
- Ontology: LinkML, single source of truth, generating Pydantic and JSON Schema. A thin project-owned generator maps LinkML classes to Kuzu node tables and relationship classes to Kuzu rel tables. Reuse MarketGraph's generator if present.
- Knowledge graph: Kuzu, embedded property graph with Cypher, the same store MarketGraph uses, with a vector index for the analogy engine. Graph persistence, ingestion, and explorer patterns transfer directly from MarketGraph.
- Tabular and numeric: Postgres on Railway with a persistent volume, holding the multi-frequency market panel (annual, monthly, daily) and the event-study working set. Chosen over an embedded file because the API and the compute jobs need concurrent access.
- Numeric and stats: pandas, numpy, statsmodels, scipy.
- Network analytics: networkx or igraph over subgraphs exported from Kuzu.
- Ingestion: yfinance, fredapi, google-cloud-bigquery, httpx, a SEC EDGAR client, and loaders for the COW, ICB, JST, and Shiller flat files.
- LLM: Anthropic Claude via the SDK. Used for CAMEO coding of modern non-GDELT text, the reasoning agent, and analogy retrieval. Never for numbers.
- Agent surface: MCP server exposing graph and analytics tools.
- Web: TypeScript, React, Vite, Tailwind; force-directed graph via d3-force or sigma.js.
- Deploy: Railway. Kuzu is an embedded file on a persistent volume, single-writer (batch ingestion and transmission jobs write one at a time; API reads are concurrent). Postgres runs as a Railway service.

Environment variables: `ANTHROPIC_API_KEY`, `BIGQUERY_PROJECT`, `GOOGLE_APPLICATION_CREDENTIALS`, `FRED_API_KEY`, `GPR_INDEX_URL`, `KUZU_DB_PATH`, `DATABASE_URL`.

## 7. Repository structure

```
geograph/
  README.md
  CLAUDE.md
  docs/build-spec.md
  core/
    ontology/
      geograph.linkml.yaml       ontology, single source of truth
      generated/                 pydantic + json schema
      kuzu_schema.py             derives Kuzu DDL from the LinkML model
      crosswalks/                cow_to_cameo.yaml, escalation_scale_map.yaml, regimes.yaml
    graph/                       kuzu_store.py, analytics.py
    panel/                       pg_store.py (multi-frequency price panel + event-study set)
    ingestion/                   gdelt.py, ucdp.py, acled.py, gpr.py, market_data.py, edgar_13f.py,
                                 cow.py, icb.py, jst.py, shiller.py
    classifier/                  typing.py, escalation.py
    transmission/                event_study.py, calendar.py, effects.py
    reasoning/                   forecasting.py, agent.py, analogy.py, sensor_loop.py,
                                 structural.py, regimes.py, calibration.py
    api/                         app.py, routers/
    mcp/                         server.py, tools.py
    settings.py
  packs/
    mena/                        actors.yaml, issues.yaml, markets.yaml, assets.yaml, priors.yaml, sources.yaml, marquee_events.yaml
    china/
  web/
  scripts/
  tests/
```

## 8. Data model

### 8.1 Ontology (LinkML)

Enums include: `ActorType` (state, org, person, swf); `QuadClass`; `RelationType` (alliance, rivalry, membership, trade); `Attribute` (position, salience, clout, resolve); `EscalationDirection`; `MarketType`; `TradingCalendar`; `EffectWindow`; and the new archive enums `FidelityTier` (deep_structured, modern_coded, live), `TemporalResolution` (year, month, day, intraday), `SourceScale` (goldstein, cow_hostility, icb_severity), `RegimeKind` (monetary_order, polarity_epoch).

Classes (key slots):
- `Source`, `Actor`, `Issue` as before. `Actor` carries COW/state-system membership windows so the actor set is time-varying (empires and states appear and dissolve over 120 years).
- `AttributeEstimate`: attribute, value_mean, value_std, as_of, method (a distribution; deep-past clout seeded from CINC, salience from ICOW).
- `Event`: action_cameo_code, event_time, goldstein, quad_class, location, geo_lat, geo_lng, region_pack, plus `fidelity_tier`, `temporal_resolution`, `source_scale`.
- `EventEscalation`: baseline, escalation_direction, escalation_magnitude, plus the harmonized goldstein-equivalent so escalation is comparable across scales.
- `Dyad`.
- `Market`: name, ticker, market_type, region_pack, trading_calendar, plus `inception_date` and an era-keyed native frequency.
- `Regime`: kind (RegimeKind), name, start, end (for example Bretton Woods 1944 to 1971, bipolar 1945 to 1991).
- `Forecast` with an embedded scenario list; `Analogue`; `NetworkMetric`.

Relationship classes become Kuzu rel tables (section 8.2). Every ingested entity links to a `Source`. Validate every record against the generated JSON Schema.

### 8.2 Knowledge graph (Kuzu)

Node tables: `Actor`, `Issue`, `Event`, `Market`, `Source`, `AttributeEstimate`, `Regime`, `Forecast`, `Analogue`, `NetworkMetric`.

Relationship tables:
- `INITIATED_BY` (Event to Actor), `DIRECTED_AT` (Event to Actor): the CAMEO quadruple.
- `RELATES_TO` (Actor to Actor) {relation_type, valid_from, valid_to}: the durable network, populated from COW alliances and IGOs in the deep past and from event flow throughout.
- `AFFECTED` (Event to Market) {window, resolution, raw_return, expected_return, abnormal_return, t_stat, p_value, first_mover, method}: the money edge, numbers written by the transmission engine, carrying its resolution.
- `HAS_ESTIMATE` (Actor to AttributeEstimate).
- `OCCURRED_IN` (Event to Regime), so analogy and forecasting can condition on regime.
- `DERIVED_FROM` (Event to Source); `FLOW` (Actor to Market) from 13F.

`EventEscalation` sits on the Event node plus a link to its Dyad. Kuzu's vector index backs analogy retrieval.

### 8.3 Tabular numeric store (Postgres)

- `market_observations` (market_ticker, obs_date, frequency, open, high, low, close, value, source_ref), where `frequency` is annual, monthly, or daily; deep-past series may carry a single value rather than full OHLC.
- `market_intraday` (market_ticker, ts, price), recent only.
- Event-study working set and results.

The transmission engine reads series from Postgres, computes in pandas and statsmodels, and writes effects back as `AFFECTED` edge properties in Kuzu.

## 9. Reference data and crosswalks

- CAMEO codebook and the Goldstein table, loaded and typed inside the LinkML ontology.
- `cow_to_cameo.yaml`: maps COW and ICB event categories to CAMEO-equivalent codes, so deep and modern events share one action vocabulary.
- `escalation_scale_map.yaml`: maps COW hostility levels (1 to 5) and ICB severity to a Goldstein-equivalent, so escalation is comparable across the archive.
- `regimes.yaml`: the monetary-order and polarity-epoch segmentation, loaded as `Regime` nodes.

## 10. Classifier

Head A, event typing: modern non-GDELT text calls Claude with the CAMEO codebook, returning `{cameo_code, actor1, actor2, quad_class, confidence}`; GDELT firehose is trusted directly; deep-tier structured events map through `cow_to_cameo.yaml` deterministically, not the LLM.

Head B, escalation, deterministic: base is the Goldstein score (from CAMEO) or the harmonized equivalent (from COW or ICB via `escalation_scale_map.yaml`). Maintain a per-dyad EWMA baseline. `escalation_magnitude = abs(score - baseline)`, `escalation_direction = sign(score - baseline)`. Always relational, never an absolute label.

## 11. Transmission engine (deterministic event study)

`transmission/event_study.py`. Deterministic. For each event and each market that existed at that time, compute the effect at the finest frequency the era allows:
- intraday open-to-close (recent, where available),
- daily CAR over `car_0_1`, `car_0_3`, `car_0_5` (modern era, yfinance),
- monthly abnormal return (Shiller era, US),
- annual abnormal return (JST era, advanced economies).

Method: market-model expected return over a clean pre-event estimation window scaled to the frequency, abnormal return equals actual minus expected, with a significance test. Record the `resolution` on every `AFFECTED` edge. Skip markets that did not exist at the event time.

Calendar handling stays load-bearing for the modern era: Gulf trades Sunday to Thursday, US Monday to Friday, no shared session; resolve the first session per market independently and record a `first_mover` flag for cross-market lead-lag. Honesty rule: measure realized effects, never assert a sign.

## 12. Network analytics

`graph/analytics.py`. Export time-windowed subgraphs from Kuzu (actors, COW and modern relationships, event flow) into networkx or igraph; compute degree, betweenness, and eigenvector centrality, brokerage and structural holes, community and coalition detection, and how each shifts across regimes and decades. Persist to `NetworkMetric`. This is the Square-and-the-Tower payload across the full 120 years.

## 13. Reasoning and forecasting layer

Two modes, plus shared infrastructure. The AI never originates numbers that appear in effects or in the deterministic parts of a forecast.

Shared:
- Regime segmentation (`regimes.py`): tag every event and window with its monetary order and polarity epoch.
- Analogy engine (`analogy.py`): retrieve structurally similar past situations using Kuzu's vector index plus structural matching on actor roles, escalation trajectory, network position, and regime. Only match within comparable regimes. Write to `Analogue`.
- Market-as-sensor loop (`sensor_loop.py`): update `AttributeEstimate` records from the residual between expected and measured effect. Powered only by realized outcomes.

Near-term mode, 0 to 3 years (`forecasting.py`, `agent.py`): temporal-graph forecasting over the modern-tier graph plus the game-theoretic agent, producing calibrated probabilistic scenarios. Backtest with rolling windows.

Long-horizon mode, 5 to 20 years (`structural.py`): structural forecasting on slow-moving variables, the power balance from CINC trajectories and power-transition dynamics, the monetary and debt regime, the alliance-network structure, and the accumulation of systemic pressure. Grounded in structural-demographic and long-cycle theory. Output is a scenario space with crisis-probability windows and structural trajectories, conditioned on the current regime and matched to deep analogues, explicitly not dated point predictions. State the boundary in the output: this maps pressure and probability over a window, it does not call exact dates or events.

Calibration (`calibration.py`): Brier-score the near-term probabilistic forecasts against realized outcomes. Evaluate the long-horizon mode by retrodiction, running it as of past dates and checking whether the structural pressure it flagged preceded the crises that followed (the Turchin retrospective method), since point-calibration does not apply at that horizon.

## 14. API and MCP surface

FastAPI endpoints for graph queries, events and effects, network metrics, forecasts and scenarios, regimes, and case studies. MCP tools: `find_actor`, `neighbors`, `events_between`, `escalation_trajectory`, `network_metrics`, `event_effects`, `analogues_for`, `regime_at`, `forecast`.

## 15. Presentation and web

An explorable, force-directed network graph in the MarketGraph lineage, with agent-over-MCP traversal and a time slider across the 120 years so a reader watches the network reconfigure through regimes. Two or three narrated case studies end to end. A long-horizon structural view showing pressure trajectories and scenario windows with visible uncertainty. Restrained, serious visual language. Separate front door from MarketGraph; cross-link as siblings.

## 16. Region pack contract

A pack provides `actors.yaml`, `issues.yaml`, `markets.yaml` (with inception dates), `assets.yaml`, `priors.yaml`, `sources.yaml`, and `marquee_events.yaml`. The core reads a pack and runs unchanged. MENA is pack one; China and Taiwan is pack two.

## 17. Provenance and determinism rules

Every ingested node and edge links to a `Source`. Every number on an `AFFECTED` edge, in the deterministic parts of a forecast, and in `NetworkMetric` is computed by the deterministic core; the AI never originates such a number. Latent estimates carry a std and are labeled as estimates. Forecasts are frozen at generation time with their inputs, so a past call can be scored or retrodicted honestly later.

## 18. Phased milestones

- Phase 0, spine and one case study. LinkML ontology and Kuzu schema applied, CAMEO/Goldstein and the crosswalks loaded, the MENA marquee spine ingested, and one modern episode worked end to end and viewable.
- Phase 1, transmission engine. Deterministic, calendar-aware, multi-frequency abnormal returns across the markets that existed, reading from Postgres and writing effects to Kuzu, reproducible.
- Phase 2, classifier and network analytics. Both heads (including the deep-tier crosswalks) validated; `NetworkMetric` computed across windows.
- Phase 3, deep-history ingestion. Load COW, ICB, V-Dem or Polity, JST, and Shiller; map the deep tier through the crosswalks; populate the 1905-to-1979 graph and the annual and monthly panel; seed clout and salience from CINC and ICOW; segment regimes.
- Phase 4, modern scale and explore. GDELT backfill for the MENA actor set, 13F flows, and the explorable graph with the time slider live.
- Phase 5, reasoning. Near-term probabilistic layer, long-horizon structural layer, analogy engine, market-as-sensor loop, and the calibration and retrodiction harness.
- Phase 6, the China pack. A `packs/china` directory satisfies the contract, the core runs against it unchanged, and the China case study is built.

## 19. Known risks and dependencies

- Non-stationarity is central over 120 years. Regime conditioning is mandatory, not optional; never fit or reason naively across the whole span.
- The 20-year horizon is structural forecasting, not point prediction. State the boundary in every long-horizon output.
- Fidelity-aware weighting: a 1912 annual effect is not a 2012 daily CAR. Carry resolution on every effect and down-weight accordingly.
- The state system is time-varying: empires and states appear and dissolve. Use COW state-system membership so the actor set changes correctly through time.
- Escalation and event crosswalks (COW and ICB to CAMEO and Goldstein) are approximations. Document them and treat deep-tier escalation as coarser.
- Historical intraday is unavailable; daily CAR is the modern workhorse and intraday is recent-only.
- 13F is a coarse, lagged, US-equity-only view of SWF flows.
- Kuzu is single-writer; batch graph writes one at a time.
- Attribution noise: flag overlapping event windows rather than averaging them.

## 20. First Claude Code session

1. Scaffold the repo per section 7, copying the MarketGraph Kuzu, LinkML, explorer, MCP, and 13F patterns, which transfer directly.
2. Write the LinkML ontology (section 8.1), generate Pydantic and JSON Schema, write or reuse the LinkML-to-Kuzu generator, and apply the schema.
3. Stand up the Railway Postgres panel, load CAMEO, Goldstein, the crosswalks, the regime segmentation, and the MENA `marquee_events.yaml`.
4. Build the deterministic transmission engine (section 11) and prove it on one modern marquee event across the markets that existed, reading yfinance and FRED data from Postgres and writing `AFFECTED` edges into Kuzu.
5. Only then wire the classifier, network analytics, deep-history ingestion, and the reasoning layer.

Build Phase 0 to completion on a single modern episode before scaling to the deep archive. The one worked case proves every layer, and it is what the reader sees first.
