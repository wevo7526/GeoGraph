"""WHAT COUNTS AS A COERCIVE ACT BETWEEN TWO STATES.

One definition, in one place, because four readers were each counting
`quad_class == "material_conflict"` and calling the answer "coercion": the
posture that gives a pair its character (`games.opening.posture`), the family
that decides which game it plays (`games.family.classify`), the region map's
ranking, and the transition kernel. GDELT's quad class is a coding of a
sentence, not a claim about states, and the gap between those two things is
what put the wrong pairs at the top of the board.

THE MEASUREMENT THAT MOTIVATES EVERY RULE BELOW (2026-08-17, over the shipped
artifacts, last four quarters):

    pair             raw   −arrest   −1-source   −non-state   final
    US–Russia        188       149         145          131     131
    US–UK            194       116         116          102      59
    US–China         150       114         113           83      83
    US–Poland         17        13          13           13       3
    Russia–Ukraine  1781      1738        1732         1707    1707

Raw counts made the United States and the United Kingdom (194) a more coercive
pair than the United States and Russia (188). They are not, and the reason the
count said so is legible in the codes: US–UK's single largest contributor was
CAMEO 173, "arrest, detain or charge with legal action", 73 of the 194, with
the action on British or American soil — British police arresting somebody,
in a story that mentions America. US–Russia's was 163, "impose embargo,
boycott or sanctions", 41 of 188, on Russian soil. Same quad class, same
count, entirely different events.

WHAT WAS REJECTED, AND WHY IT MATTERS. An earlier version of the last rule
dropped every root-19 ("fight") event whose action was on one of the pair's
own soil. It flattered the ranking and it deleted the war: Russia–Ukraine fell
from 1,707 to 342, because that war is fought on Ukrainian soil. The rule
survives only where a DECLARED DEFENCE PACT makes the reading safe — partners'
forces on a partner's territory are presence, not attack — which is the same
evidence-led correction `is_co_participation` already applies on third-country
soil, and it is the reason the alliance type had to be fixed first
(`ingestion.cow` was importing non-aggression pacts as alliances).

Nothing here drops an EVENT. The wire keeps every row it parsed, the explorer
still shows them and the transmission engine still measures them; what changes
is which of them a classifier is allowed to call coercion between two states.
"""

from __future__ import annotations

from typing import Any

#: CAMEO codes for coercion applied to PERSONS, not to a state: 173 arrest or
#: detain, 174 expel or deport individuals, 175 use of repression. They are
#: real coercion and they belong in the archive; they are not one state
#: coercing another, and counting them as such is what made a country's own
#: police blotter read as an international dispute. 73 of US–UK's 194 were 173
#: alone.
PERSONAL_ENFORCEMENT = ("173", "174", "175")

#: The floor on corroboration. GDELT's NumMentions counts repeats of the same
#: story, so the shipped `min_mentions` bar does not establish that two
#: outlets saw the same thing; NumSources does. One source is one article, and
#: an actor pairing drawn from one article is where the strangest rows come
#: from ("Poland vs the Associated Press", coded as material conflict between
#: Poland and the United States).
MIN_SOURCES = 2

#: GDELT actor TYPE codes that are not the state. An actor's country code is
#: filled in for any actor with a nationality, so a newspaper, a company, a
#: university, a dissident or a rebel group all arrive wearing their country's
#: name — which is how the Associated Press became the United States. These
#: are the types that must not be read as the state acting.
NON_STATE_TYPES = frozenset({
    "MED",  # media
    "BUS", "MNC",  # business, multinational
    "EDU", "SCI",  # education, science
    "CVL",  # civilians
    "ELI",  # elites, named individuals
    "OPP",  # opposition
    "NGO", "IGO",  # organisations
    "REL",  # religious
    "HLH", "ENV", "LAB", "HRI", "DEV", "SOC", "MOD",  # issue actors
    "RAD", "CRM", "REF",  # radicals, criminals, refugees
    "JUD", "LEG",  # courts and legislatures act domestically
    "SPY",  # intelligence services are deniable by construction
    "UAF",  # unaligned armed forces
})

#: The state types that DO act for the state, kept explicit so the rule reads
#: as an allow-list even though it is written as a deny-list: an empty type is
#: the bare country, and GOV / MIL / COP are its organs.
STATE_TYPES = frozenset({"", "GOV", "MIL", "COP"})

#: CAMEO root 19 is "fight". For a pair under a declared defence pact, a fight
#: coded on one partner's own territory is presence, not war between them.
FIGHT_ROOT = "19"


def _type_is_state(value: Any) -> bool:
    return str(value or "").strip().upper() not in NON_STATE_TYPES


def counts_as_coercion(
    row: dict[str, Any], *, allied: bool = False
) -> bool:
    """Is this corpus row one state coercing the other?

    `row` is a corpus row: `quad_class`, `action_cameo_code`, `num_sources`,
    `actor1_type`, `actor2_type`, `action_geo`, `initiator_iso3`,
    `target_iso3`. `allied` says whether a DEFENCE PACT was in force between
    the pair at the time — the caller knows the windows
    (`games.family.ally_windows`), this module knows what to do with them.

    Rows written before the quality columns existed carry none of them; the
    unknown case is treated as passing, so an old artifact degrades to the old
    behaviour rather than to silence.
    """
    if row.get("quad_class") != "material_conflict":
        return False
    if row.get("co_participation"):
        # Allies fighting alongside each other on third-country soil. The
        # existing reading, kept: this module is the second half of it.
        return False
    code = str(row.get("action_cameo_code") or "")
    if code.startswith(PERSONAL_ENFORCEMENT):
        return False
    sources = row.get("num_sources")
    if sources is not None and int(sources) < MIN_SOURCES:
        return False
    if not _type_is_state(row.get("actor1_type")):
        return False
    if not _type_is_state(row.get("actor2_type")):
        return False
    if allied and code[:2] == FIGHT_ROOT:
        geo = str(row.get("action_geo") or "")
        if geo and geo in (row.get("initiator_iso3"), row.get("target_iso3")):
            return False
    return True
