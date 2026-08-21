"""The dyad's OPENING STATE, read from data instead of assumed.

The game's opening conditions were hardcoded — capability "1 — balanced",
beliefs 0.5/0.5 — for every dyad, in the frozen sequence forecast and on
every interactive solve. The graph holds CINC capability estimates and the
archive holds each side's observed actions, so both were assumptions wearing
a default where a measurement existed:

- CAPABILITY comes off the two actors' latest `clout` AttributeEstimates
  (the deep tier's CINC), banded on the challenger/leader ratio exactly as
  `state.CAPABILITY_EDGES` defines the axis.
- BELIEFS come from the game's OWN Bayes rule run over the dyad's observed
  recent joint actions: a side that has spent three years escalating walks in
  believed resolute, one that has folded walks in believed irresolute. The
  update rule is `solve.posterior` — the same likelihoods the equilibrium
  uses along a path — so the opening belief and the in-game belief dynamics
  are one mechanism, not two.

Everything returns its provenance: a reader must be able to see whether an
opening state was measured or defaulted, because the two deserve different
trust. Deterministic; no model output enters here (the ML bridge is
`core/games/bridge.py`, and it is labelled).
"""

from __future__ import annotations

from typing import Any

from core.games import family as family_module
from core.games import solve as solve_module
from core.games import state as state_module
from core.graph import kuzu_store

#: Quarters of observed actions the belief filter consumes. Long enough to
#: hold a posture, short enough that a détente is believed within a few
#: years of it starting.
BELIEF_QUARTERS = 12


def dyad_actors(dyad_id: str) -> tuple[str, str]:
    """`dyad:cow-630--cow-666` → the two actor node ids, sorted — the exact
    inverse of `escalation.dyad_id`."""
    bare = dyad_id.split(":", 1)[-1]
    first, _, second = bare.partition("--")
    return f"actor:{first}", f"actor:{second or first}"


def _latest_clout(conn: Any, actor_id: str) -> float | None:
    rows = kuzu_store.query(
        conn,
        "MATCH (a:Actor {node_id: $id})-[:HAS_ESTIMATE]->(s:AttributeEstimate) "
        "WHERE s.attribute = 'clout' "
        "RETURN s.value_mean AS value, s.as_of AS as_of "
        "ORDER BY s.as_of DESC LIMIT 1",
        {"id": actor_id},
    )
    if not rows or rows[0]["value"] is None:
        return None
    return float(rows[0]["value"])


def capability_state(conn: Any | None, dyad_id: str) -> dict[str, Any]:
    """The capability band, measured from CINC where the graph holds it.

    Falls back to the balanced band with `source: "default"` — visibly, so a
    defaulted band never wears a measurement's face.
    """
    if conn is not None:
        actor_a, actor_b = dyad_actors(dyad_id)
        clout_a = _latest_clout(conn, actor_a)
        clout_b = _latest_clout(conn, actor_b)
        if clout_a is not None and clout_b is not None and max(clout_a, clout_b) > 0:
            ratio = min(clout_a, clout_b) / max(clout_a, clout_b)
            return {
                "band": state_module.capability_band(ratio),
                "ratio": round(ratio, 4),
                "source": "cinc",
            }
    return {"band": 1, "ratio": None, "source": "default"}


#: Which standing characterises a pair when several are in force. Not a
#: judgement about importance in general — a claim about what a reader needs
#: first when the same two states are simultaneously bound and opposed.
#: WHICH DECLARED RELATION CHARACTERISES THE PAIR when several are live.
#: Antagonism first (a pact between rivals is evidence of the rivalry, not a
#: replacement for it), then the obligations that imply alignment, then the
#: ones that imply only contact. `non_aggression` and `entente` sit BELOW
#: membership deliberately: they are the weakest claims COW records and the
#: least entitled to lead a sentence that begins "the archive declares this
#: pair…".
_STANDING_PRIORITY = {
    "rivalry": 0, "proxy": 1, "alliance": 2, "membership": 3, "trade": 4,
    "non_aggression": 5, "entente": 6,
}


def standing(conn: Any | None, dyad_id: str, *, as_of: str) -> dict[str, Any]:
    """WHAT THE PAIR IS, from the curated RELATES_TO web — not from wire mood.

    The surface used to characterise a relationship by the mean Goldstein of
    its recent coded events, which is a statistic about COVERAGE: GDELT codes
    meetings, calls and statements in far greater number than anything
    coercive, so two thirds of every region's pairs scored positive and the
    chip read "friendly" over the United States and China. The archive already
    holds the answer as a sourced fact — `packs/china/actors.yaml` declares
    that pair a `rivalry` from the 2018 tariffs, with a citation — and a
    curated, dated, sourced relation outranks an average of press coverage.

    Returns every relation in force at `as_of` (a pair can be both allied and
    rivalrous over different windows; the dates are what disambiguate), most
    recently entered first. Empty is a real answer — "no declared standing" —
    and is reported rather than filled in with a guess.
    """
    if conn is None:
        return {"relations": [], "source": "no graph"}
    actor_a, actor_b = dyad_actors(dyad_id)
    rows = kuzu_store.query(
        conn,
        # Both directions: RELATES_TO is stored as declared, and a rivalry is
        # symmetric even when the row is not. `proxy` is the one directed type
        # (patron → client), so the direction is carried through, not erased.
        "MATCH (a:Actor)-[r:RELATES_TO]->(b:Actor) "
        "WHERE (a.node_id = $a AND b.node_id = $b) "
        "   OR (a.node_id = $b AND b.node_id = $a) "
        "RETURN r.relation_type AS relation_type, r.valid_from AS valid_from, "
        "r.valid_to AS valid_to, r.source_id AS source_id, "
        "a.node_id AS from_id, b.node_id AS to_id "
        "ORDER BY r.valid_from DESC",
        {"a": actor_a, "b": actor_b},
    )
    live = [
        row for row in rows
        if str(row["valid_from"] or "") <= as_of
        and (not row["valid_to"] or str(row["valid_to"]) >= as_of)
    ]
    # A RIVALRY SUPERSEDES AN ALLIANCE THAT PREDATES IT. Two states bound by a
    # pact who then became rivals are no longer meaningfully allied. Russia and
    # Ukraine signed the CIS defence pact in 1995 and became a declared rivalry
    # in 2014; COW never records the pact's lapse (it is right-censored), so the
    # archive read them as "formal allies since 1995" beside the largest war it
    # holds — the lapse a reader cannot see because the data does not encode it.
    #
    # THE DATE IS THE TELL, and it is exactly what separates this from the Korean
    # peninsula. There the rivalry (1948) PREDATES the 1991 Basic Agreement, so
    # the pact is a de-escalation gesture WITHIN an ongoing rivalry and both
    # genuinely coexist (the standing names both). Russia–Ukraine is the reverse:
    # the rivalry began AFTER the alliance, which means the rivalry replaced it.
    # So an alliance/membership is dropped only when a live rivalry began
    # strictly after it. ISO-8601 dates sort lexically at any resolution, which
    # is what makes the comparison valid across year-only and full dates.
    latest_rivalry = max(
        (str(row["valid_from"] or "") for row in live
         if str(row["relation_type"]) == "rivalry"),
        default="",
    )
    if latest_rivalry:
        live = [
            row for row in live
            if not (
                str(row["relation_type"]) in {"alliance", "membership"}
                and str(row["valid_from"] or "") < latest_rivalry
            )
        ]
    # ANTAGONISM OUTRANKS AGREEMENT WHEN BOTH ARE IN FORCE, and the Korean
    # peninsula is why. COW codes the 1991 Basic Agreement between North and
    # South Korea as an alliance (its dataset folds non-aggression pacts in),
    # and packs/china declares the same pair a rivalry from 1948 — both true,
    # both live. Ordered by recency the chip read "alliance" over the most
    # militarised border in the archive. A pact between rivals is evidence of
    # the rivalry, not a replacement for it, so the standing that
    # CHARACTERISES the pair leads and the rest follow (the sentence names
    # both). Python's sort is stable, so recency still orders within a type.
    live.sort(key=lambda row: _STANDING_PRIORITY.get(str(row["relation_type"]), 9))
    # ONE ROW PER KIND, EARLIEST WINS. COW carries a pair's alliance history as
    # several records — NATO, a bilateral treaty, a later accession — all live
    # at once, so the chip read "alliance, alliance" and the sentence said the
    # same thing twice. The kind is what characterises the pair; the founding
    # date is what a reader wants beside it ("formal allies since 1949", not
    # since the most recent protocol). Stable sort, so priority order survives.
    by_kind: dict[str, dict[str, Any]] = {}
    for row in live:
        kind = str(row["relation_type"])
        keep = by_kind.get(kind)
        if keep is None or str(row["valid_from"] or "") < str(keep["valid_from"] or ""):
            by_kind[kind] = row
    live = [row for row in live if by_kind.get(str(row["relation_type"])) is row]
    return {
        "relations": [
            {
                "relation_type": str(row["relation_type"]),
                "since": str(row["valid_from"] or ""),
                "until": str(row["valid_to"] or "") or None,
                "source_id": str(row["source_id"] or ""),
                "directed_from": str(row["from_id"]),
            }
            for row in live
        ],
        "source": "relates_to",
        "as_of": as_of,
    }


#: Coded events in the window below which the posture is UNREAD rather than
#: named. Four quarters of a dozen wire records is a sample, not a posture —
#: and the wire's thin pairs are exactly where a share of a handful produced
#: the most confident-looking numbers (Sweden–Norway at 0.0%, United
#: States–Belarus at tone +5.07).
POSTURE_MIN_EVENTS = 25

#: Share of a window's coded events classed `material_conflict` → the word.
#: Cut on the archive's OWN distribution, measured 2026-08-15 over the pairs
#: with enough coverage to rank: the median busy pair sits near 5%, the upper
#: decile near 25%, and the pairs everyone would name as wars sit above 30%
#: (Russia–Ukraine 36% of 3,348 events, North Korea–Japan 43%). The words
#: describe THE CODED RECORD, never the relationship — that is `standing`'s
#: job — so no reading of them can contradict a declared rivalry.
POSTURE_EDGES = (
    (0.02, "almost all talk"),
    (0.10, "mostly talk"),
    (0.25, "mixed record"),
    (0.45, "often coercive"),
)


def posture_label(share: float) -> str:
    for edge, label in POSTURE_EDGES:
        if share < edge:
            return label
    return "mostly coercive"


def posture(rows: list[dict[str, Any]], *, quarters: int = 4) -> dict[str, Any]:
    """HOW THE RECORD READS LATELY: the coercive share of the pair's coded
    events over its last `quarters` active quarters.

    Replaces the mean-Goldstein "tone" as the absolute read. Mean tone is
    dominated by the volume of routine diplomacy, so it ranked the pairs by
    how much they TALK; the material-conflict share ranks them by how much of
    what they do is coercive, which is the thing the word was trying to say.
    Tone is kept beside it — it is still the sign the escalation coder reads —
    but it is a number now, not a verdict.
    """
    recent = sorted(rows, key=lambda r: r["q"])[-quarters:]
    events = sum(int(r.get("events") or 0) for r in recent)
    coercive = sum(int(r.get("conflict") or 0) for r in recent)
    tones = [float(r["tone"]) for r in recent if r.get("tone") is not None]
    tone = round(sum(tones) / len(tones), 3) if tones else None
    if events < POSTURE_MIN_EVENTS:
        return {
            "label": "too little coverage to read",
            "share": None,
            "events": events,
            "coercive": coercive,
            "tone": tone,
            "quarters": len(recent),
            "thin": True,
        }
    share = coercive / events
    return {
        "label": posture_label(share),
        "share": round(share, 4),
        "events": events,
        "coercive": coercive,
        "tone": tone,
        "quarters": len(recent),
        "thin": False,
    }


def filtered_beliefs(
    joint: dict[tuple[str, int], tuple[str, str]],
    dyad_id: str,
    payoffs: solve_module.Payoffs,
    *,
    quarters: int = BELIEF_QUARTERS,
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> dict[str, Any]:
    """P(resolute) per side, filtered from the dyad's observed recent actions.

    `belief_a` is A's belief that B IS RESOLUTE, so it updates on B's
    observed actions (and vice versa) — the same orientation the solver uses.
    Starts from the uninformed 0.5 prior and folds the last `quarters`
    quarters in order.
    """
    observed = sorted(
        (quarter, actions)
        for (dyad, quarter), actions in joint.items()
        if dyad == dyad_id
    )[-quarters:]
    belief_a = 0.5  # A's belief about B
    belief_b = 0.5  # B's belief about A
    for _, (action_a, action_b) in observed:
        belief_a = solve_module.posterior(
            belief_a, space.index(action_b), payoffs, space
        )
        belief_b = solve_module.posterior(
            belief_b, space.index(action_a), payoffs, space
        )
    return {
        "a": round(belief_a, 4),
        "b": round(belief_b, 4),
        "quarters_observed": len(observed),
        "source": "bayes_filter" if observed else "default",
    }
