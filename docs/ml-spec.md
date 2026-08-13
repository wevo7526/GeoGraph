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

> **Read §2 before §4.** The exploration changed the answer. A fitted model
> scores AUC 0.92 pooled and **0.35 within dyad** — below random. Almost all
> of the apparent skill is telling hot dyads from quiet ones, which needs no
> model, and the timing question a forecast is actually asked is currently
> answered backwards. That finding, not the architecture, is what this
> document is now about.

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

## 2. What the exploration found

> Every number in this section is reproduced by
> `python scripts/explore_panel.py`. Read-only, deterministic, no artifact —
> recheck it rather than trust it, which is the standard every counted
> likelihood in this repo is already held to.

The panel §3 proposes was built and run: 186 dyads with ≥ 8 occupied quarters,
zeros filled between each dyad's first and last observation, label = *a
significant escalation occurs in the next four quarters*, significance = a
departure in the top decile of in-regime departures from that dyad's own
baseline (threshold 8.90). **27,538 usable rows, positive rate 0.1337.**

### 2.1 The trivial baselines are strong

| Predictor | Brier | Pooled AUC |
|---|---|---|
| Base rate | 0.1159 | 0.500 |
| Persistence (escalated this quarter) | 0.0918 | 0.664 |
| Escalated in the last 4 quarters | 0.0901 | **0.776** |
| L2 logistic hazard, 8 features | **0.102** | **0.92** |

Walk-forward (train on everything before the cut, test the following five
years), the logistic hazard beats persistence at every cut:

| Cut | n train | n test | Brier base | Brier persist | Brier logit | AUC persist | AUC logit |
|---|---|---|---|---|---|---|---|
| 1990 | 15,850 | 3,444 | 0.2032 | 0.1522 | 0.1021 | 0.647 | 0.884 |
| 1994 | 18,594 | 3,519 | 0.2227 | 0.1641 | 0.1034 | 0.661 | 0.899 |
| 1998 | 21,403 | 3,464 | 0.2475 | 0.1707 | 0.1016 | 0.687 | 0.924 |
| 2002 | 24,207 | 2,152 | 0.2391 | 0.1706 | 0.1173 | 0.691 | 0.910 |

An AUC of 0.92 on conflict data should be disbelieved before it is believed.

### 2.2 …and the skill is almost entirely between dyads

Decompose the label's variance: **29.7% between dyads, 70.3% within**. A model
that only knows *which dyad it is looking at* can capture at most the first
share. Everything a forecast is for lives in the second.

Scoring within dyad — does the model rank a *given* dyad's quarters correctly?
— over the 72 dyads whose test window contains both outcomes:

| Feature set | Brier | Pooled AUC | **Within-dyad AUC** |
|---|---|---|---|
| All features | 0.1016 | 0.924 | **0.349** |
| Dynamics only (no dyad averages) | 0.1039 | 0.922 | **0.370** |
| Dyad averages only | 0.1070 | 0.917 | **0.159** |
| Persistence | — | 0.687 | **0.454** |

Every one of them is **below 0.5**. This is Simpson's paradox in a forecasting
harness: pooled, the model looks excellent; conditioned on the dyad, it ranks
quarters *backwards*.

The inversion is not noise, and it has a mechanism. The label asks whether
escalation follows in the next four quarters. At the **end** of a burst the
dyad is active (`sig_now = 1`) and the following year is quiet → label 0. In
the quarter **before** a burst the dyad is quiet and the following year is
violent → label 1. So the feature that dominates every pooled model points the
wrong way exactly at the two moments that matter. Consistently below 0.5 also
means there is real signal here — inverted signal is still signal — but it is
not the signal the current estimator assumes.

**Consequence: the modeling problem is burst TIMING, not dyad ranking.** The
production near-term forecaster currently reasons "this dyad escalates often,
therefore it will escalate again" (0.98 for Israel–Lebanon). Between dyads
that ordering is right. Within a dyad, on a one-year horizon, the evidence
says escalation is mean-reverting, not persistent.

### 2.3 Memory, sequence, and how much is left to learn

- **Autocorrelation** of the significant-escalation indicator: r = 0.46 at lag
  1 quarter, decaying only to 0.35 at 20 quarters. Long memory — but §2.2
  shows most of it is the dyad's fixed character, not dynamics.
- **Sequence information**: H(quad_class) = 1.513 bits, H(quad | previous) =
  1.306 bits → mutual information **0.206 bits, 13.6% of the marginal**. There
  is genuine sequence structure for a sequence model to find. It is modest.
- **Zero inflation**: 73% of dyad-quarters hold no event at all; only 6.1%
  hold a significant one. A hurdle/zero-inflated head is required, not
  optional.
- **The graph is thinner than it looks**: of 21,590 durable relations, 18,648
  are IGO **membership**, 2,937 alliance, 3 proxy, 2 rivalry. Membership is
  near-constant across dyads and will contribute almost nothing
  discriminative. The relational signal a GNN would need is the alliance and
  rivalry structure, and rivalry is currently 2 edges.

### 2.4 Non-stationarity is severe, and partly artefactual

Positive rate by decade: 1970s 0.020 · 1980s 0.131 · 1990s 0.252 · 2000s
0.252 · 2010s 0.029. A twelvefold swing — which tracks GDELT **coverage**, not
the world. The 1970s and 2010s troughs are the wire thinning at both ends.

Walk-forward, this is fatal to calibration: training before 1996 (positive
rate 0.090) and testing after (0.250) makes the train base rate score Brier
0.2136 on the test period — worse than simply knowing the test-period rate.
**A model trained on this panel learns coverage intensity as much as conflict
intensity.** That is the strongest argument in this document for §3 being a
data task before it is a modeling task.

---

## 3. Prerequisite: close the data gap

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

## 4. The three models

### Stage A — event timing model (the learned part)

**The exploration reframes this stage.** It was scoped as "predict the next
event type"; §2.2 shows the pooled version of that question is nearly free
(hot dyads are hot) and the hard, useful version is **when does a quiet dyad
burst, and when does an active one stop?** Every design choice below follows
from that.

**Unit of prediction:** dyad × month. **Target:** a hurdle pair — occurrence
("any event") and, conditional on it, intensity (quad_class × Goldstein
bucket). 73% of dyad-quarters are empty, so the two heads are genuinely
different processes.

**Headline metric is WITHIN-DYAD, always.** A pooled score on this panel is
not evidence. Report pooled if you like; decide on within-dyad.

| # | Model | Why it exists | Status |
|---|---|---|---|
| 0 | Base rate + persistence | The floor. | Measured: within-dyad AUC 0.454 |
| 1 | L2 logistic hazard, hand-built features | The yardstick. ~10 parameters, walk-forward, interpretable. | Measured: pooled 0.92, **within-dyad 0.349** — fails |
| 1b | **Same model, dyad-demeaned features** | The missing control. Express every feature as a deviation from that dyad's own history, so the fit cannot spend its capacity on dyad identity. This is the cheapest experiment in this document and it is the one to run next. | Not run |
| 2 | **Self-exciting point process (Hawkes)** on the dyad's event stream | The natural model for clustered arrivals — which is exactly what §2.2 says the data is. A background rate (the dyad's character, the part already solved) plus a decaying excitation kernel (the burst dynamics, the part unsolved). ~3–5 parameters per dyad with a shared kernel, which is the right size for 15k events. Interpretable: the decay constant *is* an escalation half-life. | Recommended first real model |
| 3 | Survival / change-point framing — time-to-next-burst with time-varying covariates, or Bayesian online change-point detection on the dyad series | Answers the timing question directly rather than through a binary label, and produces a hazard curve instead of one probability. | Alternative to 2, or complement |
| 4 | Sequence model — 2-layer GRU or ≤ 100k-param transformer over dyad history | Only justified by the 0.206 bits of sequence mutual information in §2.3, which is real but modest. Must beat 1b and 2 within-dyad. | Deferred |
| 5 | Graph-conditioned (R-GCN / GraphSAGE) | §2.3: 86% of durable relations are IGO membership, near-constant across dyads. Until the alliance/rivalry tier is denser this has little to add. Ablate ruthlessly. | Deferred, weakest case |

The honest reading of this table: **"deep learning" is stages 4–5, and they
are the two the data supports least.** The techniques that fit this
archive — self-exciting point processes, survival models, hierarchical partial
pooling — are classical, small, and interpretable. That is not a consolation
prize; on 15k events with a coverage-contaminated label it is the correct
answer, and it is also the answer that can be defended to someone who asks why
the model said what it said.

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

§2.2 *raises* the value of this stage. If the unsolved problem is timing —
why a dyad that has escalated for six quarters stops, why a quiet one
starts — then the missing ingredient is a mechanism for the decision to
escalate, and that is precisely what a game supplies and a curve fitted to
frequencies does not.

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

## 5. Validation — the part that decides whether any of this is real

- **Report within-dyad first.** §2.2 is the whole argument: pooled AUC 0.92,
  within-dyad 0.35. Any headline number on this panel that is not conditioned
  on the dyad is measuring a fact nobody needed a model for. Pooled scores may
  be reported; they may not be the basis of a decision.
- **Walk-forward only, by time.** Train ≤ T, validate T+1…T+k, roll. A random
  train/test split leaks the future through a dyad's own history and will
  produce a beautiful, worthless AUC. `core/reasoning/backtest.py` already
  implements exactly this discipline for the paper model (truncate the archive,
  recompute through the *same* code path, record skips) — reuse it.
- **Beat three baselines or don't ship:** base rate, persistence, and the
  stage-1 hazard model — all scored within dyad, where persistence currently
  sits at 0.454 and the fitted model at 0.349. Clearing 0.5 is the first real
  milestone, and nothing has cleared it yet.
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

## 6. Where it lands, and the §17 question

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

## 7. Phasing

| Phase | Work | Gate to pass |
|---|---|---|
| **M0** | GDELT 2006→2026 backfill; run the event study; build the dyad-timestep panel with explicit negatives | Panel exists; ≥ 10× current recent-era event mass; decade-to-decade positive rate stops tracking coverage |
| **M1** | Dyad-demeaned features (stage 1b) + the within-dyad harness | **Within-dyad AUC > 0.5.** Nothing has cleared this yet; until something does, no learned number ships |
| **M2** | Hawkes / survival timing model (stage 2–3) | Beats M1 within dyad, walk-forward |
| **M3** | Stage B1 equilibrium features; §17 amendment written | Features improve M2 within dyad, or are dropped |
| **M4** | Stage C conditional market distributions off measured `AFFECTED` | Distributions reported with n and IQR; thin samples flagged |
| **M5** | Sequence model (stage 4) | Beats M2 within dyad, or M2 stays in production |
| **M6** | Graph conditioning (stage 5); B2 inverse RL | Beats M5 on ablation, or the graph is dropped from the model |

M0 is a data task and it dominates everything after it: while the label tracks
wire coverage, every model learns coverage. M1 is one afternoon's work and is
the experiment that decides whether the rest of this document is worth
building — a demeaned model that still cannot clear 0.5 within dyad is telling
us the timing of escalation is not predictable from this archive, which is a
publishable finding and a much better outcome than a dashboard quoting 0.92.

Note what is *not* gated on any of this: the fixes in §8 already shipped, and
the deterministic core keeps working with no model in it at all.

---

## 8. The bugs this replaced — already shipped

Scoping this model started by finding that both frozen forecasts were wrong,
for the same reason the model will have to survive: uneven density. Both are
fixed and deployed; they are recorded here because they are the stage-0
baseline everything above must beat, and because they are the same mistake a
learned model makes silently.

**Near-term.** A single pooled continuation rate was assigned to every focal
dyad:

```
further_escalation:dyad:ansar-allah--cow-670     0.9347   ← 0 episodes of its own
further_escalation:dyad:cow-660--cow-666         0.9347
further_escalation:dyad:cow-630--cow-645         0.9347
```

Three different dyads, one number, and one of them had never been observed
escalating at all. 93% because an episode was *any* dyad-quarter holding *any*
escalating event, so pooled over 5,572 episodes the estimator answered "does an
active dyad stay active" — which at wire density is always yes.

Now: an episode requires a departure in the top decile of in-regime departures
from the dyad's own baseline; each dyad's rate is its own record shrunk toward
the pool by beta-binomial method of moments; focal dyads must clear an evidence
bar before ranking. MENA reads 0.98 / 0.98 / 0.87 over three dyads with
records.

**Long-horizon.** The composite pressure was a mean over whatever components
existed in a given year. Past the capability data's end in 2022 that became a
mean of the two noisiest components, computed from windows holding six events,
and 2025 printed 0.93 — an all-time high across 120 years, assembled from two
events and a definition change. Now a trailing window under 30 coded events
yields no value, and a year enters the composite only with every component
present. The MENA trajectory runs 1911–2017; the gaps are reported.

**What §2 adds to this.** The near-term fix makes the numbers *defensible*
between dyads. It does not make them a timing forecast — §2.2 shows nothing in
this archive currently does that. The frozen payloads now carry
`evidence_span`, which is the honest caveat until M0 closes the data gap: a
likelihood stamped as-of 2025 can rest almost entirely on evidence from
1979–2005.
