# Product redesign — the Relationship, the Watchlist, and plain language

> Status: design of record for the front-of-house redesign. Companion to
> `docs/event-impact-spec.md` (the event→prices engine this surface renders).
> First principles, one north star, no jargon at the surface.

## Who this is for

**A senior investment decision-maker** — an MD at a fund, not a quant living
in the models. Geopolitically literate and markets-native, but time-poor and
uninterested in the machine's anatomy. Wants the *answer* and the *so-what*,
with the evidence one click away, in English. This persona sets every call
below: **answer first, evidence behind "show me why," zero graph/ML jargon on
the surface.**

## The one job

**"What does an event do to markets — and where is this relationship headed?"**
Everything on the surface serves that. The subsystems (the game, the reasoning
engine, the transmission study, the forecaster) are *how*, never *what*; they
appear as evidence inside the answer, never as top-level destinations.

## The object model (this is the whole redesign)

| Object | Plain name | What it is | Persistence |
|---|---|---|---|
| Dyad | **Relationship** | An ordered pair of actors (US ⇄ Russia) — the thing a user follows | — |
| Event | **Event** | A dated action in a relationship, with a measured market move | graph/corpus |
| Forecast | **Outlook** | Where the relationship is headed + predicted market impact | frozen nodes |
| — | **Watchlist** | The relationships this user follows | localStorage (v1) |

**The Relationship is the unit of the product.** A user follows relationships
the way they follow tickers: save US ⇄ Russia, come back, see where it's been,
where it's going, and what it does to markets.

## Information architecture

```
Home = Explorer            (browse the whole 120-year web — unchanged; it works)
  └─ select a relationship ─────────────┐
Watchlist  = the followed relationships  │→  Relationship page  (the hero)
  └─ open one ───────────────────────────┘        past → now → forward
```

- **Explorer stays home.** It is a *place* and it works. Selecting an actor
  pair / relationship there opens its Relationship page; a ⭐ saves it.
- **Watchlist** is a new top-level destination: the user's followed
  relationships, each with a one-line current read and a "what's coming"
  teaser, linking into its Relationship page.
- **Reasoning, Games, Trading disappear as tabs.** They become evidence *inside*
  the Relationship page: Reasoning/Games → the "why we think this" drawer under
  the forward view; Trading → the "track record" section. No user ever again
  navigates to "the game."

Navigation, after: **Explorer · Watchlist · (Case studies).** That's it.

## The Relationship page — anatomy (the hero surface)

One vertical spine, top to bottom, answer-first:

1. **Header.** "United States ⇄ Russia." A one-sentence plain read of the
   current state ("Tension elevated and rising over the last two quarters").
   ⭐ Save/Unsave. No ids, no band numbers on the surface.

2. **Where it's been** — the event timeline (past). The relationship's notable
   events laid left→right up to **now**; each event shows *what markets did*
   (the measured market move, from Event Impact / precedent effects). Selecting
   an event expands it: what happened, which markets moved, by how much, who
   reacted first. This is the **explanation** beat.

3. **Now.** The current state in a sentence + the tension trend sparkline.

4. **Where it's going** — the Outlook (forward). The forecast horizon: the
   likely next moves and the **predicted market impact**, as a fan/range not a
   point. A **"why we think this"** drawer folds in the game read and the
   precedent evidence — in plain language ("markets have moved like this the
   last N times tension rose this fast in comparable periods"). This is the
   **prediction** beat, and it carries the boundary statement ("pressure over
   windows, not a dated call") without the word "retrodiction."

5. **Track record.** Have this system's calls held up? A plain accuracy read
   (hit rate, how it scored vs history) — the paper book / calibration,
   reframed as credibility, not a trading ledger.

The **past→now→forward timeline** (item 2→4) is one continuous control that
replaces the hard-to-track slider *for this view*: real events on the left, a
fixed **now** anchor, the forecast horizon on the right, so the user never
loses where the present is.

## The Watchlist

- **Save** a relationship from its page or from Explorer selection (⭐).
- **The list**: each saved relationship as a card — actors, a one-line current
  read (tension level + direction, plain), and a "what's coming" teaser from
  its Outlook — linking to the full Relationship page.
- **"Updates"**: the list itself is stored locally (v1: `localStorage`, since
  there are no accounts yet); each card fetches *fresh* state on open, and the
  Outlook re-freezes at boot, so returning to the list shows the current read,
  not a stale snapshot.
- **Forward-compatible**: the store is behind a small interface
  (`list()/add()/remove()/has()`) so a future backend user-store replaces
  localStorage without touching any component.

## The language system (jargon → human), applied once, everywhere

A single shared translation module (`web/src/lib/language.ts`), used by every
surface so terms never drift:

| Internal | Surface |
|---|---|
| dyad | relationship |
| escalation band / score | tension level |
| escalate / de-escalate | rising / easing tension |
| AFFECTED / abnormal return / CAR | market move (%) |
| Goldstein | how hostile the action was |
| retrodiction | how it scored against history |
| equilibrium / policy | what we expect |
| counterfactual | what-if |
| Brier score | accuracy |
| first_mover | reacted first |
| regime-comparable | comparable periods |
| provenance / source_id | where the number comes from |

Rule: **the machine keeps its exact names internally; the surface reads
English.** Expert detail (exact bands, source ids, method notes) lives behind
"show me why" / a details toggle, never on the first read.

## Where the data comes from (no new stores)

The Relationship page composes endpoints that already exist, plus the Event
Impact endpoint (`docs/event-impact-spec.md`):

- **Timeline / history** — `/api/panel/dyads` (series) + `/api/precedent`
  (episodes + measured market effects per market).
- **Per-event market move** — Event Impact `GET /api/impact/{event_id}`
  (measured), degrading to precedent effects where the study hasn't converged.
- **Outlook (forward)** — `/api/forecasts` (frozen near-term + long-horizon +
  model + sequence) and `/api/games/*` for the "why" drawer.
- **Track record** — `/api/trading/*` (paper book / walk-forward), relabeled.
- **Relationship list** — `/api/dyads` + `/api/panel/dyads`.

Degradation is honest everywhere: a missing measurement reads "no comparable
history yet," never "$0"; a 5xx reads "couldn't reach the archive," never
"empty." (Broken-vs-empty via `lastFailureFor`, the existing pattern.)

## Build sequence

Additive first (new surfaces beside the old), restructure last (retire old
tabs only once the new page is solid) — so nothing breaks mid-flight.

1. **Language layer** (`lib/language.ts`) + **Watchlist store**
   (`lib/watchlist.ts`, localStorage behind an interface). Pure, safe.
2. **Event Impact backend** (`core/reasoning/impact.py` + router + tests) —
   the engine for per-event and predicted market moves. Additive, unit-tested.
3. **Relationship page** (`pages/Relationship.tsx`) composing the endpoints
   above; the past→now→forward timeline; plain language throughout. New route,
   old tabs still present.
4. **Watchlist page** (`pages/Watchlist.tsx`) + ⭐ save controls.
5. **IA restructure**: nav becomes Explorer · Watchlist · Cases; Reasoning/
   Games/Trading fold into the Relationship page as drawers/sections; retire
   the standalone tabs.
6. **Perf/polish**: slider debounce+abort and 3D-identity (existing tasks),
   fetch dedup, the small correctness bugs.

Each step: verify (`npm run build` = tsc --noEmit && vite build; `pytest`/
ruff/mypy for backend), then push, then track the deploy. Nothing ships that
doesn't compile and pass.

## What "done" looks like

An MD lands on the app, searches or browses to **US ⇄ Russia**, sees at a
glance where the relationship has been (with the market moves each event
caused), where it's headed (with the predicted market impact and a plain "why"),
and how the system's past calls scored — then ⭐ saves it and finds it waiting,
current, next time. One object, one question, one language. The machinery is
all still there; the user just never has to meet it.
