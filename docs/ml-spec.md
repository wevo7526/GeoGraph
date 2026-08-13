# ML spec — a learned event-sequence model over the archive

Status: **SCOPE, not locked.** `docs/build-spec.md` remains the master spec;
this document proposes an addition to Part 13 (the reasoning layer) and must
be reconciled with §17 before any of it is built. Where it deviates, it says
so and says why.

The goal, in the user's words: *train a deep learning model on the graph, put
game theory in it, predict sequences of events, and then the market move
associated with that type of event.*

That is three models, not one, and they should stay three. The rest of this
document is what each one can honestly be given the data that exists.

---

## 1. What the archive actually holds

Measured against `data/geograph.kuzu` on 2026-08-12 (MENA loaded; the china
and eurasia GDELT artifacts were not in this local volume, so multiply the
event counts by roughly 1.6 for the deployed archive).

| Quantity | Count |
|---|---|
| Events | 107,040 |
| …dyad-coded | 107,040 (100%) |
| Actors | 747 |
| Dyads | 665 |
| Durable `RELATES_TO` edges | 21,590 |
| `AttributeEstimate` (CINC/clout, actor-year) | 13,984 |
| Markets | 8 |
| Measured `AFFECTED` edges | **0 in this volume** — verify on Railway |

Event mass by decade — **this is the finding that governs everything below**:

| Decade | Events | | Decade | Events |
|---|---|---|---|---|
| 1900s | 27 | | 1970s | 952 |
| 1910s | 121 | | 1980s | 13,203 |
| 1920s | 66 | | 1990s | 47,701 |
| 1930s | 93 | | 2000s | 44,136 |
| 1940s | 159 | | **2010s** | **146** |
| 1950s | 189 | | **2020s** | **9** |
| 1960s | 238 | | | |

98% of the archive sits in 1979–2005, which is exactly the span of the loaded
GDELT wire. Before it there is a curated spine and the COW deep tier; after it
there is a curated spine and nothing else. **The archive has 155 events from
the last twenty years.**

Effective sample size for a sequence model:

| Unit | Count |
|---|---|
| Active dyad-months | 14,964 |
| Active dyad-quarters | 9,005 |
| Dyads with ≥ 500 events | 42 |
| Dyads with ≥ 100 events | 98 |
| Dyads with ≥ 24 active months | 124 |
| **Median dyad** | **3 active months** |

Class mix is heavily imbalanced: verbal_cooperation 67,465 · material_conflict
18,166 · verbal_conflict 12,811 · material_cooperation 8,598.

### What that means

- **This is not a deep-learning-scale dataset.** ~15k dyad-month observations
  concentrated in ~100 dyads over ~27 years is a *small-data* problem. A
  transformer of the size that word usually implies will memorize it. The
  parameter budget that fits this data is on the order of 10⁴–10⁵, not 10⁸.
- **The long tail is unusable.** The median dyad has three active months. Any
  per-dyad model must pool across dyads with partial pooling (hierarchical /
  embedding-based), or restrict to the ~124 dyads with real histories and say
  so.
- **The binding constraint is the 2006–2026 gap, not the architecture.** A
  model trained to 2005 and asked about 2027 extrapolates 21 years across the
  largest distribution shift in the dataset (post-9/11 order, the rise of
  China, the return of great-power conflict, and — mechanically — a total
  change in wire coverage). No architecture repairs that.

---

## 2. Prerequisite: close the data gap

**Nothing in §3 should be built before this.** It is also the cheapest item
here, because the machinery already exists and works.

1. **GDELT 2006 → present.** `scripts/backfill_gdelt.py` already reads the free
   raw files at data.gdeltproject.org with no BigQuery project. The loaded
   artifacts stop at 2005 by choice, not by limitation. Extending to 2026 takes
   the archive from ~107k to an estimated 1–3M events per pack and, more
   importantly, gives the model twenty years of *recent* history and a real
   held-out era. GDELT 2.0 (2015+) has a different schema than 1.0 and needs a
   second reader; budget for that.
2. **Run the transmission layer.** `AFFECTED` is empty in the local volume.
   Stage C is a lookup over measured effects, so it has no input until
   `run_event_study --all` has run over the full spine.
3. **Materialize a training panel.** The graph is not a training-data format.
   A `dyad_timestep` table in Postgres (`core/panel/pg_store.py` is the
   precedent) keyed `(dyad_id, period)` with features and labels, rebuilt
   deterministically from the graph by a script. Kuzu is single-writer and the
   API holds the lock — training must never touch it live.
4. **Decide the zero policy.** Most dyad-timesteps have no events at all. The
   panel must contain explicit negatives (dyad existed, state-system membership
   windows satisfied, no event) or every model will be trained on a biased
   positive-only sample.

---

## 3. The three models

### Stage A — event sequence model (the learned part)

**Unit of prediction:** dyad × month. **Target:** next-window distribution over
(quad_class × Goldstein bucket), plus an occurrence head for "any event at
all" — a hurdle model, because occurrence and intensity are different
processes and the zero mass is large.

**Build in this order. Each stage must beat the previous one walk-forward or
it does not ship.**

| # | Model | Why it exists |
|---|---|---|
| 0 | Base rate + persistence | The floor. "Next month looks like this month" is a genuinely strong baseline in conflict data and beats most published models. |
| 1 | Regularized logistic / gradient-boosted hazard on hand-built features | The yardstick. Interpretable, ~1k parameters, fits this data comfortably. This is also the model that should serve production first. |
| 2 | Sequence model — 2-layer GRU or a 2-block, 4-head transformer, ≤ 100k params, over the dyad's own history | Captures escalation *dynamics* (spirals, tit-for-tat lags) that a feature vector flattens away. |
| 3 | Graph-conditioned — R-GCN or GraphSAGE over the actor graph, actor/dyad embeddings feeding stage 2 | **This is where the graph earns its place.** The dyad's prediction conditions on its neighbourhood: allies, shared IGOs, rivals-of-rivals, brokerage position. `RELATES_TO` (21,590 edges) and the COW alliance/IGO tier are the substrate. |

Features for stage 1 (and inputs to 2–3), all already in the graph:

- Dyad state: EWMA baseline, escalation magnitude, recent Goldstein mean,
  months since last material conflict, event counts by quad class over 1/3/12m.
- Capability: CINC ratio and its trend, `transition_proximity` (Organski).
- Network: degree/betweenness/community from `graph/analytics.py`, computed on
  the membership- and validity-windowed subgraph as of the timestep.
- Relational: alliance present, shared IGO count, prior MID history.
- Regime: monetary order and polarity epoch as categorical conditioning —
  never as a feature that lets the model match across regimes silently.

**Sequence generation:** roll the model forward autoregressively with sampling
to get *sequences*, and report the ensemble as a distribution over paths, never
a single path. Long rollouts compound error; cap the horizon at the point where
walk-forward calibration degrades, and publish that cap.

### Stage B — game theory, doing actual work

Game theory here is not garnish on a neural net. Two concrete roles, in
increasing ambition:

**B1 — Equilibrium features (build first).** Model each dyad-timestep as a
one-shot escalation game: each side chooses escalate / hold / de-escalate.
Payoffs are *estimated from the archive*, not assumed — capability ratio from
CINC, cost of conflict from the measured market effect on each side's exposed
markets (Stage C's data, used in reverse), audience/alliance constraints from
the graph. Solve the 2×2 (closed form for mixed strategies) or the 3×3 via a
support-enumeration solver. The equilibrium mixing probabilities become
**features** in Stage A. Cheap, deterministic, testable.

**B2 — Empirical game-theoretic analysis / inverse RL (the real version).**
The archive *is* observed play. Infer the payoff parameters that make observed
dyadic behaviour approximately rational (maximum-entropy IRL over the observed
action sequences), then predict with the fitted game. This is a defensible
research direction with a real literature, it produces *interpretable* output
(a payoff matrix a human can argue with), and it degrades gracefully — even a
poorly-fit payoff model tells you which dyads behave least like rational
actors, which is itself a finding.

**B3 — Decoder constraint.** When rolling Stage A forward, penalize or mask
transitions that are equilibrium-inconsistent. This stops the autoregressive
model from walking into escalation spirals no rational dyad would enter.
Optional; only after B1/B2 are validated.

### Stage C — event → market move

**This stage should not be learned, and that is the point.**

The repo already *measures* what events do to markets: `AFFECTED` edges carry
CAR at a stated `resolution`, with markets that did not exist at event time
recorded as skips and calendar handling (Gulf Sun–Thu vs US Mon–Fri) already
correct. So the market stage is a **conditional lookup, not a second neural
net**:

> Given a predicted event of type *t*, magnitude *m*, on dyad *d*, in regime
> *r* → the empirical distribution of measured effects for comparable events on
> market *k*.

Report a distribution (median, IQR, n), never a point. Gate by
`regimes.comparable()` exactly as the analogy engine does. Where the measured
sample is thin, say the sample is thin — the codebase already has that habit.

This keeps the §17 invariant intact end to end: **the learned model predicts
events; the measured transmission layer prices them.** No model originates a
market number.

---

## 4. Validation — the part that decides whether any of this is real

- **Walk-forward only, by time.** Train ≤ T, validate T+1…T+k, roll. A random
  train/test split leaks the future through a dyad's own history and will
  produce a beautiful, worthless AUC. `core/reasoning/backtest.py` already
  implements exactly this discipline for the paper model (truncate the archive,
  recompute through the *same* code path, record skips) — reuse it.
- **Beat three baselines or don't ship:** base rate, persistence, and the
  stage-1 hazard model.
- **Metrics:** log loss and Brier for occurrence (`calibration.brier_score`
  exists); ranked probability score for ordinal intensity; reliability diagrams
  per regime, because a model well-calibrated on 1990s data and miscalibrated
  after 2010 is the failure mode this archive invites.
- **Regime-held-out evaluation.** Train on one monetary order, test on the
  next. Performance will drop. Publishing how much it drops is the honest
  measure of what a 2027 claim is worth.
- **Ablate the graph.** If stage 3 does not beat stage 2, the graph is not
  adding signal and should not add complexity.

---

## 5. Where it lands, and the §17 question

Proposed layout, following the existing shape:

```
core/models/
  panel.py        dyad-timestep feature/label table (deterministic from graph)
  features.py     feature construction, regime-conditioned
  hazard.py       stage-1 baseline (numpy IRLS or gradient boosting)
  sequence.py     stage-2/3 learned model
  games.py        equilibrium solver + payoff estimation (stage B)
  registry.py     versioned model artifacts: hash, train span, metrics
scripts/train_model.py     offline, writes a versioned artifact
scripts/run_forecasts.py   extended: freeze model output as Forecast nodes
```

**Deployment shape:** train offline, ship a versioned artifact, and at boot run
a forward pass that freezes predictions into `Forecast` nodes — the existing
freeze pattern, which already gives reproducibility and later scoring for free.
Railway is CPU-only and the container boot is already long; no training there.

**The §17 reconciliation, stated plainly.** The invariant says *the AI never
originates a number* that lands in `AFFECTED`, `NetworkMetric`, or the
deterministic part of a `Forecast`. A trained model is deterministic
computation, not the LLM, so it does not violate the letter of §17 — but it
does violate its spirit if its output is presented indistinguishably from a
counted base rate. Proposal:

1. A learned prediction is a **new** `Forecast` with `method='model:<name>@<version>'`
   and the artifact hash in `frozen_inputs`. Never an overwrite of a counted
   forecast.
2. `AFFECTED` and `NetworkMetric` stay measurement-only. Unchanged.
3. Every learned number ships with its walk-forward calibration alongside it.
   A prediction without its score is not shown.

This needs to be written into build-spec §17 as an explicit amendment before
Stage A ships, not inferred from this document.

---

## 6. Phasing

| Phase | Work | Gate to pass |
|---|---|---|
| **M0** | GDELT 2006→2026 backfill; run the event study; build the dyad-timestep panel with explicit negatives | Panel exists; ≥ 10× current recent-era event mass |
| **M1** | Stage-1 hazard model + walk-forward harness + baselines | Beats base rate *and* persistence on held-out years |
| **M2** | Stage B1 equilibrium features; §17 amendment written | Features improve M1, or are dropped |
| **M3** | Stage C conditional market distributions off measured `AFFECTED` | Distributions reported with n and IQR; thin samples flagged |
| **M4** | Stage-2 sequence model | Beats M1 walk-forward, or M1 stays in production |
| **M5** | Stage-3 graph conditioning; B2 inverse RL | Beats M4 on ablation, or the graph is dropped from the model |

M0–M1 is the honest first release: it produces genuinely better numbers than
the current pooled base rate (which returns an identical 93% for every focal
dyad — see §7) and it is defensible. M4–M5 is where "deep learning" begins,
and it should only begin once M0 has given it data worth learning from.

---

## 7. The bug this replaces

The current near-term forecaster (`core/reasoning/forecasting.py`) computes a
single pooled continuation rate and assigns it to **every** focal dyad, so the
frozen MENA call reads:

```
further_escalation:dyad:ansar-allah--cow-670     0.9347
further_escalation:dyad:cow-660--cow-666         0.9347
further_escalation:dyad:cow-630--cow-645         0.9347
```

Three different dyads, one number, and that number is 93% because pooling
across 5,572 episodes measures "does any active dyad stay active" rather than
"does *this* dyad re-escalate". The pool also ignores the region entirely.

The minimum fix, independent of everything above and worth doing immediately,
is a **partially-pooled (empirical-Bayes) per-dyad rate**: shrink each dyad's
own continuation frequency toward the pooled rate in proportion to how thin its
own sample is. That is one function, it is deterministic and countable, it
produces a *different and defensible* number per dyad, and it is the correct
stage-0 baseline for everything in §3.
