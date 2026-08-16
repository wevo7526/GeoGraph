# Game families — which game a pair actually plays, and how the archive picks it

*2026-08-16. Written because the platform had ONE game and gave it to everyone.*

## The defect

`core/games/solve.py::stage_payoff` implements **Fearon (1995) crisis
bargaining**: a stake contested under threat of force, private resolve types
drawn from a prior, a cost of fighting that separates those types, and an
audience cost for backing down from an escalated position. It is a good model
of *war and the reasons wars happen*.

It is applied to every dyad the platform solves. On 2026-08-16 that produced,
for the United States and Japan — a treaty alliance since 1951 —

    escalation_probability: 0.7696
    modal course: probe_and_retreat — "one side presses, then steps back"

Washington and Tokyo contest no stake by threat of force and neither pays an
audience cost for conceding to the other. Every term in the payoff is
undefined for that pair, and the surface then narrated the output in the
vocabulary of brinkmanship. The number is not *wrong* in its own terms — it
faithfully reports departures from the pair's own baseline friction — but the
question it answers is not a question anyone asked, and the words attached to
it assert something false.

**Allies, rivals and adversaries do not play the same game.** The family has to
be classified first, and the game selected from the classification.

## The constraint every candidate has to meet

Build-spec §17: the deterministic core measures, and **nothing originates a
number**. A game family earns a place here only if

1. its **actions map onto coded quad classes**, so a solved path can be turned
   back into events the transmission layer can price; and
2. its **parameters are identifiable from the record** by the same indirect
   inference `scripts/fit_game.py` already runs — simulate, compare the
   simulated action mix and transition kernel to the observed ones, fit.

A game that fails either test is a nice idea that would put invented numbers
on the surface. Two of the six below fail the second test today and are marked
as such rather than quietly built.

The archive offers: quad classes (material/verbal × cooperation/conflict),
Goldstein scores, per-dyad escalation magnitude against the pair's own EWMA
baseline, CINC capability, the curated RELATES_TO web, and measured market
effects.

## The families

### 1. Crisis bargaining — ADVERSARY  *(implemented)*

| | |
|---|---|
| **Question** | does the contest become coercive, how far, and who backs down |
| **Actions** | de-escalate / hold / escalate → verbal_coop, verbal_conflict, material_conflict |
| **Parameters** | stake, cost of fighting by type, audience cost, discount |
| **Bad end** | open conflict |
| **Identified by** | the observed escalate/hold/de-escalate mix and the transition kernel — this is what `fit_game.py` fits today |
| **Pairs** | US–Iran, North Korea–South Korea, Russia–Ukraine, Israel–Hezbollah |

This is the game that exists. It is correct here and nowhere else.

### 2. Repeated competition with reputation — RIVAL  *(not implemented)*

| | |
|---|---|
| **Question** | does a standing competition harden toward the use of force |
| **Actions** | ease / hold / press — the same quad classes, read for approach to a threshold rather than for how a crisis ends |
| **Parameters** | temptation to defect, cost of mutual defection, discount, **misperception noise** |
| **Bad end** | a coercive turn — crossing into family 1 |
| **Identified by** | the cooperate/defect mix per quarter and the persistence of retaliation; noise from the rate of defection following cooperation |
| **Pairs** | US–China, US–Russia, China–India, UK–Russia |

A rivalry conducted in argument is a repeated game with noisy monitoring, not
a crisis. The distinction matters for the output: the interesting quantity is
**P(this pair crosses into coercion)**, not "who wins the crisis". Today
US–China is handed a crisis game and reports the odds of winning a stake
neither is contesting by force.

### 3. Alliance burden-sharing — ALLY  *(not implemented — the US–Japan gap)*

| | |
|---|---|
| **Question** | does the alliance function or fray; who carries it and who free-rides |
| **Actions** | commit / affirm / withhold → material_coop, verbal_coop, verbal_conflict |
| **Parameters** | value of the shared good, private cost of contributing, **exposure asymmetry** |
| **Bad end** | a rift — abandonment or entrapment, never war between the partners |
| **Identified by** | the material-vs-verbal cooperation mix, and the asymmetry of who supplies which |
| **Pairs** | US–Japan, US–Australia, US–South Korea, NATO members, US–Israel |

The canonical model is **Olson & Zeckhauser (1966)**: allies choose
contributions to a shared defence good, and the large ally over-provides while
the small one free-rides. Its central parameter — exposure asymmetry — is
already in the graph as the CINC ratio `opening.capability_state` reads. Its
actions are already coded. **It is identifiable, and it is the single highest-
value family to build**, because most of the pairs the platform ranks are
alliances and every one of them is currently being scored as a potential war.

### 4. Patron–client delegation — PROXY  *(not implemented)*

| | |
|---|---|
| **Question** | does the client drag the patron in, or the patron restrain the client |
| **Actions** | *directed*: patron supports / restrains; client complies / acts alone |
| **Parameters** | alignment, cost of support, client's private benefit from acting |
| **Bad end** | entrapment — the patron pulled into the client's confrontation |
| **Identified by** | the lag structure between patron statements and client actions |
| **Pairs** | Iran–Hezbollah, Iran–Hamas, Iran–Ansar Allah |

`proxy` is the ontology's one **directed** relation type, and the asymmetry is
the model. This is a moral-hazard problem, not a bargaining one. Identification
is plausible but unproven — the lag structure is thin for the smaller clients.

### 5. Terms bargaining — TRADE  *(identification unproven)*

| | |
|---|---|
| **Question** | do the terms settle or rupture |
| **Actions** | concede / hold / demand |
| **Parameters** | patience, outside option, breakdown risk |
| **Bad end** | rupture of the arrangement |
| **Pairs** | Germany–Russia (the pack's declared `trade` edge) |

Rubinstein alternating offers. The obstacle is that GDELT's quad classes do
not distinguish a trade demand from a diplomatic one, so the actions are not
cleanly separable from family 2's. **Not buildable without a finer coding of
economic events** — stated here so it is not attempted on the current data.

### 6. Bloc assurance — MEMBERSHIP  *(identification unproven)*

| | |
|---|---|
| **Question** | does a member align with the bloc or hedge |
| **Actions** | align / hedge |
| **Bad end** | defection from the bloc |

A stag hunt. The problem is that it is not really a dyadic game — it is a
member against a bloc — and the whole solver is dyadic. Listed for
completeness; it would need a different state space, not a different payoff.

## The classification

Implemented in `core/games/family.py`. It uses **both** layers the platform
already keeps apart, because neither is sufficient alone:

* **what the pair IS** — `opening.standing`, the curated RELATES_TO web, dated
  and sourced;
* **how its record READS** — `opening.posture`, the material-conflict share of
  its coded events, with the sample stated.

| declared | coercive share | family |
|---|---|---|
| rivalry | ≥ 25% | adversary |
| rivalry | < 10% | rival |
| rivalry | between | rival |
| alliance / membership | ≥ 25% | **adversary** — behaviour outweighs the declaration |
| alliance / membership | otherwise | ally |
| none | ≥ 25% | adversary |
| none | otherwise, or too thin | rival |

The cuts are not new numbers: 25% is `opening.POSTURE_EDGES`' "mixed record"
edge, the archive's own upper decile, and 10% is its "mostly talk" edge.

Two properties worth stating:

* **Standing alone is not enough.** It would hand US–China the same game as
  North Korea–South Korea, whose record is seven times more coercive.
* **Posture alone is not enough.** Two allies co-deployed in someone else's
  war accumulate material-conflict events *against each other* in GDELT's
  coding — the co-participation artefact the ranking already has to warn
  about — and would be classed adversaries on behaviour.
* **Absence of evidence makes the weakest claim.** An unclassifiable pair is a
  `rival`, never an `adversary`: calling two states adversaries is a
  statement, and silence must not make it.

## What is true today, and the order of work

Only family 1 exists. Every pair is still solved with it. What changed on
2026-08-16 is that the platform now **says so**: each solved dyad carries its
family, the question that family's game is entitled to ask, and — when the
solved game is not that family's own — a sentence saying the numbers describe
departures from the pair's own usual friction and are **not** odds of conflict.
That is honesty, not a fix.

The fix, in order of value:

1. **Family 3, alliance burden-sharing.** Most of the ranked pairs are
   alliances; all of them are currently scored as potential wars. Identifiable
   on existing data, and its key parameter is already in the graph.
2. **Family 2, repeated competition.** Turns US–China from "who wins the
   crisis" into "does this cross into coercion", which is the question a reader
   of that pair actually has.
3. **Family 4, patron–client.** Small number of pairs, high interpretive value
   in mena, identification needs checking first.
4. Families 5 and 6 need data or a state space the platform does not have.
   They are written down so nobody builds them on the current archive and
   presents the result as measured.

Each family needs: an action set mapped to quad classes, a counted transition
kernel **over its own action space**, payoffs fitted offline by indirect
inference against that family's pairs only, and surface language that does not
borrow from war. The kernel and the fitting machinery are already generic over
the action set; `state.ACTIONS` being a module constant is the thing that
currently prevents more than one.
