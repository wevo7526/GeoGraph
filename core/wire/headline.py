"""Display headlines for a wire row — verbs from the CAMEO root, not quad 4.

THE SURFACE COMPOSES THE SENTENCE (`web/src/lib/story.ts` `wireHeadline`).
This module is the deterministic twin the tests pin, and the helper the API
uses to attach named fields (`action_geo_name`, `third_country_force`,
`pair_fight`) so the composer has something to read.

WHAT THIS IS NOT. GDELT pins a roster flag by name, so an article that
mentions the United States while the action is in Syria can arrive as
ISR–USA. That is a coding defect, not a graph fact. Nothing here retargets
an actor, writes an edge, or drops a third-country fight (US–Iran in Syria
is real; the war in Ukraine is on Ukrainian soil). The rewrite is a DISPLAY
choice: a fight whose `action_geo` is a third roster country is headlined
as force IN that country, and is not offered as an A–B fight relationship.
"""

from __future__ import annotations

from typing import Any

#: CAMEO roots that are a show or use of force. Quad 4 also covers protest,
#: reducing relations and coercion (sanctions, arrests) — those are not
#: rewritten as "used force in {geo}".
FORCE_ROOTS = frozenset({"15", "18", "19", "20"})

#: Root → a verb phrase a sentence can carry. Quad-class fallbacks used to
#: collapse every material-conflict row to "used force toward".
ROOT_ACT: dict[str, str] = {
    "01": "issued a statement about",
    "02": "appealed to",
    "03": "expressed intent to cooperate with",
    "04": "consulted",
    "05": "cooperated diplomatically with",
    "06": "cooperated materially with",
    "07": "provided aid to",
    "08": "yielded to",
    "09": "investigated",
    "10": "demanded of",
    "11": "disapproved of",
    "12": "rejected",
    "13": "threatened",
    "14": "protested against",
    "15": "exhibited force toward",
    "16": "reduced relations with",
    "17": "coerced",
    "18": "assaulted",
    "19": "fought",
    "20": "used mass violence against",
}

QUAD_ACT: dict[str, str] = {
    "verbal_cooperation": "spoke with",
    "material_cooperation": "cooperated with",
    "verbal_conflict": "spoke against",
    "material_conflict": "used force toward",
}


def cameo_root(code: str | int | None) -> str | None:
    """The two-digit CAMEO root, or None when the value is not a code."""
    digits = "".join(c for c in str(code or "") if c.isdigit())
    if len(digits) < 2:
        return None
    return digits[:2]


def act_phrase(row: dict[str, Any]) -> str:
    """The verb the headline uses. Root first; quad class only as fallback."""
    root = cameo_root(row.get("action_cameo_code") or row.get("cameo_code"))
    if root and root in ROOT_ACT:
        return ROOT_ACT[root]
    quad = str(row.get("quad_class") or "")
    return QUAD_ACT.get(quad, "interacted with")


def third_country_force(
    row: dict[str, Any], roster_iso3: set[str] | frozenset[str]
) -> bool:
    """Fight/use-of-force coded on a roster country that is neither side.

    Display only. The stored initiator, target and dyad are left alone.
    """
    geo = str(row.get("action_geo") or "").strip().upper()
    left = str(row.get("initiator_iso3") or "").strip().upper()
    right = str(row.get("target_iso3") or "").strip().upper()
    if not geo or not left or not right:
        return False
    if geo in (left, right):
        return False
    if geo not in roster_iso3:
        return False
    return cameo_root(row.get("action_cameo_code") or row.get("cameo_code")) in FORCE_ROOTS


def pair_fight(row: dict[str, Any], roster_iso3: set[str] | frozenset[str]) -> bool:
    """Whether the surface may offer this row as an A–B fight.

    False for a third-country force coding, and for a live material-conflict
    row that `classifier.coercion` has already refused. Archive rows that
    never carried `coercion` keep the old offer (the field is absent, not
    false). Display/nav only — the dyad id is not rewritten.
    """
    if third_country_force(row, roster_iso3):
        return False
    return not (
        row.get("quad_class") == "material_conflict" and row.get("coercion") is False
    )


def headline(
    row: dict[str, Any],
    *,
    geo_names: dict[str, str] | None = None,
    roster_iso3: set[str] | frozenset[str] | None = None,
) -> str:
    """A reader's headline from named fields. Does not mutate `row`."""
    names = geo_names or {}
    roster = roster_iso3 if roster_iso3 is not None else set(names)
    left = str(row.get("initiator_name") or "").strip()
    right = str(row.get("target_name") or "").strip()
    if third_country_force(row, roster) and left:
        geo = str(row.get("action_geo") or "").strip().upper()
        place = names.get(geo) or geo
        return f"{left} used force in {place}"
    act = act_phrase(row)
    if left and right:
        return f"{left} {act} {right}"
    if left:
        return f"{left} {act} an unnamed counterpart"
    return "A coded event between unnamed actors"


def display_fields(
    row: dict[str, Any],
    *,
    geo_names: dict[str, str],
) -> dict[str, Any]:
    """Named fields the wire payload carries so the surface can compose.

    Pass-through of corpus columns when present; never invents a geo or a
    retarget. `action_geo_name` is the pack roster's name for that ISO3.
    """
    roster = set(geo_names)
    geo = str(row.get("action_geo") or "").strip().upper() or None
    initiator_iso3 = str(row.get("initiator_iso3") or "").strip().upper() or None
    target_iso3 = str(row.get("target_iso3") or "").strip().upper() or None
    coercion = row.get("coercion")
    shaped = {
        **row,
        "action_geo": geo,
        "initiator_iso3": initiator_iso3,
        "target_iso3": target_iso3,
    }
    third = third_country_force(shaped, roster)
    return {
        "action_geo": geo,
        "action_geo_name": geo_names.get(geo) if geo else None,
        "initiator_iso3": initiator_iso3,
        "target_iso3": target_iso3,
        "third_country_force": third,
        "pair_fight": pair_fight(shaped, roster),
        "coercion": None if coercion is None else bool(coercion),
    }
