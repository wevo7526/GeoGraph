"""Which game a pair plays — the ontological layer under the solver.

THE DEFECT THIS EXISTS FOR. There was one game, and every pair got it.
`solve.stage_payoff` is a Fearon crisis-bargaining model: a stake contested
under threat of force, private resolve types, and an audience cost for backing
down from an escalated position. That is the right model for the United States
and Iran. Applied to the United States and Japan it produced, on 2026-08-16,
an `escalation_probability` of 0.77 and a modal course of "probe and retreat —
one side presses, then steps back", for a treaty alliance with no contested
stake and no audience cost for conceding. The machinery was answering a
question that does not exist for that pair, and the surface was narrating the
answer in the language of brinkmanship.

Allies, rivals and adversaries do not play the same game. What separates them
is available in the archive already, in two layers that the platform keeps
deliberately apart:

  * WHAT THE PAIR IS — `opening.standing`, the curated RELATES_TO web, dated
    and sourced. The only field entitled to characterise a relationship.
  * HOW ITS RECORD READS — `opening.posture`, the material-conflict share of
    its coded events, with the sample stated.

Neither alone is enough. Standing alone would call the United States and China
a rivalry and hand them the same game as North Korea and South Korea, whose
record is seven times more coercive. Posture alone would call two allies
co-deployed in someone else's war an adversary pair, which is exactly the
GDELT co-participation artefact the ranking already has to warn about. The
classification uses both, and says which evidence moved it.

WHAT THIS MODULE DOES. It names the family and the question that family's
game is entitled to ask, and it gives the surface a vocabulary that is not
borrowed from war. Each family has its own action set, mapped onto coded
quad classes so payoffs stay estimable from the record (build-spec §17),
and its own stage payoff (`solve.ally_stage_payoff`, `solve.rival_stage_payoff`,
Fearon for adversaries). `classify` returns `native: True` for all three:
a pair is solved in its own space, never quietly as a war game and labelled
after the fact.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The three families, in the order the classification tries them.
FAMILIES: tuple[str, ...] = ("ally", "adversary", "rival")

#: Coercive share at which a pair's record is doing more than compete. Not a
#: new number: it is `opening.POSTURE_EDGES`' "mixed record" cut, the archive's
#: own upper decile, already used to choose the word on the chip.
ADVERSARY_SHARE = 0.25

#: …and the share below which even a declared rivalry is competition rather
#: than confrontation. `POSTURE_EDGES`' "mostly talk" cut.
RIVAL_CEILING = 0.10

#: The ABSOLUTE count of coercive events over the posture window that makes a
#: pair adversarial whatever its share. A share is coercion over everything
#: coded, and around a real confrontation the wire codes far more statements
#: than strikes: US–Iran carried 1,213 material-conflict events in the four
#: quarters to 2026-08 and a share under a quarter, and read as "a declared
#: rivalry conducted in argument". Three hundred coercive events in a year is
#: not argument. Read against the ranking's own counts: Lebanon–Israel 777,
#: US–UK (the co-participation-heavy alliance) 145, US–Russia 128.
ADVERSARY_COUNT = 300


#: What each family's game is entitled to ask, and the words its answer may
#: use. The headline is the name the surface gives the solved probability —
#: calling an alliance's friction "escalation" is the specific thing that made
#: US-Japan read as a war.
SEMANTICS: dict[str, dict[str, str]] = {
    "adversary": {
        "question": (
            "whether this pair's contest becomes coercive, and how far"
        ),
        "headline": "escalation",
        "press": "escalate",
        "concede": "de-escalate",
        "bad_end": "open conflict",
        "note": (
            "a crisis-bargaining game: a stake contested under threat of "
            "force, with an audience cost for backing down"
        ),
    },
    "rival": {
        "question": (
            "whether a standing competition hardens into coercion, from a "
            "record that is currently argument rather than force"
        ),
        "headline": "hardening",
        "press": "press",
        "concede": "ease",
        "bad_end": "a coercive turn",
        "note": (
            "a repeated-competition game below the use of force: each side "
            "eases, holds or presses over a contested prize, and the cost to "
            "fear is pressing at high friction — the coercive turn — not "
            "backing down"
        ),
    },
    "ally": {
        "question": (
            "whether the alliance functions or frays — burden, assurance and "
            "the friction between partners"
        ),
        "headline": "friction",
        "press": "withhold",
        "concede": "commit",
        "bad_end": "a rift",
        "note": (
            "an alliance-management problem, NOT a crisis: a burden-sharing "
            "game (Olson-Zeckhauser) in which each partner chooses to commit, "
            "affirm or withhold, the bad end is a rift, and the numbers "
            "describe departures from this pair's own usual level of friction "
            "- never odds of conflict between the partners"
        ),
    },
}


#: The trained classifier's bar for reading a pair as an ADVERSARY. 0.5 is the
#: point where the model's precision and recall cross on the held-out decade;
#: moving it is a decision about which error you would rather make, not a
#: tuning knob.
HOSTILITY_BAR = float(os.getenv("GEOGRAPH_HOSTILITY_BAR", "0.5"))

#: AND THE BAR FOR OVERTURNING A DECLARED DEFENCE PACT, which is higher on
#: purpose. A treaty is a sourced, dated fact; a model score is an inference,
#: and the model's largest feature is VOLUME, so the busiest alliances drift
#: upward on attention alone. The United States and the United Kingdom score
#: 0.508 — over the ordinary bar by eight thousandths, and a coin-toss away
#: from having their alliance overruled by a rounding error. The archive does
#: hold allies whose behaviour outruns their declaration, and this still finds
#: them; it just asks them to be obvious.
ALLY_OVERRIDE_BAR = float(os.getenv("GEOGRAPH_ALLY_OVERRIDE_BAR", "0.85"))


def _pct_words(value: float | None) -> str:
    return f"{(value or 0.0):.0%} confidence"


def classify(
    standing: dict[str, Any] | None,
    posture: dict[str, Any] | None,
    *,
    hostility: float | None = None,
) -> dict[str, Any]:
    """Which family this pair belongs to, and the evidence that decided it.

    Returns the family, the reason in a reader's words, and whether the
    solver's own game is the right one for it (`native`). A pair the platform
    cannot classify is `rival` — the weakest of the three claims — rather than
    `adversary`, because calling two states adversaries is a strong statement
    and absence of evidence must never make it.

    `hostility` is `models.hostility`'s probability that this pair is in a
    militarised dispute — a TRAINED read, on COW's own dispute record, of the
    question the thresholds below were guessing at. Where it is present it
    decides adversary; the thresholds remain for a pair the model cannot see
    and for the case where no model ships. They deserved replacing: on the
    held-out decade the model scores 0.847 AUC and beats the raw coercion
    count — the strongest continuous baseline — by +0.0495, while the SHARE
    the thresholds keyed on carries a fitted weight of -0.01, which is no
    information at all. (The shipped rule's own 0.533 is a BINARY indicator's
    score, so it reads as "this operating point barely discriminates" rather
    than as a like-for-like AUC comparison.)
    """
    kinds = {
        str(r.get("relation_type"))
        for r in ((standing or {}).get("relations") or [])
    }
    share = (posture or {}).get("share")
    thin = bool((posture or {}).get("thin", share is None))
    coercive = float(share) if share is not None else None
    count = int((posture or {}).get("coercive") or 0)
    # Coercion above the count bar reads as the top share bar would.
    #
    # AND THE SENTENCE MUST NOT REPORT THE LIFT AS A MEASUREMENT. The lift is
    # deliberate — 1,213 coercive acts in a year is not argument whatever the
    # denominator — but it moves `coercive` to the BAR, and every branch below
    # then printed that bar as though the archive had measured it: US–Iran's
    # `why` read "a declared rivalry whose record is 25% coercive" beside a
    # posture of 17% over 6,961 events, two numbers for one fact on one page
    # (2026-08-17). When the count is what decided it, the count is what the
    # sentence says.
    by_count = count >= ADVERSARY_COUNT and (coercive is None or coercive < ADVERSARY_SHARE)
    if by_count:
        coercive = max(coercive or 0.0, ADVERSARY_SHARE)

    def _evidence() -> str:
        """The measured fact that carried the classification, in its own terms."""
        if by_count:
            return f"{count:,} coercive acts on its recent record"
        return f"a record {coercive:.0%} coercive" if coercive is not None else "no coercive record"

    # ANTAGONISM FIRST, matching `opening._STANDING_PRIORITY`: a pact between
    # rivals is evidence of the rivalry, not a replacement for it, so a pair
    # that is declared both is read as the rivalry it is.
    # THE TRAINED READ FIRST, where there is one. A declared rivalry whose
    # record the model reads as a militarised dispute is an adversary whatever
    # the share says — that is how the United States and Russia came out
    # "a declared rivalry conducted in argument" on a 9.5% coercive share, and
    # Russia and Ukraine the same in the third year of an invasion.
    hostile = hostility is not None and hostility >= HOSTILITY_BAR
    if "rivalry" in kinds:
        if hostile:
            family, why = "adversary", (
                f"a declared rivalry the dispute model reads as militarised "
                f"({_pct_words(hostility)})"
            )
        elif coercive is not None and coercive >= ADVERSARY_SHARE:
            family, why = "adversary", (
                f"a declared rivalry with {_evidence()}"
            )
        elif coercive is not None and coercive < RIVAL_CEILING:
            family, why = "rival", (
                f"a declared rivalry conducted in argument — {coercive:.0%} "
                "of its coded events are coercive"
            )
        else:
            family, why = "rival", "a declared rivalry"
    # ONLY A DEFENCE PACT MAKES A PAIR ALLIES. `membership` used to sit in
    # this set, which reads "both are in the same IGO" as "these two would
    # fight for each other" — the United Nations would make every pair on
    # earth allies. And until the COW loader learned to read its own
    # obligation columns, `alliance` itself covered non-aggression pacts and
    # ententes, which is how France–Russia (a 1992 entente) and Germany–Russia
    # (a 1990 non-aggression treaty) were solved as burden-sharing partners on
    # the eurasia board. A pledge not to attack is evidence about a
    # relationship; it is not evidence of alignment, and rivals sign them
    # precisely because they are rivals.
    elif "alliance" in kinds:
        # A declared ally whose record is genuinely violent is not an ally in
        # the sense that matters here, and the archive has such pairs — but a
        # treaty is overturned by evidence, not by a marginal score.
        if hostility is not None and hostility >= ALLY_OVERRIDE_BAR:
            family, why = "adversary", (
                f"declared allies the dispute model reads as militarised "
                f"({_pct_words(hostility)}) — the behaviour outweighs the declaration"
            )
        elif coercive is not None and coercive >= ADVERSARY_SHARE:
            family, why = "adversary", (
                f"declared allies with {_evidence()} — "
                "the behaviour outweighs the declaration"
            )
        else:
            family, why = "ally", "a declared alliance"
    elif hostile:
        family, why = "adversary", (
            f"no declared relation, and the dispute model reads this pair's "
            f"record as militarised ({_pct_words(hostility)})"
        )
    elif coercive is not None and coercive >= ADVERSARY_SHARE:
        family, why = "adversary", (
            f"no declared relation, and {_evidence()}"
        )
    elif thin:
        family, why = "rival", (
            "too little coverage to classify from behaviour, and no declared "
            "relation — read as competition, the weakest of the three claims"
        )
    else:
        family, why = "rival", "no declared relation and no coercive record"

    semantics = SEMANTICS[family]
    return {
        "family": family,
        "why": why,
        # The trained read that decided it, carried so the ranking can order
        # on the same number the sentence quotes. None when no model ships.
        "hostility": round(hostility, 4) if hostility is not None else None,
        # NATIVE means the solver plays THIS family's own game. All three
        # families do: Fearon for adversaries, Olson-Zeckhauser burden
        # sharing for allies, repeated competition for rivals. A pair is
        # never quietly solved as a war game and labelled after the fact.
        "native": family in ("adversary", "ally", "rival"),
        "question": semantics["question"],
        "headline": semantics["headline"],
        "bad_end": semantics["bad_end"],
        "note": semantics["note"],
    }


def article(word: str) -> str:
    """"a" or "an" for the word that follows.

    The families are DATA (`FAMILIES`), so an article hardcoded for one of them
    is wrong the moment a second vowel-initial family exists — which it already
    did: the special case named `ally` and the page read "this pair is read as
    a adversary pair". The rule is the word's own first letter.
    """
    return "an" if word[:1].lower() in "aeiou" else "a"


def describe(classification: dict[str, Any]) -> str:
    """One sentence naming the family, the evidence, and — when the solved
    game is not this family's own — what that costs the reading."""
    family = classification["family"]
    line = (
        f"This pair is read as {article(family)} {family} "
        f"pair ({classification['why']}), so the question worth asking of it "
        f"is {classification['question']}."
    )
    if family == "ally":
        line += (
            " It is solved as a burden-sharing game — commit, affirm or "
            "withhold — whose bad end is a rift, and the numbers below "
            "describe departures from this pair's OWN usual level of friction, "
            "never odds of conflict between the partners."
        )
    elif family == "rival":
        line += (
            " It is solved as a repeated-competition game — ease, hold or "
            "press over a contested prize — whose bad end is a coercive turn, "
            "and the numbers below describe how far the competition hardens "
            "against this pair's OWN usual level, not the odds of open conflict."
        )
    elif not classification.get("native"):
        line += (
            " The solver runs the adversarial crisis-bargaining game for this "
            "pair, so the numbers below describe departures from this pair's "
            f"OWN usual level of {classification['headline']} and are not odds "
            f"of {classification['bad_end']}."
        )
    return line


# ── the action spaces: what each family's game is played IN ─────────────────
#
# THE SECOND HALF, landed 2026-08-16 evening. A family is not only a name and a
# vocabulary: it is an action set with its own reading of the coded record, its
# own quad classes for pricing, its own type labels, and its own payoff — and
# `state.ACTIONS` being a module constant was the one thing that prevented a
# second one. Every space keeps the SAME SHAPE — three ordinal actions, index 0
# the most conciliatory ("concede"), 1 the middle ("hold"), 2 the one that
# strains the pair ("press") — so the counted kernel, the path walk, the fan,
# the pricing and the persisted payloads keep their arrays; what a family
# changes is what the indices MEAN, how they are read off events, which quad
# class a step is priced against, and (in `solve.stage_payoff`) what the sides
# are trading off.


@dataclass(frozen=True)
class ActionSpace:
    """One family's game, as the solver sees it.

    `actions` are ordered concede / hold / press. `quads` gives the quad class
    a step of each action is PRICED against (`paths.py` → `pricing.py`).
    `types` names the private types (index 1 is the type whose cost of playing
    its family's costly action is LOW — resolute / committed). `signal` is the
    action index that is EVIDENCE OF TYPE 1 for Bayes: an adversary's
    escalation says resolute; an ally's COMMITMENT says committed, so the
    likelihood order mirrors (see `solve.posterior`).
    """

    family: str
    actions: tuple[str, str, str]
    quads: dict[str, str]
    types: tuple[str, str]
    signal: int
    #: How a side's quad-class counts for a quarter read as an action.
    reader: str  # "coercion" | "contribution"

    def index(self, action: str) -> int:
        return self.actions.index(action)

    @property
    def press(self) -> int:
        return 2

    @property
    def concede(self) -> int:
        return 0


#: The adversary space — the game that has existed all along. Its actions ARE
#: the quad-class partition collapsed to escalation direction, its types are
#: Fearon's, and escalation is the signal of resolve.
ADVERSARY = ActionSpace(
    family="adversary",
    actions=("de-escalate", "hold", "escalate"),
    quads={
        "escalate": "material_conflict",
        "hold": "verbal_conflict",
        "de-escalate": "verbal_cooperation",
    },
    types=("irresolute", "resolute"),
    signal=2,
    reader="coercion",
)

#: The rival space — repeated competition below the use of force. The same
#: three actions and the same coercion reading as the adversary's (a rival
#: eases, holds or presses, read off the same quad classes), the same types
#: (a hardliner presses more readily), but ITS OWN PAYOFF
#: (`solve.rival_stage_payoff`): the prize is contested by pressing and the
#: cost to fear is pressing at high friction — the coercive turn — not backing
#: down. `native` since 2026-08-16.
RIVAL = ActionSpace(
    family="rival",
    actions=("ease", "hold", "press"),
    quads={
        "press": "material_conflict",
        "hold": "verbal_conflict",
        "ease": "verbal_cooperation",
    },
    types=("accommodating", "hardliner"),
    signal=2,
    reader="coercion",
)

#: The ally space — Olson & Zeckhauser (1966), alliance burden-sharing. The
#: sides choose how much of the shared good to carry: COMMIT (material
#: cooperation — aid, deployments, joint operations), AFFIRM (verbal
#: cooperation — the routine assurance an alliance runs on), WITHHOLD (verbal
#: conflict — public friction, refusal, criticism). The bad end is a rift,
#: never war between the partners. The committed type's private cost of
#: contributing is LOW; commitment is the evidence of it.
ALLY = ActionSpace(
    family="ally",
    actions=("commit", "affirm", "withhold"),
    quads={
        "withhold": "verbal_conflict",
        "affirm": "verbal_cooperation",
        "commit": "material_cooperation",
    },
    types=("reluctant", "committed"),
    signal=0,
    reader="contribution",
)

SPACES: dict[str, ActionSpace] = {
    "adversary": ADVERSARY,
    "rival": RIVAL,
    "ally": ALLY,
}


def space_for(family: str) -> ActionSpace:
    """The action space a family's game is played in. Unknown → adversary,
    which is the game that always existed and the strongest claim, so it is
    only ever reached by a caller that has not classified at all."""
    return SPACES.get(family, ADVERSARY)


#: The share of a side's initiated events that must be MATERIAL COOPERATION
#: for an ally's quarter to read as "commit", and the share of friction (verbal
#: conflict, or any material conflict by the adversary rule's own bar) that
#: reads as "withhold". MEASURED, not guessed (2026-08-16, 1,695 side-quarters
#: of six current US alliances — Japan, Korea, Australia, the UK, Israel,
#: Saudi Arabia — on the wire): the material-cooperation share of a partner's
#: quarter has median 0.067, upper quartile 0.112, top decile 0.182; the
#: friction share median 0.185, upper quartile 0.263, top decile 0.364. The
#: cuts sit at the upper quartiles, so "commit" and "withhold" are each the
#: top quarter of what an alliance does, and "affirm" is its middle half — a
#: first pass at 0.20 made commit a top-decile event and the fit pushed the
#: shared good's value to its floor to reproduce a contribution that almost
#: never happened.
CONTRIBUTION_SHARE = 0.11
CONTRIBUTION_COUNT = 5
FRICTION_SHARE = 0.25


def contribution_action(quad_counts: dict[str, int]) -> str:
    """The ally reading of a side's quarter — commit / affirm / withhold.

    Friction outranks contribution, mirroring the adversary rule where
    material conflict outranks talk: a partner that publicly refuses AND ships
    aid in the same quarter is read as withholding, because the refusal is the
    departure from an alliance's usual level and the aid is what it always
    does. Material conflict between allies (co-participation artefacts aside)
    is friction too. Silence — a side that initiated nothing — is "affirm":
    the alliance's resting state, and inventing withholding from silence would
    put a rift in the record nobody observed.
    """
    total = sum(int(v) for v in quad_counts.values())
    if total <= 0:
        return "affirm"
    verbal_conflict = int(quad_counts.get("verbal_conflict", 0))
    material_conflict = int(quad_counts.get("material_conflict", 0))
    friction = verbal_conflict + material_conflict
    if friction and (
        friction / total >= FRICTION_SHARE or material_conflict >= CONTRIBUTION_COUNT
    ):
        return "withhold"
    material_coop = int(quad_counts.get("material_cooperation", 0))
    if material_coop and (
        material_coop / total >= CONTRIBUTION_SHARE or material_coop >= CONTRIBUTION_COUNT
    ):
        return "commit"
    return "affirm"


#: How each family names the SHAPE of a course. The kind KEYS are shared across
#: families (they describe who pressed and who conceded, which is family-blind
#: by construction), so the persisted payloads and the sorting rule keep
#: working; the label and the sentence are the family's own words. An
#: adversary's "brinkmanship" is an ally's "withhold, then recommit".
KIND_WORDS: dict[str, dict[str, tuple[str, str]]] = {
    "adversary": {
        "mutual_escalation": ("mutual escalation", "both sides escalate"),
        "brinkmanship": ("brinkmanship", "both sides escalate, then at least one steps back"),
        "one_sided_pressure": ("one-sided pressure", "one side presses while the other holds"),
        "probe_and_retreat": ("probe and retreat", "one side presses, then steps back"),
        "step_down": ("step-down", "at least one side de-escalates and neither presses"),
        "drift_up": ("drift up", "both hold, yet the counted kernel drifts intensity up"),
        "drift_down": ("drift down", "both hold and intensity subsides"),
        "holding_pattern": ("holding pattern", "both sides hold; intensity stays where it is"),
    },
    "rival": {
        "mutual_escalation": ("mutual hardening", "both sides press"),
        "brinkmanship": ("press and ease", "both sides press, then at least one eases"),
        "one_sided_pressure": ("one-sided pressure", "one side presses while the other holds"),
        "probe_and_retreat": ("probe and ease", "one side presses, then eases"),
        "step_down": ("easing", "at least one side eases and neither presses"),
        "drift_up": ("drift up", "both hold, yet the counted kernel drifts intensity up"),
        "drift_down": ("drift down", "both hold and intensity subsides"),
        "holding_pattern": ("holding pattern", "both sides hold; intensity stays where it is"),
    },
    "ally": {
        "mutual_escalation": ("mutual withholding", "both partners withhold — the rift course"),
        "brinkmanship": (
            "withhold, then recommit", "both partners withhold, then at least one recommits"
        ),
        "one_sided_pressure": (
            "free-riding", "one partner withholds while the other carries the alliance"
        ),
        "probe_and_retreat": ("friction, then repair", "one partner withholds, then recommits"),
        "step_down": ("burden shared", "at least one partner commits and neither withholds"),
        "drift_up": ("drift up", "both affirm, yet the counted kernel drifts friction up"),
        "drift_down": ("drift down", "both affirm and friction subsides"),
        "holding_pattern": ("steady state", "both partners affirm; friction stays where it is"),
    },
}


def kind_words(family: str, kind: str) -> tuple[str, str]:
    """(label, sentence) for a course kind in a family's own words."""
    table = KIND_WORDS.get(family) or KIND_WORDS["adversary"]
    return table.get(kind, (kind.replace("_", " "), kind.replace("_", " ")))


# ── co-participation: allies coded against each other on third-country soil ─
#
# GDELT PAIRS CO-PARTICIPANTS AS ADVERSARIES. Checked directly on 2026-08-16:
# US–Australia's material-conflict record for the year to 2026-08 was 25
# events of CAMEO 190 ("use conventional military force: Australia → United
# States") and 13 of 193 ("fight with small arms") — co-involvement in
# third-party operations, coded as a dyadic event between the two allies. On
# the ranking that put US–Australia above North Korea–South Korea and US–UK
# above US–Russia. An alliance filter would erase genuine ally-vs-ally
# friction (base disputes at home, a public rift), so the rule needs the one
# fact that separates the two: WHERE it happened. Two partners under a
# declared alliance in force at the time, coded in material conflict with
# each other, on soil that is NEITHER partner's, are read as co-participants;
# the raw CAMEO code and quad class are kept as coded, and the readers that
# count coercion (`models.panel.build`, `transition.quad_counts`) treat the
# flagged event as the material COOPERATION between the partners it was.

#: Where the deep tier's raw files live when fetched; the COW alliance list is
#: the widest source of DECLARED alliance windows the corpus can read offline.
_RAW_DIR = Path(
    os.getenv("GEOGRAPH_RAW_DIR")
    or Path(__file__).resolve().parents[2] / "data" / "raw"
)
_COW_ALLIANCES = "alliance_v4.1_by_directed.csv"

#: The last year COW's alliance v4.1 file knows about — kept as documentation
#: of what a right-censored row actually claims. Windows are NOT closed here
#: (see `ally_windows`): doing so retired NATO in 2013.
COW_ALLIANCE_COVERAGE_END = 2012


def _year_of(value: Any, default: int) -> int:
    text = str(value or "").strip()
    return int(text[:4]) if text[:4].isdigit() else default


def ally_windows(pack: Any) -> tuple[dict[str, list[tuple[int, int]]], list[str]]:
    """dyad_id → the year windows in which the archive declares it allied,
    and where the declarations came from.

    Read OFFLINE — the pack's `relations` (in the image) and, when the COW
    alliance file is on disk, COW's directed alliance list restricted to the
    pack's roster. Windows, not pairs: a first pass took every pair ever
    allied since 1905, which put US–China (1942) and US–Iran (1958-79) in the
    ally sample with their whole wire-era record. An open window is
    (start, 9999). Used by the corpus's co-participation flag and by
    `scripts/fit_game.py --family ally`.
    """
    from core.classifier import escalation

    # A DECLARED RIVALRY ENDS AN ALLY WINDOW, and this is the fix for the
    # worst regression this file ever shipped. COW records an alliance with no
    # end year as RIGHT-CENSORED — "still in force when the data was
    # collected", which for alliance v4.1 means 2012 — and `_year_of` mapped
    # the blank to 9999. Russia and Ukraine signed a defence pact in 1995, so
    # the archive believed they were allies in 2026, and the allied-fight rule
    # in `classifier.coercion` then read every Russian strike on Ukrainian
    # soil as a partner's presence: 20,011 material-conflict rows fell to
    # 4,237, and the largest interstate war in the archive was 79% erased from
    # every counter downstream (2026-08-17, caught by audit before a reader
    # saw it — the unit test passed `allied` by hand and could not).
    #
    # The packs' curated relations are the modern truth and they say this
    # plainly: eurasia declares Russia–Ukraine a rivalry from 2014-02-27.
    # Antagonism outranks a lapsed treaty, exactly as `_STANDING_PRIORITY`
    # already has it for the standing a reader is shown.
    rivalries = {
        escalation.dyad_id(str(r["a"]), str(r["b"]))
        for r in pack.relations
        if str(r.get("relation_type")) == "rivalry"
    }
    roster = {str(a["id"]) for a in pack.actors}
    ccode_to_id = {
        int(a["cow_ccode"]): str(a["id"]) for a in pack.actors if a.get("cow_ccode")
    }
    windows: dict[str, list[tuple[int, int]]] = {}
    sources: list[str] = []
    for relation in pack.relations:
        # A DEFENCE PACT, not any treaty. These windows decide when two states
        # coded against each other are read as partners rather than opponents,
        # so admitting shared IGO membership here would have made every pair in
        # the United Nations co-participants.
        if relation.get("relation_type") == "alliance":
            dyad = escalation.dyad_id(str(relation["a"]), str(relation["b"]))
            if dyad in rivalries:
                continue
            windows.setdefault(dyad, []).append(
                (_year_of(relation.get("valid_from"), 1905),
                 _year_of(relation.get("valid_to"), 9999))
            )
    if windows:
        sources.append("packs")
    cow_file = _RAW_DIR / _COW_ALLIANCES
    if cow_file.exists():
        import csv

        with open(cow_file, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    a = ccode_to_id.get(int(row["ccode1"]))
                    b = ccode_to_id.get(int(row["ccode2"]))
                except (TypeError, ValueError):
                    continue
                if not a or not b or a == b or a not in roster or b not in roster:
                    continue
                # COW files four obligations under one file, and only the
                # defence pact means what this window is used for. See
                # `ingestion.cow.alliance_relation` for the same rule on the
                # graph side and for what reading them all as "alliance" did.
                if str(row.get("defense", "")).strip() != "1":
                    continue
                if escalation.dyad_id(a, b) in rivalries:
                    continue
                start = _year_of(row.get("dyad_st_year"), 1905)
                # A RIGHT-CENSORED WINDOW STAYS OPEN, and the rivalry check
                # above is what makes that safe. 60.7% of the file's
                # defence-pact rows carry no end year, which COW means as
                # "still in force when we stopped looking" (2012) — and
                # closing them there was measured to be worse: NATO went with
                # them, so the United States and the United Kingdom stopped
                # being allies in 2013 and their joint operations began
                # counting as coercion between them again. An alliance ends
                # when something says it ended; for the pairs where that
                # matters, the packs say so as a dated rivalry.
                end = _year_of(row.get("dyad_end_year"), 9999)
                if end < 1905:
                    continue
                windows.setdefault(escalation.dyad_id(a, b), []).append((start, end))
        sources.append("cow:alliance_v4.1")
    return windows, sources


def allied_in(windows: list[tuple[int, int]] | None, year: int) -> bool:
    return bool(windows) and any(start <= year <= end for start, end in windows or [])


def is_co_participation(
    row: dict[str, Any], windows: dict[str, list[tuple[int, int]]]
) -> bool:
    """The rule, over a corpus row carrying `quad_class`, `event_time`,
    `dyad_id`, `action_geo`, `initiator_iso3` and `target_iso3`."""
    if row.get("quad_class") != "material_conflict":
        return False
    geo = str(row.get("action_geo") or "")
    if not geo or geo in (row.get("initiator_iso3"), row.get("target_iso3")):
        return False
    return allied_in(windows.get(str(row.get("dyad_id"))), _year_of(row.get("event_time"), 0))
