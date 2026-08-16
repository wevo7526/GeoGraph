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

WHAT THIS MODULE DOES AND DOES NOT DO. It names the family and the question
that family's game is entitled to ask, and it gives the surface a vocabulary
that is not borrowed from war. It does NOT yet give each family its own action
set and fitted payoffs — that is the second half, and it is a bigger change:
each family's actions have to map onto coded quad classes so its payoffs stay
estimable from the record rather than invented (build-spec §17). Until then a
pair outside the adversarial family is SOLVED THE OLD WAY AND SAID TO BE, so
the reader can discount it, rather than being quietly presented as a war game.
"""

from __future__ import annotations

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
            "structural competition below the use of force; the same "
            "bargaining game, read for whether the threshold is approached "
            "rather than for how a crisis ends"
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
            "an alliance-management problem, NOT a crisis. The solver still "
            "runs the bargaining game, so this pair's numbers describe "
            "departures from its own usual level of friction and must not be "
            "read as odds of conflict"
        ),
    },
}


def classify(
    standing: dict[str, Any] | None,
    posture: dict[str, Any] | None,
) -> dict[str, Any]:
    """Which family this pair belongs to, and the evidence that decided it.

    Returns the family, the reason in a reader's words, and whether the
    solver's own game is the right one for it (`native`). A pair the platform
    cannot classify is `rival` — the weakest of the three claims — rather than
    `adversary`, because calling two states adversaries is a strong statement
    and absence of evidence must never make it.
    """
    kinds = {
        str(r.get("relation_type"))
        for r in ((standing or {}).get("relations") or [])
    }
    share = (posture or {}).get("share")
    thin = bool((posture or {}).get("thin", share is None))
    coercive = float(share) if share is not None else None

    # ANTAGONISM FIRST, matching `opening._STANDING_PRIORITY`: a pact between
    # rivals is evidence of the rivalry, not a replacement for it, so a pair
    # that is declared both is read as the rivalry it is.
    if "rivalry" in kinds:
        if coercive is not None and coercive >= ADVERSARY_SHARE:
            family, why = "adversary", (
                f"a declared rivalry whose record is {coercive:.0%} coercive"
            )
        elif coercive is not None and coercive < RIVAL_CEILING:
            family, why = "rival", (
                f"a declared rivalry conducted in argument — {coercive:.0%} "
                "of its coded events are coercive"
            )
        else:
            family, why = "rival", "a declared rivalry"
    elif kinds & {"alliance", "membership"}:
        # A declared ally whose record is genuinely violent is not an ally in
        # the sense that matters here, and the archive has such pairs.
        if coercive is not None and coercive >= ADVERSARY_SHARE:
            family, why = "adversary", (
                f"declared allies whose record is {coercive:.0%} coercive — "
                "the behaviour outweighs the declaration"
            )
        else:
            family, why = "ally", "a declared alliance"
    elif coercive is not None and coercive >= ADVERSARY_SHARE:
        family, why = "adversary", (
            f"no declared relation, and {coercive:.0%} of its coded events "
            "are coercive"
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
        # THE SOLVER'S GAME IS THE ADVERSARIAL ONE. Saying so is the honest
        # half of this change until each family has its own actions and
        # fitted payoffs: a reader can then discount an ally's numbers
        # instead of being handed brinkmanship language for a treaty partner.
        "native": family == "adversary",
        "question": semantics["question"],
        "headline": semantics["headline"],
        "bad_end": semantics["bad_end"],
        "note": semantics["note"],
    }


def describe(classification: dict[str, Any]) -> str:
    """One sentence naming the family, the evidence, and — when the solved
    game is not this family's own — what that costs the reading."""
    family = classification["family"]
    line = (
        f"This pair is read as {'an' if family == 'ally' else 'a'} {family} "
        f"pair ({classification['why']}), so the question worth asking of it "
        f"is {classification['question']}."
    )
    if not classification.get("native"):
        line += (
            " The solver runs the adversarial crisis-bargaining game for every "
            "pair, so the numbers below describe departures from this pair's "
            f"OWN usual level of {classification['headline']} and are not odds "
            f"of {classification['bad_end']}."
        )
    return line
