# OOS spec — generalizing to events the archive has never seen

**Status: SCOPE. Nothing here is built.** This document fixes the questions
and the acceptance bars BEFORE any code, because both of the estimators this
repo has already had to repair were wrecked by measuring the wrong thing
convincingly (CLAUDE.md, "uneven density"; docs/ml-spec.md, "the gate is
within-dyad"). The pattern in both cases was a number that looked good and
answered a question nobody asked. The defence is to write down what would
count as an answer first.

## The deliverable this is validating

A **new major event** — one that is not in the archive, that happened this
week — enters the system as prose, and comes out the far side as a sequenced
forecast with a market implication attached:

```
prose  →  CAMEO code  →  Goldstein  →  escalation vs that dyad's
          (LLM, closed   (crosswalk,     EWMA baseline  →  intensity band
           vocabulary)    deterministic)  →  game state  →  sequenced paths
                                          →  priced from measured AFFECTED
```

Everything after the first arrow already exists and is deterministic. The
first arrow is the only new machinery, and it is deliberately narrow: **the
model chooses a CODE from a closed vocabulary, and never a number.** Goldstein
follows from the code through `core/ontology/crosswalks/`, exactly as it does
for a deep-tier record. The model can be wrong about "this is CAMEO 190", and
that is auditable and arguable by a human reading the same sentence. It cannot
be wrong about the value given the code. Build-spec §17 survives intact.

## Why two lines, and why both must pass

End-to-end accuracy is roughly the PRODUCT of two independent things:

- **Line A** — does the *coding* generalize to novel text?
- **Line B** — given a correct code, does the *forecast* generalize?

They have completely different evidence budgets, completely different failure
modes, and completely different fixes. A single end-to-end score tells you the
system missed without telling you which half missed, so it cannot be acted on.
**Report both halves separately AND the product.** Never only the product.

---

## LINE A — does the coding generalize?

### The reframing that drives the whole design

The question is **not** "is the code correct." It is:

> Does the code agree with the CONVENTION the archive's baselines are
> expressed in?

Magnitude is `|score − baseline|`, and every baseline in the graph is an EWMA
over Goldstein values derived from GDELT's own machine coding (TABARI /
PETRARCH). A coder that is *genuinely better than TABARI* but systematically
differs from it produces magnitudes measured against the wrong zero. The new
event then reads as more or less escalatory than it is, for a reason that has
nothing to do with the event.

**Agreement with the archive beats accuracy against truth**, and those two
genuinely pull apart. This is the same class of decision as scoring within
dyad rather than pooled: the estimator has to be expressed in the units the
downstream consumer actually uses.

Corollary worth stating because it is counter-intuitive: if the coder is
measurably better than TABARI, the correct response is NOT to ship the better
coder against the existing baselines. It is to recode the archive, or to carry
an explicit offset. Mixing conventions silently is the failure.

### Three evidence tiers

No single source is both large and gold, so use three and say which is which.

| Tier | Source | Size | Establishes |
|---|---|---|---|
| **Convention** | Held-out GDELT events, code hidden, text fetched from `SOURCEURL` | large, **2013+ only** | agreement with the archive's coding convention |
| **Gold** | The curated spine — hand-coded by Head B over the crosswalk | hundreds | accuracy against careful human coding |
| **External** | COW MIDs (hand-coded hostility levels), already loaded | moderate | does a level-4 MID read as an escalation |

**The feasibility risk lives in the top row and must be measured before
planning around it.** GDELT's free raw files carry `SOURCEURL`, not article
text, and only from ~2013 — pre-2013 rows have no URL at all. 2013–2018 has
heavy link rot. The first task in Line A is therefore not modelling, it is
counting: *how many held-out events still have retrievable text?* If the
answer is small, the Convention tier shrinks to a sample and the Gold tier
carries more weight than planned.

This constraint does **not** bite for live use. A 2026 event has text.

### Metrics, chosen by what downstream consumes

`pricing.price_step` matches on `(quad_class, intensity_band)`. So:

- **Primary: joint (quad, band) agreement.** Not exact 300-way CAMEO. Exact
  code accuracy will be low and mostly will not matter — many codes collapse
  to the same quad and a similar Goldstein value.
- **Sign agreement** — escalation vs de-escalation. This is what the game's
  action space keys off and what the diverging colour pair encodes. Getting
  the sign wrong is categorically worse than getting the magnitude wrong.
- **Goldstein MAE, in score units** — because magnitude is a distance, and an
  error here propagates as a false departure from baseline.

### Two error sources, measured separately

**Actor resolution is a distinct failure and probably the harder half.**
GDELT's actor coding is its noisiest field. "The IRGC" resolving to the wrong
`Actor`, or to one outside its `state_from`/`state_to` window at date D,
breaks the `Dyad` lookup before coding accuracy is even reachable.

Report:
- actor-resolution accuracy on its own,
- code accuracy **conditional on correct actors**.

Pooling them produces one number that hides which half is broken — the same
mistake as pooling across dyads.

### Stratification

**By era and by pack, never pooled.** A coder strong on 1990s wire copy and
weak on 2020s sourcing reads as fine in aggregate. This is the archive's
defining hazard applied to a new estimator, and it has already distorted four
separate statistics in this repo.

### Acceptance bars (PROVISIONAL — recalibrate once the gold set is measured)

| Metric | Bar | Why this bar |
|---|---|---|
| Actor resolution (gold set) | ≥ 0.90 | below this nothing downstream is meaningful |
| Sign agreement | ≥ 0.90 | a sign error inverts the forecast |
| Quad agreement, given correct actors | ≥ 0.85 | quad is the load-bearing pricing key |
| Joint (quad, band) | ≥ 0.60 | bands are harder; `price_step` already falls back to quad-only |
| Goldstein MAE | ≤ 1.5 | ~7.5% of the −10..+10 range; small against band width |

These are stated so they can be argued with and moved ON EVIDENCE, the way
`passes_gate` records its own gate moving. A bar that is quietly relaxed after
seeing the result is not a bar.

---

## LINE B — does the forecast generalize?

### Test 0 — run this FIRST, it needs no new events, and it gates everything

> Are the conditional quantiles from `price_step` any narrower, or any more
> shifted, than the UNCONDITIONAL distribution of that market's abnormal
> returns?

If knowing the quad and band does not move the distribution, then the market
implication — **the main artifact** — is decoration. A median with a percent
sign next to it that would have been the same for any event.

This is falsifiable on the existing archive today, costs nothing, and should
gate the rest of the work rather than follow it.

**Bar:** conditional IQR at least 15% narrower than unconditional, OR
conditional median shifted at least 0.5 unconditional-σ. Reported per market
and per quad, never pooled across markets — a bond and an equity index have
different unconditional widths and averaging them is meaningless.

If Test 0 fails, the fix is upstream of everything in this document: the
matching in `pricing.py` is too coarse, or the effects are too noisy, and no
amount of better sequence prediction rescues it.

### Walk-forward design

Freeze the system at cut date T. Rebuild state from data ≤ T only. Feed the
events in `(T, T+h]` **through the new-event path, not the bulk loader** — so
the test exercises the code a live event will actually take, including actor
resolution and the dyad lookup. Score. Advance T.

### Four leak points, all specific to this system

1. **The EWMA baseline.** The graph holds every event; a baseline query at T
   that does not filter on `event_time <= T` pulls the future. This is the
   easy leak and it will pass silently while inflating every result.
2. **The shipped model artifact.** `models/` was trained on the whole archive.
   Honest walk-forward means retraining per cut date, or excluding the `model`
   forecast mode from the OOS claim outright. **Decide this deliberately.**
   The two counted forecasts do not depend on it existing, so exclusion is a
   legitimate choice — but it has to be stated, not discovered.
3. **Regime gating.** `regimes.comparable()` must be evaluated with only the
   regimes known at T.
4. **Cut-date selection.** Must satisfy the coverage floor **per dyad**, not
   globally. The post-backfill 2006–2026 span *should* now clear
   `_MIN_WINDOW_SAMPLE`; that must be VERIFIED per dyad. Assuming it is
   exactly how the 0.93 structural-pressure artifact happened.

### Scoring, and what has to be beaten

**Sequence** — Brier, scored **within dyad**. Baseline is PERSISTENCE, which
on this archive is the signal and not a floor (+0.4253 within dyad; nothing
beat it). So the bar is the existing gate, reused deliberately for
consistency: keep persistence's ordering (≥ 0.95×) and beat its error.

**Price** — **quantile calibration by PIT**, not RMSE. Across many events the
realized abnormal return should fall below the predicted p25 about 25% of the
time, and inside the p25–p75 band about 50% of the time. RMSE is the wrong
instrument for a quantile forecast: it rewards a confident point estimate over
an honest interval, which is the opposite of what this system claims to
produce.

**Bar:** p25–p75 empirical coverage within 50% ± 10pp, and the PIT histogram
not rejected against uniform. Baseline is the unconditional distribution from
Test 0.

### The fast loop, which is the reason live events are worth the trouble

For a real new event the realized abnormal return exists **in five trading
days**. So the pricing half is scoreable almost immediately — no waiting for
the geopolitics to resolve. That is the market-as-sensor loop of build-spec §4
operating on the new-event path, and it is the only part of this system that
gets graded fast enough to iterate on.

It updates from REALIZED outcomes only, never from the model's own
predictions, and updates are new `AttributeEstimate` nodes
(`method='sensor_update'`), never overwrites.

---

## Composition, and the failure mode where both lines "pass"

Report Line A, Line B, and the product. Then check the case that passes both
and is still worthless:

> The coder agrees with the archive. The sequence beats persistence. And the
> conditional price bands are exactly as wide as unconditional.

That is a well-calibrated event predictor wearing a market implication that
carries no information — and it would ship, because both headline numbers are
green. **This is why Test 0 comes first.**

## The refusal surface

Generalizing OOS means meeting inputs the archive cannot speak to. Each of
these is a REFUSAL, reported as such, never an imputation:

- actors that do not resolve to `Actor` nodes;
- an actor outside its COW state-system window at date D;
- a resolved pair with no `Dyad` — no baseline, therefore no escalation
  classification, therefore no state;
- a regime `comparable()` rejects for the analogy;
- a market that did not exist at event time (already handled — the
  transmission engine records the skip);
- a `(quad, band)` cell with too few measurements, which `price_step` already
  reports as `thin` rather than hiding.

The last two are precedent: this codebase already prefers a recorded absence
to a filled-in number. The new-event path inherits that, it does not
re-litigate it.

## Build order

1. **Test 0.** Existing archive, no new code paths, no API key. Decides
   whether the main artifact is real.
2. **The deterministic new-event path**, taking the CAMEO code as an INPUT.
   Fully testable today with no `ANTHROPIC_API_KEY`. This is the path Line B
   walks forward on.
3. **Line B walk-forward**, with the four leak points closed and the model
   artifact question decided.
4. **Feasibility count for Line A** — how many held-out events have
   retrievable text. Cheap, and it sizes the rest.
5. **The coder**, behind the deterministic path, plus Line A measurement.

Note that 1–3 need no LLM at all. The dependency on `ANTHROPIC_API_KEY` is
real but it is LAST, not first.

## Not in scope

- Improving GDELT's coding. If our coder beats TABARI, see the corollary in
  Line A — that is a recoding decision, not an OOS one.
- Intraday market response. No dependency on historical intraday, ever.
- Long-horizon scoring. Long-horizon output is retrodicted and carries
  `BOUNDARY_STATEMENT`; `calibration.score_forecast` refuses scenarios without
  likelihoods rather than mis-scoring them, and that stands.
