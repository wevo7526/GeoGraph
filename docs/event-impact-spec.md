# Event Impact — design spec (DRAFT, not yet implemented)

> Status: design only. No code. Sequenced AFTER the robustness Tier 1 work
> (study convergence), because this feature is only as rich as `AFFECTED`
> coverage. See "Dependencies" below.

## The point of the whole machine, in one sentence

**One event in, a table of market price movements out.** Head B scores the
event, the transmission engine measures what markets did, the precedent layer
turns those measurements into a base rate, and the ML + game decide *which
event comes next and how much weight each outcome carries*. Prices are always
**measured `AFFECTED`**; the learned layers only choose the weights. That
single rule is what keeps the provenance invariant intact (build-spec §17: the
AI never originates a number that lands in `AFFECTED`).

The user-facing story is two beats, same object:

1. **Explanation** — for an event that happened: *"markets did X; the base
   rate expected Y; the surprise was X−Y."*
2. **Prediction** — for an event specified (or the next one the game expects):
   *"if this escalation happens in this dyad, markets move Y (± interval),
   standing on N comparable precedents."*

---

## 1. The contract

A single `EventImpact` object serves both beats. `mode` says which beat the
caller asked for; the shape is identical so one UI panel renders either.

```jsonc
{
  "mode": "historical" | "hypothetical",
  "event": {
    "id": "event:gdelt-...",          // null for a purely hypothetical spec
    "date": "2019-09-14",
    "dyad": "IRN|SAU",                // escalation.dyad_id — sorted actor pair
    "actors": {"initiator": "actor:irn", "target": "actor:sau"},
    "regime": "regime:...",
    "escalation": {"direction": "escalate", "band": 4, "magnitude": 3.1,
                   "goldstein": -9.0},
    "fidelity_tier": "daily", "temporal_resolution": "day"
  },
  "markets": [
    {
      "ticker": "market:tadawul", "name": "Tadawul All Share",
      "native_frequency": "daily",
      "existed_at_event": true,        // fidelity gradient; false => skipped
      "skip_reason": null,             // e.g. "founded 2007-02-26, after event"

      "measured": {                    // present iff historical AND measured
        "car": -0.041, "window": "0..5", "resolution": "day",
        "first_mover": true, "source_id": "source:..."
      },

      "expected": {                    // the prediction, from precedent base rates
        "mean_car": -0.028, "lo": -0.061, "hi": 0.004,
        "n_precedents": 23, "window": "0..5", "first_mover": true,
        "basis": "regime-gated precedents at band>=4 in IRN|SAU (+comparable dyads)"
      },                               // null (not zero) when no admissible precedents

      "surprise": -0.013               // measured - expected, when both present
    }
  ],
  "precedents": [                      // the evidence behind every `expected`
    {"event_id": "event:...", "date": "2016-01-02", "dyad": "IRN|SAU",
     "band": 5, "weight": 0.9, "why_comparable": "same dyad, regime-comparable"}
  ],
  "forward": {                         // optional ML+game layer: "what comes next"
    "next_band_distribution": {"1": 0.4, "2": 0.3, "3": 0.2, "4": 0.1},
    "priced": [{"ticker": "market:brent", "expected_car": 0.012,
                "lo": -0.005, "hi": 0.031}],
    "converged": false,                // disclosed, never hidden (mena/china fits)
    "artifact": "intensity@a1b2c3 / game-mena@d4e5f6"
  },
  "boundary_statement": "pressure over windows, never a dated prediction",
  "evidence_span": {"from": "1988-01-01", "to": "2026-08-11"},
  "computed_at": "2026-08-14T22:00:00Z"
}
```

Field rules that carry the honesty:

- **`measured` is the actual, `expected` is the base rate, and they are never
  merged.** The explanation beat shows both and the `surprise`; the prediction
  beat shows `expected` (+ `forward`) alone.
- **`expected` is `null`, not `0`, when there are no admissible precedents.**
  Zero is a measurement; absence is not. (This is the "$0 book" lesson applied
  to prices.)
- **`existed_at_event: false` markets are skipped with a `skip_reason`**, never
  priced against — the fidelity gradient (Gulf markets before founding).
- **Every number in `expected`/`forward` traces to `precedents` or a named
  `artifact`.** Nothing is originated by the model.

---

## 2. Two entry points

Both return the same `EventImpact`. A new thin router `core/api/routers/impact.py`,
mounted under `/api`.

| Endpoint | Beat | Input | Produces |
|---|---|---|---|
| `GET /api/impact/{event_id}` | explanation | an existing Event id | `mode:historical` — `measured` + `expected` + `surprise`, plus `forward` for the dyad |
| `POST /api/impact` | prediction | `{dyad or actors, band, direction, date, region}` | `mode:hypothetical` — `expected` (+ `forward`), no `measured` |

`GET` is event-centric (it's *this* event); the Explorer's event-selection
panel calls it. `POST` prices a specified/hypothetical event; the Reasoning /
Games "what-if" surface calls it. No new store, no new `AFFECTED` writer.

---

## 3. The composition, function by function

`EventImpact` is a **pure composition over functions that already exist and
were confirmed WIRED in the audit.** New code is a thin orchestration module
plus one extracted helper — no new subsystem.

### New module: `core/reasoning/impact.py`

```
event_impact(conn, panel, *, event_id=None, spec=None,
             region, include_forward=True) -> EventImpact
```

It calls, in order:

**(1) Resolve the event → dyad, band, regime, date, fidelity.**
- Historical: read the Head B escalation slots already on the Event node
  (`escalation.*`), and `escalation.dyad_id` for the sorted pair.
- Hypothetical: score the `spec` through the SAME Head B path
  (`escalation.code_events` / `DyadTracker`) so a specified event is banded by
  the identical rule — no second code path.
- Reuse: `core/classifier/escalation.py`.

**(2) Measured effects (historical only).**
- `measured_effects_for(conn, event_id) -> {ticker: MeasuredEffect}`.
- This is **`precedent._effects_for` extracted into a shared helper** (see the
  refactor note below) — it reconstructs dyad membership from
  `INITIATED_BY`/`DIRECTED_AT` actor edges, the 2026-08-14 fix that reaches the
  278k `AFFECTED` beside the spine's 55 `OF_DYAD` edges.
- Reuse: `core/transmission/effects.py` (writer stays the sole writer; we add a
  read helper beside it).

**(3) Expected effects — the base-rate engine (both beats).**
- `expected_effects(conn, panel, dyad, band, regime, as_of) -> ({ticker: ExpectedDist}, precedents)`.
- Gather comparable precedents: candidate events at a similar band, filtered by
  `regimes.comparable(dyad, dyad')` — the admissibility GATE, not a similarity
  score. For a true forecast, restrict to `date < as_of`.
- For each precedent, read its measured `AFFECTED` via (2), then aggregate per
  market into `{mean_car, lo, hi, n}` (percentile interval, not a normal
  assumption — the archive is not Gaussian).
- Reuse: `reasoning/analogy.rank_candidates` (candidate ranking),
  `reasoning/regimes.comparable` (gate), and the aggregation shape already in
  `games/pricing.measured_effects` (which today maps action-courses→markets via
  `AFFECTED`; the per-band base rate is the same reduction with a different
  grouping key). Down-weight annual/monthly-resolution precedents (fidelity),
  never treat them as equal to daily CAR.

**(4) First-mover + window per market.**
- `first_mover_window(markets, date) -> {ticker: (window, first_mover)}`.
- Reuse: `core/transmission/calendar.py` (`calendar_for`) — the Abqaiq case
  (Tadawul reacts Sunday, US Monday) is real information, surfaced here.

**(5) Forward — the ML + game layer (optional, "what comes next").**
- `forward(conn, panel, dyad, region) -> Forward`.
- Solve the dyad's game with the frozen model tilt (the existing freeze path:
  `bridge.tilted_kernel` from `intensity.forecast_trajectory`), enumerate the
  next-step band distribution (`games/paths.enumerate_paths`), then **weight
  each band's `expected_effects` base rate by its probability** and combine.
  That is the whole elegant link: the game picks the band mixture, the base
  rates price each band, the weighted sum is the predicted next move.
- Reuse: `games/solve.solve`, `games/paths.enumerate_paths`, `games/bridge.py`,
  `games/pricing.price_paths`. Degrades to `null` when no game artifact / gate
  failed / dyad uncovered — exactly as `η=0` reproduces the untilted kernel.
- Carries `converged` (disclosed for the mena/china fits) and `artifact`
  `name@hash`, and the `boundary_statement`.

**(6) Assemble** into `EventImpact`, stamp `computed_at` and `evidence_span`
(when the precedent evidence is from — which is not when the archive ends).

### One refactor (prerequisite-free, can land first)

`precedent._effects_for` currently lives inside the router
(`core/api/routers/precedent.py`). Extract it to
`core/transmission/effects.py::effects_for_event(s)` so both the precedent
router and `impact.py` read effects through one tested helper. Pure move + test
— no behavior change, no dependency on Tier 1.

---

## 4. Dependencies (why this is sequenced after robustness Tier 1)

- **`expected` and `n_precedents` are only as rich as `AFFECTED` coverage.**
  The event study does not currently converge (per-event commits/merges cap it
  at ~300s/pack), so `AFFECTED` is partial and precedent distributions are
  thin. **Tier 1 #2 (batch the study's commits/merges so it converges) is the
  substrate for this feature**, not merely an optimization. Until it lands,
  many events return `expected: null, n:0` — honest, but empty where the
  feature should be richest.
- **Read speed (Tier 2):** the `expected` engine reads many precedents' effects
  per call. The shared-Connection concurrency fix and the aggregate-in-Cypher
  fixes keep the panel responsive under load. Not blocking, but pairs well.
- **The `(event, market)` watermark (Tier 1 #3)** matters here directly: today
  pack-shadowing leaves some markets unmeasured for shadowed events, so their
  `expected`/`measured` would be silently missing. Fixing the watermark is what
  makes the per-market table complete.

Degradation contract while coverage is partial: every missing cell is an
explicit `null` + reason (no precedents / not measured / didn't exist), never a
fabricated or zero price.

---

## 5. UI surface

One panel, driven by `GET /api/impact/{id}` on event selection in the Explorer
(and reused by a "price this" control on Reasoning/Games via `POST`). Layout,
top to bottom:

1. **Header** — event, dyad, band, date, regime, fidelity tier.
2. **Per-market table** — one row per market:
   - measured bar (if historical) beside the expected fan (`lo..hi`), on the
     validated diverging pair (`--accent` gain / `--alert` loss — they carry
     the sign of the number);
   - `first_mover` tag; `n_precedents`; `surprise` when both are present.
   - Reuse `web/src/Charts.tsx` band/fan/strip primitives; no new chart lib.
3. **"What comes next"** — the `forward` strip: next-band distribution +
   priced expectation, labelled with the boundary statement and `converged`.
4. **Evidence** — the `precedents` list (collapsible): which past events the
   base rate stands on, each with `why_comparable`.

State discipline (from the frontend audit): tri-state `undefined`(loading) /
`null`(failed-or-empty) / data; branch broken-vs-empty on `lastFailureFor` so a
503 reads "the API did not answer," never "no impact." A market with no
precedents reads "no comparable precedent yet," not "$0."

---

## 6. Edge cases & failure modes

| Case | Behavior |
|---|---|
| Event has no comparable precedents | `expected: null`, `n:0`, panel says "no comparable precedent yet" |
| Market didn't exist at event time | `existed_at_event:false` + `skip_reason`, not priced |
| Deep-tier event (annual/monthly) | precedents down-weighted; `resolution` surfaced; never equal to daily CAR |
| No game artifact / gate failed | `forward: null`; explanation + expected still render |
| Game fit not converged (mena/china) | `forward.converged:false`, disclosed inline |
| Graph closed (api-first boot tail) | 503 → banner "reaching the archive," not "no impact" |
| Hypothetical spec names a ghost actor / undeclared dyad | 4xx with the reason, like the seed's ghost-actor refusal |

---

## 7. Tests that pin it

- A historical event with known `AFFECTED` returns `measured.car` equal to the
  stored value (round-trip).
- `expected` aggregates ONLY regime-comparable precedents — a non-comparable
  dyad's event is excluded (assert it never enters `precedents`).
- A market founded after the event → `existed_at_event:false`, skipped, reason.
- No precedents → `expected:null`, `n:0` (not `0.0`).
- `forward.next_band_distribution` sums to 1; `converged` surfaced; `η=0` path
  (no model) reproduces the untilted base-rate weighting.
- Provenance: no number in `expected`/`forward` exists without a matching
  `precedents` entry or a named `artifact` (the invariant, asserted).
- Hypothetical and historical beats share the object shape (one serializer).

---

## 8. Build sequence (when approved)

1. **Refactor** `precedent._effects_for` → `effects.effects_for_event(s)`
   (no behavior change; unblocks nothing, but is the shared read).
2. **Tier 1 robustness** — study convergence + watermark, so `AFFECTED` is
   complete and fresh. (Substrate.)
3. **`core/reasoning/impact.py`** — the composition (§3), fully deterministic.
4. **`core/api/routers/impact.py`** — the two endpoints (§2).
5. **Explorer impact panel** (§5), reusing `Charts`.
6. **Tier 2 read speedups** as the panel takes traffic.

Every step is gated on explicit approval before any push to `main`.
