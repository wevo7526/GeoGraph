# GeoGraph master build plan — the data, the games, and the ship

> The one document that ties the machine together: what data we have, what
> games we solve and how, the end-to-end pipeline, and the phased sequence to
> ship a real piece of software. Companion to `docs/build-spec.md` (locked
> foundation), `docs/event-impact-spec.md`, and `docs/product-redesign-spec.md`.
> Where this deviates from build-spec it says so.

## 0. The thesis, in one line

**Geopolitical events → a signed, market-aware escalation signal → a learned
model → a stochastic incomplete-information game solved to a real Bayesian Nash
equilibrium → a distribution over future event sequences → priced to markets.**
The user-facing output is a single object: for any relationship or event, the
market's price response — measured in the past, forecast forward as an
equilibrium *band*, in plain language.

The invariant that makes it honest: **prices are always measured `AFFECTED`
abnormal returns; the ML and the game only decide the weights.** No model ever
originates a price (build-spec §17).

---

## 1. The data we have (and what each is good for)

| Source | What it is | Coverage | Role in the machine | Known gap |
|---|---|---|---|---|
| **GDELT wire corpus** (`data/derived/gdelt-<pack>-*.tsv.gz`, `core/wire/`) | ~1.3M dyadic events, CAMEO→Goldstein scored | 1979→2026, per pack | The behavioral data: the escalation series and the game's transition kernel | Latest quarter is partial (recency); volume ≠ hostility |
| **Head B escalation** (`core/classifier/escalation.py`) | Per-dyad EWMA baseline; signed direction + magnitude per event | Whole corpus + deep tier | Scores every event; feeds panel, kernel, model | `magnitude` is a departure (acceleration), not a level; the signed level (`tone`) was under-used |
| **COW deep tier** (`core/ingestion/`, packs) | States, MIDs, **CINC capabilities**, alliances, IGO | 1905+ | Actor capability (the game's capability axis), the curated spine | ICB/ICOW deferred |
| **Price panel** (`core/panel/`, Postgres) | Multi-frequency price series | 1871→present, ~30 markets/pack | The market side: returns for the event study | Row-by-row upsert; recency guard |
| **AFFECTED edges** (`transmission/effects.write_effects`) | Measured event×market×window CAR, provenance-stamped | 278k+ and climbing | THE market truth: base rates for pricing, the event→price answer | Study hasn't converged → coverage partial (#3) |
| **NetworkMetric** (`graph/analytics.py`) | Centrality/brokerage/communities over windowed subgraphs | Decades + regime spans | Structural context | No UI (deferred) |
| **Packs** (`packs/{mena,china,eurasia}`) | Actors, markets, dyads, regimes, marquee events | 3 regions | The contract; nothing in `core/` special-cases a region | — |
| **Regimes** (`reasoning/regimes.py`) | Monetary-order segmentation | 1905+ | The admissibility gate for analogy/precedent | — |

**The two data facts that drive the modeling work:**
1. We have the **market movements** (`AFFECTED`) to *train* on — and today the ML
   uses them nowhere (feature nor target). (#25)
2. We have the **counted transition kernel** and **capability (CINC)** and the
   **observed action history** (for Bayes-filtered beliefs) to solve a genuine
   incomplete-information game — the pieces exist; the solver doesn't use the
   belief axis. (#21)

---

## 2. The games we want to solve

### 2.1 The primitive

A **two-player, incomplete-information, finite-horizon stochastic game**, one
per dyad, per quarter forward over the forecast horizon (H=4):

- **State** `s = (x, k, μ)`: intensity band `x ∈ {0..5}` (5 = rupture, open
  top), capability band `k` (from CINC), and **belief** `μ` over the opponent's
  private type. *The belief axis is the piece missing from the current solver.*
- **Private type** `θ ∈ {irresolute, resolute}`: each side's cost of
  escalation, unknown to the other. This is what makes it *Bayesian*.
- **Actions** `a ∈ {de-escalate, hold, escalate}`.
- **Transition kernel** `P(x' | x, a_A, a_B)`: **counted from the wire**
  (`games/transition.py`), thin cells pooled — this is the measured stochastic
  process, not an assumption.
- **Payoffs** (`games/solve.stage_payoff`): Fearon-style — escalation buys
  pressure but is costly now in proportion to type (the separating mechanism),
  with an audience cost for backing down. Fitted by indirect inference
  (`games/estimate.py`).

### 2.2 The solution concept — a REAL Bayesian Nash equilibrium

The target is a **Perfect Bayesian Equilibrium**: type-conditioned strategies
`σ_θ` where each type best-responds to the belief-weighted average of the
opponent's type-conditioned play, and beliefs update by Bayes' rule along the
path. The reputational spiral — *escalate → raise the posterior of resolve →
itself a reason to escalate* — must live **in** the equilibrium, not be pasted
on afterward.

Current gap (see the BNE analysis): `solve()` computes a **symmetric
complete-information logit-QRE** (opponent typed as `own`, no belief axis) and
mixes the two type-policies by belief *ex post*. It is not a BNE. (#21)

### 2.3 How we solve it — the methods

No single LP solves a general-sum Bayesian stochastic game to Nash (PPAD-hard).
The correct, tractable design:

1. **Outer loop: backward induction** over the finite horizon (kills the folk
   theorem; already in place, keep it).
2. **Inner stage solver — exact BNE:** expand the type structure into the
   **agent/ex-ante normal form** (a 9×9 bimatrix: a pure strategy is a map
   `θ ↦ action`), and solve it with **Lemke–Howson** (`nashpy`). Restore the
   belief axis to `value`/`policy` (`BELIEF_LEVELS=5` grid already defined in
   `state.py`, unused). Select a unique, continuous equilibrium by **tracing
   the logit-QRE homotopy** from high temperature down — this keeps the
   continuity the indirect-inference fit needs *and* lands on an exact Nash.
3. **Equilibrium band — a correlated-equilibrium LP:** a single
   `scipy.optimize.linprog` (HiGHS) per stage over the joint law
   `π(θ_A, a_A, θ_B, a_B)` with linear obedience + consistency constraints,
   solved twice (min and max expected escalation). This yields an **escalation
   band**, not a point — and π **is the stochastic process** tying dyad actions
   to the next state, the object to expose to the user. (#22)
4. **Best-response steps (stationary variant): MDP-as-LP** — fix the opponent's
   strategy, solve each type's MDP by the classic primal LP; alternate for
   exact best-response dynamics.

### 2.4 The bridge and the output

- The learned intensity trajectory **tilts the kernel** (`games/bridge.py`,
  exponential, bounded, audited `name@hash`) — the ML's forward read bends the
  measured dynamics.
- The solved policy → **path distribution** (`games/paths.py`, the fan) → each
  step **priced from `AFFECTED` base rates** (`games/pricing.py`). Stop pruning
  the rupture tail (#24).
- Output per dyad: `P(escalate)` per state/type, the per-quarter band fan (with
  the CE band), and the priced modal sequence. This is what the Relationship
  page's "how it plays out" renders.

### 2.5 Identification (why the fits must converge)

`converged:false` for mena/china is the **parameter fit** (Nelder–Mead), not
the equilibrium: the objective clips into a box → flat plateaus → the simplex
stalls with three params on bounds. δ pins at 0.5 (near-myopic), understating
escalation persistence. Fix with a bound-respecting optimizer
(`differential_evolution`/L-BFGS-B), fit only the identified subspace, and add
the **`duration` second moment** (the market-implied persistence statistic —
the bond curve's answer to "how long do these crises last") to separate
discount from cost. (#23)

---

## 3. The pipeline, joint by joint (module map + status)

| # | Joint | Module | Status |
|---|---|---|---|
| 1 | events → scored (signed) | `classifier/escalation.py` | WIRED; needs a signed *level* surfaced (#25) |
| 2 | scored → panel/features | `models/panel.py`, `models/features.py` | WIRED; add signed measure + market feature (#25) |
| 3 | features → learned model | `scripts/train_forecaster.py` → `models/intensity.json` | WIRED; add market movements to training (#25) |
| 4 | model → game kernel tilt | `games/bridge.py` | WIRED |
| 5 | kernel + beliefs → **BNE policy** | `games/solve.py` | **BROKEN as a BNE** — no belief axis (#21) |
| 6 | policy → path distribution | `games/paths.py` | WIRED; un-prune rupture tail (#24) |
| 7 | path → market prices | `games/pricing.py` reads `AFFECTED` | WIRED |
| 8 | measured effects written | `transmission/effects.write_effects` | WIRED (sole writer); study must converge (#3) |
| 9 | event → market prices (product) | `reasoning/impact.py`, `/api/impact/*` | WIRED (shipped) |
| 10 | the read → surface | Relationship page, Watchlist | WIRED (shipped); direction fixed |
| 11 | equilibrium band exposed | `games/solve_ce()` (new) → surface | TODO (#22) |
| 12 | calibration / track record | `reasoning/calibration.py`, `run_backtest.py` | WIRED; re-validate after model changes |

---

## 4. Invariants that hold throughout (non-negotiable)

- **Provenance:** every sourced edge carries a `source_id` that resolves; the
  AI never originates a number in `AFFECTED`, `NetworkMetric`, or the
  deterministic core of a Forecast.
- **One writer:** `transmission/effects.write_effects` is the only `AFFECTED`
  writer; numbers cross panel→graph in one direction.
- **Regime-gated analogy:** `regimes.comparable` is an admissibility gate, not a
  score — no modern question answered with Bretton-Woods evidence.
- **Fidelity gradient:** markets that didn't exist at event time are recorded
  skips; deep-past effects are down-weighted, never equal to daily CAR.
- **Honest empties:** absent measurement reads "no comparable history," never
  "$0"; a broken API reads "couldn't reach the archive," never "empty."
- **Reproducible artifacts:** offline fits are a pure function of the repo
  (corpus-first); a market-aware model either materializes committed CAR
  aggregates or cites the deviation.
- **Plain surface:** the machine keeps its exact names internally; the user
  reads English.

---

## 5. The build, phased to ship

Each phase ends at a shippable, verified state (pytest/ruff/mypy + vite build,
pushed to `main`). Ordered by dependency and by how much each unlocks.

### Phase 0 — DONE (shipped this program)
Fast deploys (fingerprint), shared effects-read, **Event Impact engine + API**,
the **Relationship page + Watchlist**, the game exposed as "how it plays out",
IA cleanup (retired the disjointed tabs), and the **direction fix** (escalation
= signed tone). The product spine exists and is honest.

### Phase 1 — Make the data real (the substrate for everything downstream)
- **#3/#4** Event study converges: batch the per-event Postgres commits + Kuzu
  merges, hoist the hot-loop parsing, per-market watermark. → `AFFECTED` full
  and fresh, so `expected`/prices stop being sparse.
- **#5** Restore the measuring steps on boot once convergence is confirmed.
- **Gate:** production `AFFECTED` coverage reaches the full spine; Relationship
  pages show measured moves, not empties.

### Phase 2 — Make the signal right (correctness of the automated read)
- **#25a** Add a **signed per-quarter measure** to `panel.build` (surface,
  near-term episodes, and model can tell "no acceleration" from "no hostility").
- **#25b** Bring **market movements into training**: a per-dyad-quarter signed
  CAR feature/target; re-run the within-dyad ablation; the gate decides if it
  earns its place. Materialize committed CAR aggregates for reproducibility.
- **Gate:** the near-term and model reads agree with the signed direction on a
  battery of known cases (China–Japan escalating, etc.); the gate passes.

### Phase 3 — Solve the games for real (the core)
- **#23** Fit convergence (bound-respecting optimizer + duration second moment)
  → mena/china converge; escalation persistence un-muted.
- **#21** Restore beliefs → a real Bayesian Nash equilibrium (belief axis,
  type-expanded stage, reputational spiral in the equilibrium).
- **#22** The **LP/CE band**: `solve_ce()` returns min/max escalation bounds and
  the joint law π; exposed on the Relationship page's "how it plays out" as a
  band, not a point. Add `nashpy` (exact stage BNE via Lemke–Howson + QRE
  homotopy).
- **#24** Stop under-representing escalation (rupture-tail floor, QRE precision,
  thin-cell fallback).
- **Gate:** the solved equilibrium reproduces the reputational spiral on a
  synthetic separating case; the CE band brackets the QRE point; refit
  artifacts re-validate against the walk-forward backtest without regression.

### Phase 4 — Product depth & polish
- **#19/#20** Case study generator + deepen the three-per-region to real,
  data-grounded conclusions (measured escalation arc + market moves + verdict).
- **#13/#14** Explorer slider debounce/abort + 3D stability; fetch dedup; the
  small correctness bugs; prune the now-dead API surface.
- **#6/#7/#8** API robustness (per-thread Kuzu connections, aggregate the
  full-scan endpoints, batched panel upserts).
- **#15** Feature-flow decisions (sensor loop, unsurfaced backends).

### Phase 5 — Validation & ship
- Re-run the walk-forward backtest and calibration on the new models; confirm
  no regression and that the market-aware, belief-carrying model beats the
  prior within dyad.
- Acceptance pass against §6.
- Tag a release.

---

## 6. Definition of done — "a real piece of software"

1. **Correct read:** for a battery of known dyads, the surface direction
   (escalating/easing) matches the signed evidence; no magnitude/level
   confusion; the partial current quarter is handled.
2. **Real equilibrium:** `solve()` is a Bayesian Nash equilibrium with beliefs
   in it; mena/china fits converge; the CE band is exposed; escalation is not
   systematically under-counted.
3. **Market-trained:** the model uses measured market movements as evidence and
   passes its within-dyad gate; the invariant (AI never originates a price)
   holds.
4. **Rich data:** the event study is converged; `AFFECTED` covers the spine;
   Relationship pages are full, not empty.
5. **Coherent product:** one front-of-house (Explorer · Relationship ·
   Watchlist · Case studies); the game is legible ("how it plays out toward
   equilibrium"); case studies reach conclusions; everything speaks English.
6. **Operable:** deploys open the graph fast; the API is safe under concurrency;
   nothing lies by zero.
7. **Honest:** every invariant in §4 holds, verified by the test suite.

---

## 7. Sequencing rationale & risk

- **Data before models before product depth:** a converged study (Phase 1) is
  the substrate — the models and the surface are only as good as `AFFECTED`
  coverage. Refitting the game (Phase 3) before Phase 1/2 would fit to sparse,
  wrong-signed data.
- **The BNE rework is the highest-risk change** (new dependency, core solver
  rewrite, artifact re-validation). It is gated behind convergence (#23) and a
  correct signal (#25), and every refit is validated against the walk-forward
  backtest before it ships. The CE-LP (#22) is *additive* — it can ship beside
  the current solver as the first, safe piece of the equilibrium rework.
- **Every phase ships.** No phase leaves `main` in a broken or half-migrated
  state; the product stays usable throughout.
