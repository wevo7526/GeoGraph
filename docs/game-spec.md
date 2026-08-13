# Game spec — from a fitted decay to a sequenced forecast with a price

Status: **SCOPE, not locked.** `docs/build-spec.md` is the master spec and §4
already commits this repo to a rationalist bargaining frame — four latent
variables (position, salience, clout, resolve) held as distributions, the
market as a second sensor, and a learning loop powered only by realized
outcomes. This document does not choose that frame; it makes it *computable*
and says exactly where the fitted model plugs in.

The deliverable it is written toward, in the user's words: **a sequenced event
in the forecast with the associated predicted market movement.**

---

## 0. The one-paragraph version

The ridge model in `core/models/` measures how fast a dyad returns to its own
baseline. That decay is a *moment of observed play*, not a mechanism. We take
a finite-horizon stochastic bargaining game with private resolve, solve it for
Markov Perfect Bayesian Equilibrium by backward induction, and choose its
structural parameters so that **simulating the equilibrium and re-fitting the
same ridge reproduces the decay we measured**. That is indirect inference: the
learned model is the auxiliary statistic, the game is the structure. Once
fitted, the equilibrium policy is rolled forward to produce *distributions
over event sequences*, and each step of each sequence is priced by the
measured `AFFECTED` distribution for comparable events. Markets feed back in
as a second observation of resolve — equity for severity, the term structure
for expected duration — but only from realized outcomes, never from the
system's own predictions.

---

## 1. What kind of game, and why not the others

**Chosen: a finite-horizon stochastic (Markov) bargaining game with two-sided
incomplete information over resolve.**

Per dyad, per quarter. Both sides move simultaneously each period.

| Alternative | Why rejected |
|---|---|
| One-shot 2×2 escalate/hold | Produces a probability, not a **sequence**. The deliverable is a path; a one-shot game cannot emit one. |
| Infinitely repeated game with discounting | **The folk theorem kills it.** With patient players almost any path is supportable as an equilibrium, so the model predicts everything and therefore nothing. This is the single strongest reason the horizon must be finite. |
| Complete-information stochastic game | Then war is never rational — Fearon's puzzle. Escalation has to be explicable, and private resolve is the mechanism §4 already commits to. |
| Deep RL / learned policy | 28k dyad-quarters. It would fit the data and answer no counterfactual. |

**Finite horizon is load-bearing, not a convenience.** It (a) defeats the folk
theorem's multiplicity, (b) makes backward induction exact rather than
iterative, and (c) matches the forecast horizon we already ship — H = 4
quarters, extensible to 8.

### 1.1 The game, precisely

For dyad *d* at quarter *t*:

- **State** `s = (x, κ, μ)`
  - `x` — escalation intensity **relative to the dyad's own EWMA baseline**,
    discretized to ~6 levels. This is exactly the panel's `intensity`, which
    the classifier already computes relationally (a −6.0 is routine for a
    rivalry, a rupture for an alliance).
  - `κ` — capability ratio from CINC (`AttributeEstimate.clout`), discretized
    to ~3 levels.
  - `μ` — the **belief pair**: each side's posterior over the other's resolve
    type, discretized to ~5 levels each.
- **Types** `θ ∈ {resolute, irresolute}`, private, drawn once per episode
  from a prior tied to `AttributeEstimate.resolve`.
- **Actions** `a ∈ {escalate, hold, de-escalate}` — these are not invented,
  they are the `QuadClass` partition we already code every event into.
- **Payoffs** `u = (stake × p_win(κ, a)) − (cost(θ) × intensity) − audience(a)`
  - `stake` from issue salience (`AttributeEstimate.salience`, ICOW-seeded)
  - `cost` is where **markets enter as structure** — see §3
  - `audience` from alliance/IGO embedding (backing down in front of allies is
    costly; the `RELATES_TO` alliance tier is the input)
- **Transition** `P(x' | x, a₁, a₂)` — estimated, not assumed. See §2.

### 1.2 The equilibrium the system works toward

**Markov Perfect Bayesian Equilibrium (MPBE).** Strategies depend on the
payoff-relevant state only; beliefs update by Bayes on observed actions;
sequential rationality at every node.

Two things are worth separating, because "the equilibrium the system works
toward" can mean either and they behave differently:

1. **Within a forecast** — MPBE is *solved*, not converged to. Backward
   induction from `t = H` gives a policy `σ(s) → Δ(actions)` in one pass. It
   is the prediction, not an attractor.
2. **Across time** — the *belief* half genuinely converges. Each realized
   outcome updates the posterior over resolve, and under Bayes those
   posteriors are a martingale: they settle as evidence accumulates. **That is
   the flywheel's convergence, and it is the only thing in this system that
   "works toward" anything.**

Refinement: where backward induction admits multiple equilibria we take the
**belief-monotone** one (higher posterior on resolute ⇒ weakly more
escalation) and record that we did. An unrefined multiplicity is reported, not
silently resolved.

---

## 2. How the fitted model feeds the game

This is the crux, and the answer is a named technique rather than a vibe.

### 2.1 The ridge is the auxiliary model in indirect inference

We can *simulate* the game easily and cannot write its likelihood. That is the
textbook setting for **indirect inference** (Gouriéroux–Monfort–Renault):

1. Fit the auxiliary model — our ridge — to the REAL panel. It returns
   `β̂ = (β₁, β₂, β₃, β₄)`, the per-horizon decay on `level_now`. Measured:
   **1.250 → 1.228 → 1.199 → 1.176**, plus the residual spreads.
2. For a candidate structural parameter vector `θ = (δ, cost_resolute,
   cost_irresolute, escalation efficacy, prior on resolute)`:
   - solve the MPBE,
   - simulate N synthetic dyad-histories under it,
   - **re-fit the same ridge, by the same code path**, to the simulated panel,
     giving `β̃(θ)`.
3. Choose `θ̂ = argmin (β̂ − β̃(θ))′ W (β̂ − β̃(θ))`.

The decay is exactly the right binding moment: in a bargaining game, how fast
intensity reverts to baseline **is** a function of the discount factor and the
cost asymmetry. We are reading a structural parameter off a reduced-form one.

The same code path in step 2 is not fastidiousness — an auxiliary model fitted
differently to simulated data measures the difference between two estimators
rather than between two worlds.

### 2.2 Identification, stated honestly

Patience `δ` and cost `c` both flatten the decay. **They are not separately
identified from the decay alone.** Two additional moments break the tie:

- the **frequency of de-escalation from high intensity** (patience shows up as
  willingness to absorb cost now for position later), and
- the **market-implied duration** from the term structure (§3.2), which prices
  expected persistence directly.

If those still fail to separate them, we fix `δ` at a regime-typical value and
estimate `c` only — and say so in the artifact rather than reporting a number
the data cannot support.

### 2.3 The transition kernel

`P(x' | x, a₁, a₂)` is estimated from the panel by counting transitions
between discretized intensity levels, conditioned on the coded quad class of
the events in the origin quarter, with **Laplace smoothing and a per-cell
sample floor**. Cells below the floor fall back to the pooled kernel and are
flagged — the same drop-and-count discipline the rest of the archive uses.

---

## 3. Markets: two roles, one direction each

**These must not be confused, and §17 is why.** Markets are an *input* to
belief and an *output* of the forecast, and no code path connects the output
back to the input.

### 3.1 Equity — severity

Regional indices (`^TASI.SR`, `DFMGI.AE`) and the global risk asset (`^GSPC`)
price the *magnitude* of a shock. The abnormal return on an event is already
measured by the transmission engine; `sensor_loop` already reads the residual
as surprise and revises resolve. This half exists.

### 3.2 Bonds — duration, and this is the new and valuable half

Equity tells you how bad. **The term structure tells you how long.** A crisis
the market expects to be short moves the front end and leaves the long end;
one expected to persist moves the long end and steepens or inverts.

That maps directly onto the object we are trying to forecast. Sequencing *is*
a duration question, so a market instrument that prices duration is the single
most informative sensor available for it:

- **Front-end move** (short rates, ≤2y) → expected near-term disruption
- **Long-end move** (10y, `DGS10`) → expected persistence / structural repricing
- **Slope change** → the market's implied hazard profile over the horizon

Concretely, the ratio of long-end to front-end abnormal move becomes a
**market-implied duration statistic**, and it enters §2.2 as the moment that
separates patience from cost. A dyad whose crises repeatedly move the long end
is one the market believes cannot de-escalate quickly — which is a statement
about `δ` and `c` that the event record alone does not make.

*Prerequisite:* the current MENA pack carries `DGS10` only. Front-end and
regional sovereign spreads must be added to the pack's `markets.yaml` before
this is more than an outline.

### 3.3 Markets as output

Predicted sequence → per-step market distribution, by **lookup over measured
`AFFECTED` edges** for comparable events (same quad class, comparable
magnitude band, regime-gated by `regimes.comparable`). Report median, IQR and
`n`. Thin samples say so. **No second neural net, and no model-originated
price** — §17 holds: the game predicts events, the measured transmission layer
prices them.

### 3.4 The flywheel, and the guard on it

```
realized event ──> measured market response ──> belief update on resolve
      ^                                                    │
      │                                                    v
 realized outcome <── (the world) <── event sequence <── re-solved equilibrium
```

**The loop closes only through the world.** `sensor_loop` reads `AFFECTED`
edges the transmission engine measured, and there is no code path from a
`Forecast` into it. A flywheel is exactly the structure where self-confirmation
creeps in, so this is enforced structurally rather than by intention — and any
refactor that adds such a path is a defect, not a feature.

---

## 4. The architecture we settle on

| Layer | Form | Why not something bigger |
|---|---|---|
| Transition kernel | Counted, smoothed, sample-floored | 28k rows; a learned kernel would memorize |
| Auxiliary model | The shipped ridge, unchanged | It is already gated and calibrated |
| Structural estimation | Indirect inference, ~5 parameters | Simulable, not likelihood-able |
| Solver | Backward induction over discretized state | Exact at finite horizon; no convergence risk |
| Beliefs | Bayesian posteriors on 2 types, as `AttributeEstimate` nodes | The ontology already holds these |
| Market map | Measured `AFFECTED` conditional distributions | §17 — never a model-originated price |

**State-space budget.** `6 (x) × 3 (κ) × 5×5 (μ) = 450` states × 9 joint
actions × 4 type pairs × 4 periods. That is small enough to solve exactly in
seconds per dyad, which is what makes indirect inference (thousands of solves)
feasible at all. **This is the reason for every discretization above** — the
coarseness is a budget decision in service of exact solution, and should be
stated wherever the output is shown.

Explicitly **not** a deep sequence model. The value here is counterfactual
capability — "what if the US signals commitment", "what if the capability
ratio closes" — which a fitted policy answers and a black box cannot. And the
data does not support the alternative.

---

## 5. The artifact

The deliverable object. One per dyad per freeze, a new `Forecast` mode
(`mode='sequence'`) rather than an overwrite of any counted forecast:

```jsonc
{
  "dyad_id": "dyad:cow-630--cow-645",
  "as_of": "2026-06-30",
  "equilibrium": {
    "concept": "MPBE, finite horizon H=4, belief-monotone refinement",
    "theta": { "delta": 0.88, "cost_resolute": 0.4, "cost_irresolute": 1.9,
               "prior_resolute": {"a": 0.31, "b": 0.44} },
    "identified": ["cost"], "fixed": ["delta"],   // §2.2, stated not hidden
    "multiplicity": "unique under refinement"
  },
  "paths": [                       // the SEQUENCE — a distribution, not one line
    { "probability": 0.34,
      "steps": [
        {"q": "+1", "action": "escalate",    "quad": "material_conflict",
         "intensity": 11.2, "band": [7.1, 15.0]},
        {"q": "+2", "action": "hold",        "intensity": 9.4, "band": [5.0, 13.1]},
        {"q": "+3", "action": "de-escalate", "intensity": 5.1, "band": [1.2, 9.0]},
        {"q": "+4", "action": "hold",        "intensity": 4.4, "band": [0.9, 8.2]}
      ],
      "market": [                  // per STEP, from measured effects
        {"ticker": "BZ=F",   "median": 0.031, "iqr": [0.009, 0.058], "n": 41},
        {"ticker": "^TASI.SR","median": -0.018,"iqr": [-0.041, 0.004],"n": 27},
        {"ticker": "DGS10",  "median": -0.012, "iqr": [-0.03, 0.001], "n": 44,
         "note": "long-end move; front end absent from this pack"}
      ]
    }
  ],
  "calibration": { "auxiliary_fit": 0.94, "within_dyad": 0.4236, "brier": null },
  "boundary_statement": "…"
}
```

Three properties the shape enforces:

- **A distribution over paths, never one path.** A single sequence presented as
  the forecast would be the most misleading object this repo could produce.
- **Every price is a measured distribution with an `n`.** Not a point, not a
  model output.
- **The equilibrium's own weaknesses travel with it** — what was identified,
  what was fixed, whether the refinement was needed.

---

## 6. Phasing, with gates

| Phase | Work | Gate |
|---|---|---|
| **G0** | Add front-end and sovereign-spread instruments to the packs | **DONE.** Every pack now carries 3M/2Y/10Y, so there is a curve rather than one long bond |
| **G1** | Transition kernel from the panel, counted and floored | Kernel reproduces the panel's observed transition frequencies out of sample |
| **G2** | Solver: backward induction, MPBE, fixed θ | Solves in < 1s per dyad; equilibria stable under discretization refinement |
| **G3** | Indirect inference for θ | Simulated ridge coefficients land within the real ones' standard errors |
| **G4** | Market-implied duration from the term structure | Adds identification (§2.2) or is dropped and said so |
| **G5** | Path artifact + surface | Paths render as a distribution; every price carries `n` |

**`AFFECTED` was never the blocker it was written up as.** This document
originally listed an empty `AFFECTED` table as G0's hard dependency. That was
true of a local development copy of the graph and false of the deployed one,
which holds **382,736 measured effects** — the boot has been running the
event study over the whole spine since Phase 1. The cost term has had its
input all along.

Worth recording as a working rule rather than an embarrassment: *a local
graph is a sample, not the archive.* Anything measured against it that reads
as "the system lacks X" should be checked against `/api/stats` before it is
written down as a constraint, because the expensive half of this repo — the
panel, the transmission engine, the volume — only exists where it is
deployed.

The live G0 dependency is narrower: the front-end and 2-year instruments
added above have to be INGESTED into the panel before a duration statistic
can be computed from them, which happens on the next price load.

The standing gate applies throughout: **an equilibrium feature ships only if
it improves within-dyad ordering or error.** Six of nine hand-built features
already failed that test. Nothing here is exempt from it, and a game that
fails it is a finding to report rather than a component to keep.

---

## 7. What would make this wrong

Written down in advance so it is not rationalized later.

- **The folk theorem creeping back.** If H is extended far enough that
  multiplicity returns, predictions become arbitrary. H stays finite and small.
- **Identification failure.** If neither de-escalation frequency nor
  market-implied duration separates δ from c, we report a partially identified
  model and fix one parameter — we do not report both as estimated.
- **The coverage artifact.** Everything here is built on the panel, which is
  built on an archive whose density grew ~50× between 2006 and 2019. The
  coverage floor and the per-year cap exist for this reason; if the decade
  profile does not flatten after the backfill, the transition kernel is
  measuring the corpus and the game inherits it.
- **Eight markets.** The cost term needs *both* sides' exposure. For dyads
  where one side has no traded instrument, the payoff is half-observed and the
  artifact must say so rather than imputing a zero.
