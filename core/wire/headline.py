"""Display headlines for a wire row — verbs from the CAMEO root, not quad 4.

THE SURFACE COMPOSES THE SENTENCE (`web/src/lib/story.ts` `wireHeadline`).
This module is the deterministic twin the tests pin, and the helper the API
uses to attach named fields (`action_geo_name`, `third_country_force`,
`allied_presence`, `pair_fight`) so the composer has something to read.

WHAT THIS IS NOT. GDELT pins a roster flag by name, so an article that
mentions the United States while the action is in Syria can arrive as
ISR–USA. That is a coding defect, not a graph fact. Nothing here retargets
an actor, writes an edge, or drops a third-country fight (US–Iran in Syria
is real; the war in Ukraine is on Ukrainian soil). The rewrite is a DISPLAY
choice: a fight whose `action_geo` is a third country is headlined as force
IN that country, and a force coding between defence-pact partners is not
offered as an A–B fight.
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


def names_from_coded_title(name: str | None) -> tuple[str | None, str | None]:
    """Split GDELT's `Label: A → B` title. Never invents a side that is absent."""
    text = str(name or "")
    if ":" not in text or "→" not in text:
        return None, None
    rest = text.split(":", 1)[1]
    left, _, right = rest.partition("→")
    left, right = left.strip(), right.strip()
    if left and right:
        return left, right
    return None, None


def _iso3(row: dict[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip().upper()


def _force_root(row: dict[str, Any]) -> bool:
    return cameo_root(row.get("action_cameo_code") or row.get("cameo_code")) in FORCE_ROOTS


def third_country_force(
    row: dict[str, Any], roster_iso3: set[str] | frozenset[str] | None = None
) -> bool:
    """Fight/use-of-force coded on a country that is neither side.

    Display only. The stored initiator, target and dyad are left alone.
    `roster_iso3` is accepted so existing callers keep compiling; a geo off
    the pack is still a third country — we just cannot name it.
    """
    del roster_iso3
    geo = _iso3(row, "action_geo")
    left = _iso3(row, "initiator_iso3")
    right = _iso3(row, "target_iso3")
    if not geo or not left or not right:
        return False
    if geo in (left, right):
        return False
    return _force_root(row)


def allied_presence(row: dict[str, Any]) -> bool:
    """Defence-pact partners coded in a force event, not a war between them.

    Home-soil CAMEO 15/18/19/20 between allies is presence, an exercise, or a
    coding defect — the US–UK "assault" / "use of military force" rows. A
    missing geo still rewrites when the row is flagged `allied`, because GDELT
    often omits ActionGeo on those pairings. Display only.
    """
    if not _force_root(row):
        return False
    if row.get("co_participation"):
        return False
    geo = _iso3(row, "action_geo")
    left = _iso3(row, "initiator_iso3")
    right = _iso3(row, "target_iso3")
    on_home = bool(geo and geo in (left, right))
    if row.get("allied") is True:
        return on_home or not geo
    if row.get("allied") is False:
        return False
    # The corpus already refused this as interstate coercion, on home soil.
    return row.get("coercion") is False and on_home


def pair_fight(
    row: dict[str, Any], roster_iso3: set[str] | frozenset[str] | None = None
) -> bool:
    """Whether the surface may offer this row as an A–B fight.

    False for a third-country force coding, an allied-presence coding, and
    for a material-conflict row that `classifier.coercion` has already
    refused. Archive rows that never carried `coercion` keep the old offer
    (the field is absent, not false). Display/nav only — the dyad id is not
    rewritten.
    """
    if third_country_force(row, roster_iso3):
        return False
    if allied_presence(row):
        return False
    return not (
        row.get("quad_class") == "material_conflict" and row.get("coercion") is False
    )


def _place_name(row: dict[str, Any], geo_names: dict[str, str]) -> str | None:
    geo = _iso3(row, "action_geo")
    if not geo:
        return None
    return geo_names.get(geo) or str(row.get("action_geo_name") or "").strip() or None


def headline(
    row: dict[str, Any],
    *,
    geo_names: dict[str, str] | None = None,
    roster_iso3: set[str] | frozenset[str] | None = None,
) -> str:
    """A reader's headline from named fields. Does not mutate `row`."""
    names = geo_names or {}
    left = str(row.get("initiator_name") or "").strip()
    right = str(row.get("target_name") or "").strip()
    if not left or not right:
        parsed_left, parsed_right = names_from_coded_title(str(row.get("name") or ""))
        left = left or (parsed_left or "")
        right = right or (parsed_right or "")
    if third_country_force(row, roster_iso3) and left:
        place = _place_name(row, names) or "a third country"
        return f"{left} used force in {place}"
    if (allied_presence(row) or not pair_fight(row, roster_iso3)) and left and right:
        if _force_root(row):
            place = _place_name(row, names)
            if third_country_force(row, roster_iso3) and place:
                return f"{left} used force in {place}"
            if place:
                return (
                    f"{left} and {right} — a force coding in {place}, "
                    "not a fight between them"
                )
            return f"{left} and {right} — a force coding, not a fight between them"
        if row.get("coercion") is False:
            return (
                f"{left} and {right} — a coded event, not interstate coercion"
            )
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
    geo = _iso3(row, "action_geo") or None
    initiator_iso3 = _iso3(row, "initiator_iso3") or None
    target_iso3 = _iso3(row, "target_iso3") or None
    coercion = row.get("coercion")
    shaped = {
        **row,
        "action_geo": geo,
        "initiator_iso3": initiator_iso3,
        "target_iso3": target_iso3,
    }
    third = third_country_force(shaped, roster)
    presence = allied_presence(shaped)
    return {
        "action_geo": geo,
        "action_geo_name": geo_names.get(geo) if geo else None,
        "initiator_iso3": initiator_iso3,
        "target_iso3": target_iso3,
        "third_country_force": third,
        "allied_presence": presence,
        "pair_fight": pair_fight(shaped, roster),
        "coercion": None if coercion is None else bool(coercion),
        "headline": headline(shaped, geo_names=geo_names, roster_iso3=roster),
    }


def decorate(
    row: dict[str, Any],
    *,
    actor_names: dict[str, str],
    geo_names: dict[str, str],
    iso3_by_actor: dict[str, str] | None = None,
    allied: bool | None = None,
) -> dict[str, Any]:
    """Fill actor names, ISO3, display flags and a composed headline.

    Does not retarget the stored pair. Missing names fall back to the coded
    `Label: A → B` title when that is all the row carried.
    """
    shaped = dict(row)
    if allied is not None:
        shaped["allied"] = allied
    initiator_id = str(shaped.get("initiator_id") or "")
    target_id = str(shaped.get("target_id") or "")
    parsed_left, parsed_right = names_from_coded_title(str(shaped.get("name") or ""))
    if not shaped.get("initiator_name"):
        shaped["initiator_name"] = actor_names.get(initiator_id) or parsed_left
    if not shaped.get("target_name"):
        shaped["target_name"] = actor_names.get(target_id) or parsed_right
    lookup = iso3_by_actor or {}
    if not shaped.get("initiator_iso3") and initiator_id:
        shaped["initiator_iso3"] = lookup.get(initiator_id)
    if not shaped.get("target_iso3") and target_id:
        shaped["target_iso3"] = lookup.get(target_id)
    if not shaped.get("cameo_code"):
        shaped["cameo_code"] = shaped.get("action_cameo_code")
    display = display_fields(shaped, geo_names=geo_names)
    shaped.update(display)
    return shaped
