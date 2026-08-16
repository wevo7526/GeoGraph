"""The region scenario map: every active dyad solved, named, priced, explained.

`/games/explore` answers one dyad at a time and persists nothing. This module
is the map the game-theory page reads: for a region, every dyad the panel
can model is solved at its DATA-DRIVEN opening state (CINC capability,
beliefs filtered from its own actions, the gated ML model's kernel tilt where
one is frozen), under BOTH stage concepts — the fitted quantal response and
the LP correlated equilibrium with its distance from a Nash point — its
paths are walked and priced to the measured market map, and each course of
play is NAMED as a scenario with a likelihood. The region view aggregates
those into a future-event map: which dyads carry escalation mass, over which
quarters, priced to which markets.

THE EXPLANATION IS WRITTEN FROM THE NUMBERS AND NOTHING ELSE. `explain()` is a
template over the solution's own fields — no model originates a sentence the
payload cannot substantiate (build-spec §17). It reads like an analyst's note
because the quantities have meanings; that is the point of a structural game
over a black box.

Persistence lives in `core/panel/pg_store.py` (`game_solutions`), written by
`scripts/solve_games.py`; nothing here writes.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from core.games import context as context_module
from core.games import family as family_module
from core.games import opening as opening_module
from core.games import paths as paths_module
from core.games import pricing as pricing_module
from core.games import solve as solve_module
from core.games import state as state_module

#: Dyads solved per region — the panel's most active first. Beyond this the
#: series are thin and the opening state is mostly the default.
REGION_DYADS = 12

#: THE SHAPE OF A PERSISTED SOLUTION, stamped into every payload this module
#: builds and checked by the reader (`pg_store.game_solution`). Bump it
#: whenever a field the surface reads is renamed, added or given a new
#: meaning — a mismatch makes the stored row a cache MISS and the endpoint
#: solves live instead of serving it.
#:
#: This exists because of the 2026-08-15 failure it would have caught: the
#: ranking's metric was renamed `escalation_probability` →
#: `sharp_departure_probability`, the games boot step is opt-in and did not
#: re-solve, and Postgres kept serving payloads written an hour earlier by the
#: previous shape. The API answered 200 with a payload that validated against
#: nothing, and every pair on the region map read "NaN%" — beside courses of
#: play named at 100%, because those rows also predate the belief ceiling that
#: stopped a filtered belief reaching certainty. A persisted computation
#: outlives the code that wrote it; the version is what makes that safe.
PAYLOAD_VERSION = "2026-08-16.10"

#: A step's market row needs this many measurements before the scenario
#: names it as an implication (the pricing module's own thinness bar).
_MARKET_MIN = pricing_module.MIN_MEASUREMENTS

#: INTENSITY BANDS ARE RELATIVE — the quarter's largest escalating departure
#: from the pair's OWN baseline, banded against the pair's own scale — so the
#: labels are departure words, never absolute conflict words. "United States
#: – Japan in the tense band" was the 2026-08-15 finding: a high-baseline
#: alliance's routine friction wearing a hostility label. The ABSOLUTE reads
#: are `opening.standing` (what the pair IS — curated, sourced, dated) and
#: `opening.posture` (how its coded record READS lately — the coercive share
#: of its events). Mean tone was the absolute read until it called two thirds
#: of every region "friendly", the United States and China among them.
BAND_LABELS = (
    "at baseline", "mild departure", "notable departure",
    "sharp departure", "rupture", "extreme rupture",
)
BAND_SEMANTICS = (
    "bands are departures from the pair's own baseline (relative friction, "
    "banded on the pair's own scale), not absolute hostility; what the pair IS "
    "is its declared standing, and how its record reads lately is the coercive "
    "share of its coded events"
)


def tone_label(tone: float | None) -> str:
    """The raw SIGN of the mean Goldstein, kept for readers who want it.

    NOT a characterisation of the relationship, and nothing user-facing may
    present it as one: the wire codes far more meetings than coercion, so this
    ranks pairs by how much they talk. Measured 2026-08-15 across the three
    packs: 65% of china's pairs, 64% of eurasia's and 51% of mena's scored
    "friendly" or better, the United States and China (a declared rivalry
    since 2018) among them at +1.65.
    """
    if tone is None:
        return "unread"
    if tone >= 2.0:
        return "cooperative"
    if tone >= 0.5:
        return "friendly"
    if tone > -0.5:
        return "mixed"
    if tone > -2.0:
        return "strained"
    if tone > -5.0:
        return "hostile"
    return "conflictual"

BOUNDARY_STATEMENT = (
    "Scenarios are courses of play the solved game puts mass on, priced by "
    "what such courses have historically moved — pressure over quarters, not "
    "dated predictions. The kernel is counted from the archive; the payoffs "
    "are fitted at bounds and read as a direction; the LP's nash_gap says how "
    "far its welfare-maximal play is from a Nash point. Not advice."
)


def band_label(band: int, bands: int) -> str:
    if bands <= 1:
        return BAND_LABELS[0]
    index = int(round(band / (bands - 1) * (len(BAND_LABELS) - 1)))
    return BAND_LABELS[max(0, min(index, len(BAND_LABELS) - 1))]


def split_sides(dyad_name: str) -> tuple[str, str]:
    for sep in ("–", "—", " - ", "--"):
        if sep in dyad_name:
            a, b = dyad_name.split(sep, 1)
            return a.strip(), b.strip()
    return dyad_name, "the other side"


# ── naming a course of play ─────────────────────────────────────────────────


#: Course kinds that MEAN pressure, and the ones that mean the opposite.
#: Named here because the sorting rule below has to prefer them over the band
#: delta, and the two genuinely disagree.
PRESSING_KINDS = ("mutual_escalation", "one_sided_pressure", "brinkmanship")
CALMING_KINDS = ("step_down", "drift_down")


def sort_scenarios(
    scenarios: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(escalatory, calming), most mass first — THE KIND WINS OVER THE DELTA.

    Getting that backwards printed "Russia-Japan - step down at 92%" under the
    heading "the escalatory scenarios with the most mass". The two fields
    measure different things: `kind` names the ACTIONS played, `delta_band` is
    where the pair ended up against its own baseline, and a pair can play
    de-escalate every period and still drift up. So a course whose name says
    it steps down is never listed as escalatory, and the band delta only sorts
    the courses whose name is silent about direction (`probe_and_retreat`,
    `holding_pattern`, `drift_up`).

    This is the same self-contradiction the old mean-Goldstein tone verdict
    produced — two numbers about different things, presented as one claim.
    """
    escalatory = [
        sc for sc in scenarios
        if sc["kind"] in PRESSING_KINDS
        or (sc["delta_band"] > 0 and sc["kind"] not in CALMING_KINDS)
    ]
    calming = [
        sc for sc in scenarios
        if sc["kind"] in CALMING_KINDS
        or (sc["delta_band"] < 0 and sc["kind"] not in PRESSING_KINDS)
    ]
    escalatory.sort(key=lambda sc: -sc["likelihood"])
    calming.sort(key=lambda sc: -sc["likelihood"])
    return escalatory, calming


def classify_course(
    steps: list[dict[str, Any]], opening_band: int,
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> tuple[str, str]:
    """(kind, sentence) for an action course. Deterministic, from the steps.

    THE SHAPE IS FAMILY-BLIND, THE WORDS ARE NOT. Who pressed and who conceded
    is read by INDEX in the space (index 2 presses — escalate, or withhold;
    index 0 concedes — de-escalate, or commit), so the kind KEYS are shared
    across families and the sorting rule and the persisted payloads keep
    working. The sentence is the family's own (`family.kind_words`): an
    adversary's brinkmanship is an ally's "withhold, then recommit".
    """
    press, concede = space.actions[2], space.actions[0]
    esc_a = sum(1 for s in steps if s["action_a"] == press)
    esc_b = sum(1 for s in steps if s["action_b"] == press)
    de_a = sum(1 for s in steps if s["action_a"] == concede)
    de_b = sum(1 for s in steps if s["action_b"] == concede)
    end_band = int(steps[-1]["intensity_band"]) if steps else opening_band
    first_esc = next(
        (i for i, s in enumerate(steps) if press in (s["action_a"], s["action_b"])),
        None,
    )
    last_de = max(
        (i for i, s in enumerate(steps) if concede in (s["action_a"], s["action_b"])),
        default=None,
    )
    if esc_a and esc_b:
        if last_de is not None and first_esc is not None and last_de > first_esc:
            kind = "brinkmanship"
        else:
            kind = "mutual_escalation"
    elif esc_a or esc_b:
        if last_de is not None and first_esc is not None and last_de > first_esc:
            kind = "probe_and_retreat"
        else:
            kind = "one_sided_pressure"
    elif de_a or de_b:
        kind = "step_down"
    elif end_band > opening_band:
        kind = "drift_up"
    elif end_band < opening_band:
        kind = "drift_down"
    else:
        kind = "holding_pattern"
    return kind, family_module.kind_words(space.family, kind)[1]


def _presser(
    steps: list[dict[str, Any]], side_a: str, side_b: str,
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> str | None:
    press = space.actions[2]
    esc_a = sum(1 for s in steps if s["action_a"] == press)
    esc_b = sum(1 for s in steps if s["action_b"] == press)
    if esc_a and not esc_b:
        return side_a
    if esc_b and not esc_a:
        return side_b
    return None


def market_implications(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The measured market map of a course: per market, the median abnormal
    return across the steps that carry a non-thin measurement, ranked by
    magnitude. Empty when the course was never priced — stated, not filled."""
    pooled: dict[str, dict[str, Any]] = {}
    for step in steps:
        for row in step.get("market", []) or []:
            if row.get("thin") or int(row.get("n", 0)) < _MARKET_MIN:
                continue
            slot = pooled.setdefault(row["market_id"], {
                "market_id": row["market_id"],
                "market_name": row.get("market_name", row["market_id"]),
                "medians": [], "n": 0,
            })
            slot["medians"].append(float(row["median"]))
            slot["n"] += int(row["n"])
    out = []
    for slot in pooled.values():
        median = float(np.median(slot["medians"]))
        out.append({
            "market_id": slot["market_id"],
            "market_name": slot["market_name"],
            "median": round(median, 6),
            "n": slot["n"],
            "steps_priced": len(slot["medians"]),
        })
    out.sort(key=lambda r: -abs(r["median"]))
    return out


def scenarios_for(
    priced: dict[str, Any], *, dyad_id: str, dyad_name: str, opening_band: int, bands: int,
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> list[dict[str, Any]]:
    """Named scenarios from the priced paths — one per KIND of course.

    ONE SCENARIO PER KIND, not one per course (2026-08-15). A scenario used to
    be a single action course, which had two consequences the surface wore:
    `scenario_name` was `kind:dyad` and therefore NOT unique — the region's
    escalatory list could carry the same pair under the same name four times,
    each holding a slice of one distribution — and each slice's likelihood
    answered "how much mass is on this exact sequence", which is a question
    about the enumeration's resolution rather than about the world. With 1,645
    enumerated courses the top eight held 1.4% of the mass between them, so
    the page's own headline read "most likely course … at 1%".

    The likelihood is therefore pooled over EVERY enumerated course of that
    kind (`paths.enumerate_paths` returns `kinds`, computed before the reading
    cut), and the shares sum to one across kinds. The modal course is kept as
    `course`, with `courses` saying how many were pooled behind it. Payloads
    without `kinds` — anything frozen before this — fall back to pooling the
    kept paths, which is the old, smaller number.
    """
    side_a, side_b = split_sides(dyad_name)
    grouped: dict[str, dict[str, Any]] = {}
    for course_group in priced.get("kinds") or []:
        steps = course_group["steps"]
        kind, sentence = classify_course(steps, opening_band, space)
        grouped[kind] = {
            "kind": kind, "sentence": sentence,
            "likelihood": float(course_group["probability"]),
            "lead": float(course_group.get("lead_probability", course_group["probability"])),
            "courses": int(course_group.get("courses", 1)),
            "paths": [course_group],
        }
    if not grouped:
        for path in priced.get("paths", []):
            kind, sentence = classify_course(path["steps"], opening_band, space)
            slot = grouped.setdefault(kind, {
                "kind": kind, "sentence": sentence, "likelihood": 0.0,
                "lead": 0.0, "courses": 0, "paths": [],
            })
            slot["likelihood"] += float(path["probability"])
            slot["lead"] = max(slot["lead"], float(path["probability"]))
            slot["courses"] += 1
            slot["paths"].append(path)

    out = []
    for slot in sorted(grouped.values(), key=lambda g: (-g["likelihood"], g["kind"])):
        lead = max(slot["paths"], key=lambda p: (p["probability"], str(p["steps"])))
        steps = lead["steps"]
        kind = str(slot["kind"])
        likelihood = float(slot["likelihood"])
        end_band = int(steps[-1]["intensity_band"]) if steps else opening_band
        presser = _presser(steps, side_a, side_b, space)
        course = " → ".join(f"{s['action_a']}/{s['action_b']}" for s in steps)
        # Priced over EVERY pooled course's steps: same class of course, more
        # measured events behind the median, and the thinness bar unchanged.
        implications = market_implications(
            [step for p in slot["paths"] for step in p["steps"]]
        )
        ends = sorted({
            int(p["steps"][-1]["intensity_band"]) for p in slot["paths"] if p["steps"]
        })
        out.append({
            "scenario_name": f"{kind}:{dyad_id}",
            "kind": kind,
            # The family's own label for the kind — "free-riding" where an
            # adversary's course would read "one-sided pressure".
            "kind_label": family_module.kind_words(space.family, kind)[0],
            "family": space.family,
            "likelihood": round(likelihood, 4),
            "courses": int(slot["courses"]),
            "lead_likelihood": round(float(slot["lead"]), 4),
            "dyad_id": dyad_id,
            "dyad_name": dyad_name,
            "presser": presser,
            "course": course,
            "steps": steps,
            "opening_band": opening_band,
            "end_band": end_band,
            "end_band_range": [ends[0], ends[-1]] if ends else [end_band, end_band],
            "end_label": band_label(end_band, bands),
            "delta_band": end_band - opening_band,
            "beliefs_end": (
                {"a": steps[-1].get("belief_a"), "b": steps[-1].get("belief_b")}
                if steps else None
            ),
            "market_implications": implications[:4],
            "rationale": (
                f"{slot['sentence']}; pooled over {len(slot['paths'])} course"
                f"{'s' if len(slot['paths']) != 1 else ''} of play carrying "
                f"{likelihood:.0%} of the walk's own mass, the modal one "
                f"({course}) holding {float(lead['probability']):.0%}; intensity "
                f"moves {band_label(opening_band, bands)} → "
                f"{band_label(end_band, bands)} over {len(steps)} quarter"
                f"{'s' if len(steps) != 1 else ''}"
                + (
                    f"; {presser} is the side "
                    f"{'withholding' if space.family == 'ally' else 'pressing'}"
                    if presser else ""
                )
                + "."
            ),
        })
    return out


# ── one dyad, both concepts ────────────────────────────────────────────────


#: The band that holds the pair's OWN median departure: `intensity_band` maps
#: value/scale = 1.0 to the band whose lower edge is 1.0 — with the shipped
#: edges (0, 0.5, 1.0, 1.5, 2.0, 3.0) that is band 2. Above it, a departure is
#: sharper than this pair's usual friction; that is what the region ranks by.
TYPICAL_BAND = state_module.intensity_band(1.0, 1.0)


def _escalation_probability(marginal: list[dict[str, Any]], opening_band: int) -> float:
    """P(band at the horizon's end > opening band), off the fan. Trivially
    high for a pair opening at baseline — reported, not ranked by."""
    if not marginal:
        return 0.0
    dist = marginal[-1]["distribution"]
    return round(float(sum(dist[opening_band + 1:])), 4)


def _sharp_departure_probability(marginal: list[dict[str, Any]]) -> float:
    """P(band at the horizon's end > the pair's typical band) — sharper than
    its own usual friction. The ranking metric since 2026-08-15."""
    if not marginal:
        return 0.0
    dist = marginal[-1]["distribution"]
    return round(float(sum(dist[TYPICAL_BAND + 1:])), 4)


def _propensity(
    equilibrium: dict[str, Any], capability: int,
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> dict[str, list[float]]:
    """P(press | band, type) at the opening period — escalate for an
    adversary, withhold for an ally."""
    press = 2
    return {
        space.types[t]: [
            round(float(equilibrium["policy"][0, b, capability, t][press]), 4)
            for b in range(len(state_module.INTENSITY_EDGES))
        ]
        for t in range(len(space.types))
    }


def _matrix_at(
    equilibrium: dict[str, Any], band: int, capability: int, own: int,
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> dict[str, Any]:
    a, b = equilibrium["opening_matrices"]
    return {
        "a": [[round(float(v), 4) for v in row] for row in a[band, capability, own]],
        "b": [[round(float(v), 4) for v in row] for row in b[band, capability, own]],
        "actions": list(space.actions),
        "type": space.types[own],
        "mix_a": [round(float(v), 4) for v in equilibrium["policy"][0, band, capability, own]],
        "mix_b": [round(float(v), 4) for v in equilibrium["policy_b"][0, band, capability, own]],
        "value": round(float(equilibrium["value"][band, capability, own]), 4),
    }


def solve_dyad(
    context: dict[str, Any],
    *,
    region: str,
    dyad_id: str,
    payoffs: solve_module.Payoffs,
    graph_conn: Any,
    horizon: int = 4,
    solvers: tuple[str, ...] = ("qre", "lp"),
    ally_payoffs: solve_module.Payoffs | None = None,
) -> dict[str, Any] | None:
    """The full solved game for one dyad at its data-driven opening state.

    `context` is the games router's per-region context (table, kernel, joint,
    effects, model_trajectories, model_identity, coverage, as_of). Returns
    None when the dyad has no series in the region.

    THE FAMILY IS DECIDED FIRST, and everything after it is played in that
    family's space: `payoffs` is the adversary game's (the rival game plays it
    too, and says so); `ally_payoffs` is the burden-sharing game's, read from
    `models/game-ally-<region>.json` or the ally defaults when None.
    """
    own = [r for r in context["table"] if r["dyad_id"] == dyad_id]
    if not own:
        return None
    dyad_name = str(own[0]["dyad_name"])
    bands = len(state_module.INTENSITY_EDGES)

    scale = state_module.dyad_scale([float(r["intensity"]) for r in own])
    latest = max(own, key=lambda r: r["q"])
    band = state_module.intensity_band(float(latest["intensity"]), scale)
    # THREE READS, THREE SOURCES, NO OVERLAP — the 2026-08-15 contradiction.
    # `standing` is what the pair IS (curated RELATES_TO, sourced and dated);
    # `posture` is how the coded record READS lately (the coercive share of
    # its events); the band is where it sits against its OWN baseline. They
    # cannot disagree because they answer different questions — which the old
    # tone verdict could not say of itself, and so put "friendly" over a
    # declared rivalry.
    read = opening_module.posture(own)
    tone_now = read["tone"]
    standing_now = opening_module.standing(graph_conn, dyad_id, as_of=context["as_of"])
    capability = opening_module.capability_state(graph_conn, dyad_id)
    cap = int(capability["band"])

    # WHICH GAME, before any number. The family is read from what the pair IS
    # and how its record READS; the space it names decides the reading of the
    # record the beliefs are filtered from, the kernel, the payoff, the words.
    classification = family_module.classify(standing_now, read)
    space = family_module.space_for(classification["family"])
    if space.family == "ally":
        payoffs = ally_payoffs or solve_module.Payoffs(
            **context_module.fitted_payoffs(region, "ally")
        )
    joint = context_module.joint_for(context, space)
    beliefs = opening_module.filtered_beliefs(joint, dyad_id, payoffs, space=space)

    kernel, tilt = context_module.kernel_for(context, dyad_id, space)

    concepts: dict[str, Any] = {}
    for solver in solvers:
        equilibrium = solve_module.solve(
            kernel, payoffs, horizon=horizon, solver=solver, space=space
        )
        walked = paths_module.enumerate_paths(
            equilibrium, kernel, intensity=band, capability=cap,
            belief_a=float(beliefs["a"]), belief_b=float(beliefs["b"]), payoffs=payoffs,
            # The naming lives here; the walk does the counting. Injected so
            # the kind shares are pooled over EVERY enumerated course rather
            # than over the eight the reading cut keeps.
            classify=lambda steps: classify_course(steps, band, space)[0],
            space=space,
        )
        priced = pricing_module.price_paths(
            walked, context["effects"], as_of=context["as_of"], scale=scale or 1.0
        )
        marginal = paths_module.marginal_intensity(priced, horizon)
        concepts[solver] = {
            "concept": equilibrium["concept"],
            "nash_gap": equilibrium.get("nash_gap"),
            "marginal": marginal,
            "escalation_probability": _escalation_probability(marginal, band),
            "sharp_departure_probability": _sharp_departure_probability(marginal),
            "escalation_propensity": _propensity(equilibrium, cap, space),
            "paths": priced["paths"],
            "paths_enumerated": priced["paths_enumerated"],
            "retained_probability": priced["retained_probability"],
            "pricing": priced.get("pricing"),
            "opening_matrix": {
                space.types[t]: _matrix_at(equilibrium, band, cap, t, space)
                for t in range(len(space.types))
            },
            "scenarios": scenarios_for(
                priced, dyad_id=dyad_id, dyad_name=dyad_name,
                opening_band=band, bands=bands, space=space,
            ),
        }

    primary = solvers[0]
    solution = {
        "payload_version": PAYLOAD_VERSION,
        "region": region,
        "dyad_id": dyad_id,
        "dyad_name": dyad_name,
        "sides": list(split_sides(dyad_name)),
        "as_of": context["as_of"],
        "horizon": horizon,
        "bands": bands,
        "band_labels": [band_label(b, bands) for b in range(bands)],
        "band_semantics": BAND_SEMANTICS,
        "opening": {
            "intensity_band": band,
            "intensity_label": band_label(band, bands),
            "tone": tone_now,
            # WHAT IT IS (sourced) beside HOW IT READS (measured). `tone_label`
            # stays only as the raw sign of the mean Goldstein for readers who
            # want it; nothing on the surface may present it as the pair's
            # character. See opening.standing / opening.posture.
            "tone_label": tone_label(tone_now),
            "standing": standing_now,
            "posture": read,
            # WHICH GAME THIS PAIR PLAYS, from what it IS and how it BEHAVES.
            # The solver has one game — a crisis-bargaining model — and it was
            # being applied to treaty allies, which is how US-Japan came to
            # carry a 0.77 "escalation probability" and a modal course of
            # "probe and retreat". Naming the family is the honest half of the
            # fix; giving each family its own actions and fitted payoffs is
            # the other, and is not done yet.
            "family": classification,
            "latest_intensity": round(float(latest["intensity"]), 3),
            "scale": round(float(scale or 0.0), 3),
            "active_quarters": len([r for r in own if float(r["intensity"]) > 0]),
            "capability": capability,
            "beliefs": beliefs,
            "tilt": tilt,
        },
        "payoffs": {
            "discount": payoffs.discount, "cost_resolute": payoffs.cost_resolute,
            "cost_irresolute": payoffs.cost_irresolute, "stake": payoffs.stake,
            "audience": payoffs.audience,
        },
        # THE GAME PLAYED: its family, its actions in order (concede / hold /
        # press) and its private types, so a reader of the payload — and the
        # explanation below — never has to guess what index 2 meant.
        "space": {
            "family": space.family,
            "actions": list(space.actions),
            "types": list(space.types),
            "quads": dict(space.quads),
        },
        "primary_solver": primary,
        "concepts": concepts,
        "kernel": (
            context.get("coverage_by_space", {}).get("ally", context["coverage"])
            if space.family == "ally" else context["coverage"]
        ),
        "boundary_statement": BOUNDARY_STATEMENT,
    }
    solution["explanation"] = explain(solution)
    return solution


# ── the explanation, from the numbers ──────────────────────────────────────


def _pct(x: float) -> str:
    return f"{x:.0%}"


#: How a declared relation reads in a sentence. Curated types only — an
#: unmapped one prints its own name rather than being silently dropped.
_STANDING_WORDS = {
    "rivalry": "a declared rivalry",
    "alliance": "formal allies",
    "proxy": "a patron and its client",
    "membership": "members of a shared bloc",
    "trade": "a declared trade dependence",
}


def describe_standing(opening: dict[str, Any]) -> str:
    """What the pair IS, from the graph's sourced relations — never inferred
    from the wire's mood."""
    relations = (opening.get("standing") or {}).get("relations") or []
    if not relations:
        return "under no relation the archive has declared"
    parts = []
    for relation in relations[:2]:
        kind = str(relation["relation_type"])
        words = _STANDING_WORDS.get(kind, kind.replace("_", " "))
        since = str(relation.get("since") or "")[:4]
        parts.append(f"{words}{f' since {since}' if since else ''}")
    return " and ".join(parts)


def describe_posture(opening: dict[str, Any]) -> str:
    """How the CODED RECORD reads lately — a measurement with its sample
    stated, so it can never be mistaken for the sentence above."""
    read = opening.get("posture") or {}
    if not read or read.get("thin") or read.get("share") is None:
        return (
            f"too thinly covered lately to read a posture "
            f"({int(read.get('events', 0))} coded events in the last "
            f"{int(read.get('quarters', 0))} quarters)"
        )
    return (
        f"their record over the last {int(read['quarters'])} quarters is "
        f"{read['label']} ({_pct(float(read['share']))} of "
        f"{int(read['events'])} coded interactions were coercive, mean tone "
        f"{float(read['tone']):+.2f})"
        if read.get("tone") is not None else
        f"their record over the last {int(read['quarters'])} quarters is "
        f"{read['label']} ({_pct(float(read['share']))} of "
        f"{int(read['events'])} coded interactions were coercive)"
    )


def _mix_words(mix: list[float], actions: list[str] | None = None) -> str:
    names = list(actions or state_module.ACTIONS)
    parts = [f"{names[i]} {_pct(m)}" for i, m in enumerate(mix) if m >= 0.05]
    return ", ".join(parts) if parts else "no action above 5%"


def describe_kernel(tilt: dict[str, Any] | None) -> str:
    """Which instrument produced this pair's kernel, in one sentence.

    TWO SHAPES OF AUDIT, and a third case that is neither — extracted here
    because `explain` read `tilt["eta"]` straight out of the block and the
    dynamics audit has no eta (it names features, a bound and the gate it
    passed). Production answered `KeyError: 'eta'` on the first solve after
    the model shipped. One function knows the shapes; everything else asks it.
    """
    if tilt and "features" in tilt:
        measured = ", ".join(
            f"{name} {value:+.2f}" for name, value in sorted(tilt["features"].items())
        )
        horizon = tilt.get("ordering_horizon")
        return (
            f"This pair's kernel is its own: the counted table enters as an offset and "
            f"{tilt['model']} adds a residual read off its measured record ({measured}), "
            f"bounded at ±{tilt.get('max_tilt')} in log space. Held out, that model beat "
            f"the counted kernel this game used to solve over for every pair alike — "
            f"{tilt.get('gate', '')}."
            + (
                f" Its dyad-specific claim was measured to hold "
                f"{horizon} quarter ahead: further out the counted evidence "
                "is what the path rests on, so the later periods of the fan "
                "are less informed than the first."
                if horizon else ""
            )
        )
    if tilt:
        return (
            f"The learned layer tilts this pair's kernel: η = {float(tilt['eta']):+.3f} "
            f"(bounded by {tilt['scale']}) from the frozen model {tilt['model']}'s "
            "trajectory for this dyad — the counted kernel remains the evidence; the "
            "tilt is the model's claim about magnitude."
        )
    return (
        "No transition model ships for this region and no gated model trajectory is "
        "frozen for this pair, so the kernel is the region's counted table, untilted."
    )


def explain(solution: dict[str, Any]) -> list[str]:
    """Paragraphs a reader can check against the payload — every number in
    the prose is a field in the solution."""
    side_a, side_b = solution["sides"]
    op = solution["opening"]
    primary = solution["primary_solver"]
    lp = solution["concepts"].get("lp")
    qre = solution["concepts"].get("qre")
    horizon = solution["horizon"]
    space_info = solution.get("space") or {}
    actions = list(space_info.get("actions") or state_module.ACTIONS)
    type_one = str((space_info.get("types") or state_module.TYPES)[1])
    is_ally = space_info.get("family") == "ally"
    out: list[str] = []

    cap = op["capability"]
    bel = op["beliefs"]
    cap_words = (
        f"a CINC capability ratio of {float(cap.get('ratio', 0.5)):.2f} "
        f"(band {cap['band']} of {len(state_module.CAPABILITY_EDGES) - 1})"
        if cap.get("source") == "cinc"
        else "no CINC estimate for either side, so the middle capability band by default"
    )
    bel_words = (
        "beliefs filtered through the game's own Bayes rule from "
        f"{bel.get('quarters_observed', 0)} "
        f"observed quarters put {side_a} at {_pct(float(bel['a']))} and {side_b} at "
        f"{_pct(float(bel['b']))} likely resolute"
        if bel.get("source") == "bayes_filter"
        else "no observed actions to filter, so the prior on resolve is flat"
    )
    # THE SOURCED FACT FIRST. A declared relation is dated and cited; the
    # coded record is a measurement of the last four quarters; the band is a
    # departure from this pair's own baseline. Said in that order they nest,
    # which is what stops the page contradicting itself.
    tone_words = describe_standing(op) + ", and " + describe_posture(op)
    out.append(
        f"{side_a} and {side_b} are {tone_words}; they open at a {op['intensity_label']} "
        f"from their own baseline (latest quarterly departure {op['latest_intensity']:.2f} "
        f"against this pair's own scale of {op['scale']:.2f}; {op['active_quarters']} active "
        f"quarters on record), with {cap_words}; {bel_words}. Bands are relative friction, "
        "not absolute hostility."
    )

    # WHICH GAME, AND WHETHER IT IS THIS PAIR'S OWN. Said before any solved
    # number, because it changes what every number after it means: an
    # alliance's "escalation probability" is a statement about friction
    # between partners, and reading it as odds of conflict is the specific
    # error that made US-Japan look like a war on 2026-08-16.
    if op.get("family"):
        out.append(family_module.describe(op["family"]))

    if lp:
        m = lp["opening_matrix"][type_one]
        gap = lp.get("nash_gap") or {}
        out.append(
            f"The LP solution: at this opening state the welfare-maximal correlated equilibrium "
            f"(entropy-regularised at the fitted precision, so a tie between equilibria is kept "
            f"rather than resolved into certainty) has a {type_one} {side_a} playing "
            f"{_mix_words(m['mix_a'], actions)} and {side_b} playing "
            f"{_mix_words(m['mix_b'], actions)}. Across the {gap.get('stage_games', 0)} "
            "stage games of the "
            "backward induction it sat on a Nash point in "
            f"{_pct(float(gap.get('share_product_form', 0)))} "
            f"of them (mean nash_gap {float(gap.get('mean', 0)):.3f}, worst "
            f"{float(gap.get('max', 0)):.3f}) — "
            + (
                "the correlated play is essentially a Bayesian Nash equilibrium here."
                if float(gap.get("mean", 0)) < 0.02
                else "some states need a coordinating signal the archive does not "
                "model; read those cells as a direction."
            )
        )
    if qre and lp:
        modal_lp = int(np.argmax(lp["opening_matrix"][type_one]["mix_a"]))
        modal_qre = int(np.argmax(qre["opening_matrix"][type_one]["mix_a"]))
        agree = modal_lp == modal_qre
        out.append(
            f"The fitted quantal response, the concept the payoffs were estimated under, "
            f"{'agrees' if agree else 'disagrees'} on the modal opening action "
            f"({actions[modal_qre]} vs the LP's {actions[modal_lp]}); "
            f"its {'friction' if is_ally else 'escalation'} probability over "
            f"{horizon} quarters is "
            f"{_pct(qre['escalation_probability'])} "
            f"against the LP's {_pct(lp['escalation_probability'])}. Where they diverge, "
            "the QRE is "
            "the estimator's own play and the LP is the exact benchmark."
        )

    concept = solution["concepts"][primary]
    if concept["scenarios"]:
        top = concept["scenarios"][0]
        marg = concept["marginal"]
        fan_start = marg[0]["expected_band"] if marg else op["intensity_band"]
        fan_end = marg[-1]["expected_band"] if marg else op["intensity_band"]
        out.append(
            f"The most likely kind of course under the {primary.upper()} is "
            f"{top.get('kind_label') or top['kind'].replace('_', ' ')} at "
            f"{_pct(top['likelihood'])} of the "
            f"walk's own mass — {top.get('courses', 1)} enumerated course"
            f"{'s' if int(top.get('courses', 1)) != 1 else ''} the classifier reads "
            f"the same way, the modal one ({top['course']}, "
            f"{_pct(top.get('lead_likelihood', top['likelihood']))}) ending "
            f"{top['end_label']}"
            + (
                f" with {top['presser']} {'withholding' if is_ally else 'pressing'}"
                if top.get("presser") else ""
            )
            + f". The fan's expected band moves {fan_start:.2f} → {fan_end:.2f}; "
            f"the chance of a sharper-than-usual departure after {horizon} quarters is "
            f"{_pct(concept['sharp_departure_probability'])} (above the opening band: "
            f"{_pct(concept['escalation_probability'])}). {concept['paths_enumerated']} "
            "courses were "
            f"enumerated and the top {len(concept['paths'])} carry "
            f"{_pct(concept['retained_probability'])} of them."
        )
        if top["market_implications"]:
            moves = "; ".join(
                f"{r['market_name']} {r['median']:+.2%} (median abnormal return over "
                f"{r['n']} measured events)"
                for r in top["market_implications"][:3]
            )
            out.append(
                "Priced by the transmission engine, courses like this one have "
                f"historically moved: {moves}. "
                "Direction and size are measured per event and regime-gated; nothing here "
                "is asserted."
            )
        else:
            out.append(
                "The transmission engine holds too few measured effects for courses like "
                "this one at this "
                "intensity to name a market implication — that absence is reported rather "
                "than filled."
            )
    else:
        out.append(
            "No course of play cleared the retained-mass floor — the fan alone is the forecast."
        )

    out.append(describe_kernel(op.get("tilt")))

    k = solution["kernel"]
    out.append(
        f"Kernel: {k.get('measured', 0)} of {k.get('cells', 0)} transition cells measured "
        f"({_pct(float(k.get('share_measured', 0)))}) from {k.get('observations', 0)} "
        "dyad-quarters; "
        "payoffs are the region's fitted values (several at the estimator's bounds — a direction, "
        "not a point). " + BOUNDARY_STATEMENT
    )
    return out


# ── the region map ─────────────────────────────────────────────────────────


def region_map(
    context: dict[str, Any],
    *,
    region: str,
    payoffs: solve_module.Payoffs,
    graph_conn: Any,
    dyad_ids: list[str],
    horizon: int = 4,
    solvers: tuple[str, ...] = ("qre", "lp"),
) -> dict[str, Any]:
    """Every dyad solved; the region's future-event map aggregated from them.

    Returns the per-dyad solutions in full (the drill-in reads them) beside
    the region aggregate; the writer splits them across rows.
    """
    # The ally game's payoffs, resolved ONCE for the region: the fitted ally
    # artifact where one ships, the ally defaults otherwise. `solve_dyad`
    # picks them up only for pairs its family read makes allies.
    ally_payoffs = solve_module.Payoffs(**context_module.fitted_payoffs(region, "ally"))
    solutions: list[dict[str, Any]] = []
    for dyad_id in dyad_ids:
        solved = solve_dyad(
            context, region=region, dyad_id=dyad_id, payoffs=payoffs,
            graph_conn=graph_conn, horizon=horizon, solvers=solvers,
            ally_payoffs=ally_payoffs,
        )
        if solved is not None:
            solutions.append(solved)

    primary = solvers[0]
    bands = len(state_module.INTENSITY_EDGES)
    heat = []
    ranking = []
    all_scenarios: list[dict[str, Any]] = []
    for s in solutions:
        c = s["concepts"][primary]
        heat.append({
            "dyad_id": s["dyad_id"], "dyad_name": s["dyad_name"],
            "opening_band": s["opening"]["intensity_band"],
            "expected_band": [row["expected_band"] for row in c["marginal"]],
            "modal_band": [row["modal_band"] for row in c["marginal"]],
        })
        top = c["scenarios"][0] if c["scenarios"] else None
        ranking.append({
            "dyad_id": s["dyad_id"], "dyad_name": s["dyad_name"],
            "opening_band": s["opening"]["intensity_band"],
            "opening_label": s["opening"]["intensity_label"],
            "tone": s["opening"].get("tone"),
            "tone_label": s["opening"].get("tone_label"),
            # The chip's two honest halves: what the pair IS (sourced) and how
            # its record READS (measured). The old chip showed a mean-Goldstein
            # verdict, which called two thirds of every region "friendly".
            "standing": s["opening"].get("standing"),
            "posture": s["opening"].get("posture"),
            # WHICH GAME THIS PAIR PLAYS, so the row can say "ally" beside a
            # number that would otherwise read as odds of war.
            "family": s["opening"].get("family"),
            "space": (s.get("space") or {}).get("family"),
            "escalation_probability": c["escalation_probability"],
            "sharp_departure_probability": c["sharp_departure_probability"],
            "escalation_probability_qre": (
                s["concepts"]["qre"]["escalation_probability"] if "qre" in s["concepts"] else None
            ),
            "sharp_departure_probability_lp": (
                s["concepts"]["lp"]["sharp_departure_probability"]
                if "lp" in s["concepts"] else None
            ),
            "expected_end_band": (
                c["marginal"][-1]["expected_band"] if c["marginal"] else None
            ),
            "top_scenario": (
                {k: top.get(k) for k in (
                    "scenario_name", "kind", "kind_label", "likelihood", "courses",
                    "lead_likelihood", "course", "end_label", "presser",
                )}
                if top else None
            ),
            "nash_gap_mean": (c.get("nash_gap") or {}).get("mean"),
            # The absolute measure the ranking is sorted by, hoisted so the
            # surface can name the number it ordered on rather than implying
            # the departure probability did the ordering.
            "coercive_events": (s["opening"].get("posture") or {}).get("coercive"),
            "coercive_share": (s["opening"].get("posture") or {}).get("share"),
            "tilted": s["opening"]["tilt"] is not None,
            "capability_source": s["opening"]["capability"].get("source"),
            "beliefs_source": s["opening"]["beliefs"].get("source"),
        })
        for sc in c["scenarios"]:
            all_scenarios.append({
                **{k: sc[k] for k in (
                    "scenario_name", "kind", "likelihood", "courses", "lead_likelihood",
                    "dyad_id", "dyad_name", "presser", "course", "opening_band",
                    "end_band", "end_band_range", "end_label", "delta_band",
                    "market_implications", "rationale",
                )},
                "kind_label": sc.get("kind_label"),
                "tone_label": s["opening"].get("tone_label"),
                "posture": s["opening"].get("posture"),
                "standing": s["opening"].get("standing"),
                "family": s["opening"].get("family"),
            })
    # RANKED BY AN ABSOLUTE MEASURE, not by the game's own departure
    # probability — the fix for the finding that opened this audit.
    #
    # `sharp_departure_probability` is P(this pair leaves the band it opened
    # in), and the band is a departure from the PAIR'S OWN baseline. So a
    # quiet ally at the top of its own range outranks a war, which is exactly
    # what the surface showed: Russia–China, US–South Korea and US–Philippines
    # (all alliances) above US–China, which came last of twelve. Over 36
    # solved dyads the metric separated allies from rivalries by 0.0006.
    #
    # The ordering question is absolute — "which pairs carry coercion right
    # now" — so it is answered by a measured count, not by a relative
    # probability and not by a fitted model. Both were tried against it:
    # ranking these pairs by P(next quarter carries material coercion) fitted
    # on volume, coercive share, level and volatility scored AUC 0.8617 in
    # mena and 0.7587 in china against the plain count's 0.8722 and 0.7730.
    # The count won, so the count ships: nothing to train, nothing to drift,
    # and a number a reader can check against the events themselves.
    #
    # The departure probability stays in the payload beside it. The two answer
    # different questions and the surface says which is which.
    ranking.sort(key=lambda r: (
        -((r.get("posture") or {}).get("coercive") or 0),
        -(r["sharp_departure_probability"] or 0),
        r["dyad_id"],
    ))
    escalatory, calming = sort_scenarios(all_scenarios)

    # Region-level fan: dyad-average of the per-period marginals — a picture
    # of where the region's mass sits, period by period.
    region_fan = []
    for period in range(horizon):
        mass = np.zeros(bands)
        n = 0
        for s in solutions:
            m = s["concepts"][primary]["marginal"]
            if len(m) > period:
                mass += np.asarray(m[period]["distribution"], dtype=float)
                n += 1
        share = mass / n if n else mass
        region_fan.append({
            "period": period + 1,
            "distribution": [round(float(v), 4) for v in share],
            "expected_band": round(float(share @ np.arange(bands)), 3) if n else None,
        })

    gaps: list[float] = [
        float(g) for g in (
            (s["concepts"]["lp"].get("nash_gap") or {}).get("mean")
            for s in solutions if "lp" in s["concepts"]
        ) if g is not None
    ]
    aggregate = {
        "payload_version": PAYLOAD_VERSION,
        "region": region,
        "as_of": context["as_of"],
        "horizon": horizon,
        "bands": bands,
        "band_labels": [band_label(b, bands) for b in range(bands)],
        "band_semantics": BAND_SEMANTICS,
        "primary_solver": primary,
        "solvers": list(solvers),
        "concepts": {
            solver: solutions[0]["concepts"][solver]["concept"] for solver in solvers
        } if solutions else {},
        "payoffs": solutions[0]["payoffs"] if solutions else None,
        "kernel": context["coverage"],
        "model": context.get("model_identity"),
        "dyads_solved": len(solutions),
        "dyads_tilted": sum(
            1 for s in solutions if s["opening"]["tilt"] is not None
        ),
        "dyads_cinc": sum(
            1 for s in solutions if s["opening"]["capability"].get("source") == "cinc"
        ),
        "nash_gap": {
            "mean": round(float(np.mean(gaps)), 4) if gaps else None,
            "max": round(float(np.max(gaps)), 4) if gaps else None,
        },
        "ranking": ranking,
        "heat": heat,
        "region_fan": region_fan,
        "scenarios_escalatory": escalatory[:12],
        "scenarios_calming": calming[:12],
        "scenarios_all": sorted(all_scenarios, key=lambda sc: -sc["likelihood"])[:40],
        "boundary_statement": BOUNDARY_STATEMENT,
    }
    aggregate["explanation"] = explain_region(aggregate)
    return {"region": aggregate, "dyads": solutions}


def explain_region(aggregate: dict[str, Any]) -> list[str]:
    ranking = aggregate["ranking"]
    out: list[str] = []
    if not ranking:
        return ["No dyad in this region cleared the panel's modelling bar; nothing was solved."]
    hot = [r for r in ranking if (r["sharp_departure_probability"] or 0) >= 0.5]
    lead = ranking[0]
    out.append(
        f"{aggregate['dyads_solved']} pairs solved under the {aggregate['primary_solver'].upper()} "
        f"and the fitted QRE at their own opening states ({aggregate['dyads_cinc']} with "
        "CINC-measured "
        f"capability, {aggregate['dyads_tilted']} on a model-conditioned kernel). "
        # THE ORDERING IS ABSOLUTE and the sentence says so, because the
        # departure probability beside it is not: it is relative to each
        # pair's own baseline, which is why it once put three alliances above
        # a declared rivalry.
        f"Ranked by coercive events measured in the last four quarters, the pair carrying "
        f"the most is {lead['dyad_name']} "
        f"({lead.get('coercive_events') or 0} coercive events, "
        f"{describe_standing(lead)}, {describe_posture(lead)}, opening at a "
        f"{lead['opening_label']}"
        + (f", most likely course {lead['top_scenario']['kind'].replace('_', ' ')} at "
           f"{_pct(lead['top_scenario']['likelihood'])}" if lead.get("top_scenario") else "")
        + f"). Separately, {len(hot)} pairs carry at least even odds of a "
        f"sharper-than-usual departure from their OWN baseline after "
        f"{aggregate['horizon']} quarters — a different question, and the reason "
        "a quiet ally can score high on it."
    )
    # WHAT THE COUNT COUNTS, stated where it is used to order the region.
    #
    # A dyad's coercive events are those the archive coded with one side as
    # initiator and the other as target. GDELT's actor pairing does not
    # distinguish "A coerced B" from "A and B were both present in a coercive
    # event", and the difference shows: US-Australia's material-conflict
    # record for the year to 2026-08 is 25 events of CAMEO 190 ("use
    # conventional military force: Australia -> United States") and 13 of 193
    # ("fight with small arms"), which is co-involvement in third-party
    # operations, not an Australian attack. North Korea-South Korea's is 42 of
    # 194 and 14 of 150 ("exhibit military posture"), which is the real thing.
    #
    # The measure is still the best cross-pair ranker measured (it beat every
    # fitted alternative out of sample), and the standing chip carries the
    # correction — but a reader ranking allies against rivals deserves the
    # caveat in the same breath as the number, not in a footnote.
    if lead.get("standing", {}) and any(
        r.get("relation_type") == "alliance"
        for r in ((lead.get("standing") or {}).get("relations") or [])
    ):
        out.append(
            f"{lead['dyad_name']} is a declared alliance, which is worth "
            "reading against the measure rather than through it: a dyad's "
            "coercive events are those coded with one side as initiator and "
            "the other as target, and GDELT's actor pairing does not separate "
            "\"A coerced B\" from \"A and B were both in a coercive event\". "
            "Allies in the same operations accumulate the second kind. The "
            "count is still the best cross-pair ordering measured here — every "
            "fitted alternative scored worse out of sample — but the standing "
            "beside it is the correction, not decoration."
        )

    esc = aggregate["scenarios_escalatory"][:3]
    if esc:
        out.append(
            "The escalatory scenarios with the most mass: " + "; ".join(
                f"{sc['dyad_name']} — {sc['kind'].replace('_', ' ')} at {_pct(sc['likelihood'])}"
                + (f", pressed by {sc['presser']}" if sc.get("presser") else "")
                + (
                    f", historically moving {sc['market_implications'][0]['market_name']} "
                    f"{sc['market_implications'][0]['median']:+.2%}"
                    if sc.get("market_implications") else ""
                )
                for sc in esc
            ) + "."
        )
    calm = aggregate["scenarios_calming"][:2]
    if calm:
        out.append(
            "Where the game expects a step-down: " + "; ".join(
                f"{sc['dyad_name']} ({_pct(sc['likelihood'])})" for sc in calm
            ) + "."
        )
    gap = aggregate["nash_gap"]
    if gap.get("mean") is not None:
        out.append(
            f"Across the region the LP's mean nash_gap is {gap['mean']:.3f} (worst dyad "
            f"{gap['max']:.3f}): "
            + (
                "the welfare-maximal correlated play is, in effect, Bayesian Nash play."
                if gap["mean"] < 0.02
                else "several dyads' stage games call for coordination the archive "
                "does not model."
            )
        )
    k = aggregate["kernel"]
    out.append(
        f"Kernel {_pct(float(k.get('share_measured', 0)))} measured over "
        f"{k.get('observations', 0)} "
        f"dyad-quarters. " + BOUNDARY_STATEMENT
    )
    return out
