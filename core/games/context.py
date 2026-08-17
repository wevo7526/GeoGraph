"""The per-region game context: kernel, joint actions, measured effects, and
the frozen model's trajectories — built once per process per region.

Extracted from the games router so the scenario map (`scenarios.py`,
`scripts/solve_games.py`) and the live counterfactual endpoint solve over
IDENTICAL inputs: same corpus table, same counted kernel, same effects, same
tilt. Two builders would drift; this is the one.
"""

from __future__ import annotations

import json
import os
from typing import Any

from core import packs as packs_module
from core.games import family as family_module
from core.games import pricing as pricing_module
from core.games import solve as solve_module
from core.games import state as state_module
from core.games import transition as transition_module
from core.graph import kuzu_store
from core.models import dynamics as dynamics_module
from core.models import hostility as hostility_module
from core.models import panel as panel_module
from core.wire import serving

#: Per-region context cache. Counting the kernel walks every dyad-quarter in
#: the archive (seconds); it cannot change under a single process anyway (the
#: corpus is immutable for the process's life, Kuzu is single-writer).
#:
#: ONE REGION AT A TIME. A context carries `pricing.measured_effects` — every
#: measured event-market effect for its region — and AFFECTED passed a million
#: edges on 2026-08-16, so three cached regions is ~0.9 GB that GROWS every
#: time the study succeeds. The container reached 7.67 GB of a 7.45 GiB limit
#: and was OOM-killed inside the heavy jobs. Holding one region costs a
#: re-read when the games job moves to the next (it iterates serially, so that
#: is once per region per pass) and takes the same cache to ~0.3 GB.
CACHE: dict[str, dict[str, Any]] = {}

#: How many regions `CACHE` may hold. See above; 1 unless a reader is measured
#: to need more.
CACHE_REGIONS = int(os.getenv("GEOGRAPH_CONTEXT_CACHE_REGIONS", "1"))


class GraphNeeded(RuntimeError):
    """No corpus artifact for the region and no open graph to fall back to."""


class NothingToSolve(RuntimeError):
    """The region has no modelable dyad."""


def build(conn: Any, region: str) -> dict[str, Any]:
    cached = CACHE.get(region)
    # A context built while the graph was closed has no measured effects and
    # no model tilt; it must not outlive the boot. Rebuild once it opens.
    if cached is not None and (cached["graph_was_open"] or conn is None):
        return cached

    # THE CORPUS FIRST for the panel and the joint actions — the same order
    # `fit_game.py` reads, so the game solved here is solved over the same
    # table its committed payoffs were fitted to. The graph keeps two jobs it
    # is the only source for: the fallback when no artifact ships, and the
    # AFFECTED effects, which the transmission engine writes there alone.
    table = serving.table(region)
    joint = serving.joint_actions()
    # THE ALLY READING OF THE SAME RECORD, and its own counted kernel. Every
    # family's game is played over the same intensity bands, but what each
    # side DID in a quarter is read differently (contribution rather than
    # coercion), so the transition counts differ too. Both derive from the
    # retained quad counts, no second parse.
    joint_ally = serving.joint_actions(family_module.ALLY)
    joint_rival = serving.joint_actions(family_module.RIVAL)
    if table is None or joint is None:
        if conn is None:
            raise GraphNeeded(f"no corpus artifact for {region} and the graph is closed")
        table = panel_module.build(panel_module.dyad_event_rows(conn), region_pack=region)
        rows = transition_module.event_rows(conn)
        counts = transition_module.quad_counts(rows, quarter_of=panel_module.quarter_index)
        joint = transition_module.joint_from_counts(counts)
        joint_ally = transition_module.joint_from_counts(counts, family_module.ALLY)
        joint_rival = transition_module.joint_from_counts(counts, family_module.RIVAL)
    if not table:
        raise NothingToSolve(f"no modelable dyad in {region} — nothing to solve over")
    kernel, observed = transition_module.kernel(transition_module.count(table, joint))
    kernel_ally, observed_ally = transition_module.kernel(
        transition_module.count(table, joint_ally or {}, family_module.ALLY)
    )
    # The rival reading is the adversary's on renamed actions, so its counted
    # kernel is the adversary's table exactly; kept under its own key so a
    # reader never has to know that.
    kernel_rival, observed_rival = transition_module.kernel(
        transition_module.count(table, joint_rival or {}, family_module.RIVAL)
    )

    model_trajectories: dict[str, list[dict[str, Any]]] = {}
    model_identity: dict[str, Any] | None = None
    if conn is not None:
        frozen_model = kuzu_store.query(
            conn,
            "MATCH (f:Forecast) WHERE f.mode = 'model' AND f.region_pack = $region "
            "RETURN f.frozen_inputs_json AS frozen "
            "ORDER BY f.generated_at DESC, f.node_id LIMIT 1",
            {"region": region},
        )
        if frozen_model and frozen_model[0]["frozen"]:
            inputs = json.loads(str(frozen_model[0]["frozen"]))
            model_trajectories = {
                t["dyad_id"]: t["path"] for t in inputs.get("trajectories", [])
            }
            model_identity = inputs.get("model")

    context = {
        "region": region,
        "table": table,
        "kernel": kernel,
        "dynamics": load_dynamics(region),
        "dynamics_ally": load_dynamics(region, "ally"),
        "joint": joint,
        "coverage": transition_module.coverage(observed),
        # The ally space's own reading and kernel, beside the adversary's.
        "joint_by_space": {"ally": joint_ally or {}, "rival": joint_rival or {}},
        "kernel_by_space": {"ally": kernel_ally, "rival": kernel_rival},
        "coverage_by_space": {"ally": transition_module.coverage(observed_ally),
                              "rival": transition_module.coverage(observed_rival)},
        # Measured market effects live in the graph alone. An open graph adds
        # them; without one the game still solves, priced over no effects
        # rather than refusing — and it is a LIST, as every consumer iterates
        # (an empty dict here once meant "iterate the keys of nothing").
        "effects": (
            pricing_module.measured_effects(conn, region_pack=region) if conn is not None else []
        ),
        "model_trajectories": model_trajectories,
        "model_identity": model_identity,
        # P(this pair is in a militarised dispute), per dyad, from the trained
        # classifier — the read that decides ADVERSARY. Built once per region
        # because it walks the whole table; None when no model ships or its
        # gate failed, and `family.classify` then keeps its thresholds.
        "hostility": hostility_scores(region),
        "hostility_model": hostility_module.identity(),
        "graph_was_open": conn is not None,
        "as_of": max(row["date"] for row in table),
    }
    # Evict in insertion order — the games job walks regions serially, so the
    # oldest is the one furthest from being asked for again.
    while len(CACHE) >= CACHE_REGIONS:
        CACHE.pop(next(iter(CACHE)))
    CACHE[region] = context
    return context


def declared_rivalries(region: str) -> set[str]:
    """dyad_ids the pack declares a rivalry for. The classifier reads it as a
    FEATURE (weight +0.35), not as an override — a curated relation is
    evidence, and the model was fitted with it present."""
    from core.classifier import escalation

    out: set[str] = set()
    try:
        pack = packs_module.load(region)
    except Exception:  # noqa: BLE001 - a broken pack must not stop a solve
        return out
    for relation in pack.relations:
        if str(relation.get("relation_type")) == "rivalry":
            out.add(escalation.dyad_id(str(relation["a"]), str(relation["b"])))
    return out


def hostility_scores(region: str) -> dict[str, float]:
    """dyad_id → P(militarised dispute) over the pair's TRAILING TWELVE MONTHS.

    THE TRAINED READ OF WHAT A RELATIONSHIP IS. The thresholds it replaces
    scored 0.533 AUC on the held-out decade against this model's 0.847 — a
    coin flip against a usable classifier — and the coercive SHARE they keyed
    on carries a fitted weight of -0.01. Empty when no model ships or its gate
    failed, which leaves `family.classify` on its rules.

    THREE THINGS HAD TO MATCH THE FIT, and each was wrong once:

      * the FEATURES are built by `hostility.dyad_year_rows`, the function the
        fit uses. The first wiring rebuilt them from the quarterly panel,
        where `severe_share` and `fight_share` do not exist and arrived as
        zeros — the United States and Russia scored 0.41 served against 0.82
        trained, the whole difference between "rival" and "adversary".
      * the WINDOW is twelve months, not the latest calendar year. The fit's
        rows are complete years; the newest year in a live corpus is a partial
        one, and `log_events` carries the largest weight in the model (+0.57),
        so every pair was scored as though the world had gone quiet.
      * `declared_rivalry` is a FEATURE the fit was given, so it has to be
        supplied here too. Hardcoding it False cost every declared rivalry
        the +0.35 the model had learned to give it.
    """
    payload = hostility_module.load()
    if payload is None:
        return {}
    rivalries = declared_rivalries(region)
    rows = list(serving.iter_rows_of(region))
    if not rows:
        return {}
    newest = max(str(r.get("event_time") or "") for r in rows)[:10]
    if len(newest) < 10:
        return {}
    year, month, day = int(newest[:4]), int(newest[5:7]), newest[8:10]
    cutoff = f"{year - 1:04d}-{month:02d}-{day}"
    window = [r for r in rows if str(r.get("event_time") or "") >= cutoff]
    cells = hostility_module.dyad_year_rows(window)
    merged: dict[str, dict[str, float]] = {}
    for (dyad, _), cell in cells.items():
        into = merged.setdefault(dyad, {
            "events": 0.0, "coercion": 0.0, "severe": 0.0, "fight": 0.0,
            "goldstein_sum": 0.0, "goldstein_n": 0.0, "max_departure": 0.0,
        })
        for key, value in cell.items():
            if key == "max_departure":
                into[key] = max(into[key], value)
            else:
                into[key] += value
    return {
        dyad: round(hostility_module.score(
            payload, cell, declared_rivalry=dyad in rivalries), 4)
        for dyad, cell in merged.items()
    }


def load_dynamics(region: str, family: str = "adversary") -> dict[str, Any] | None:
    """The region's transition model, or None if it does not ship or failed —
    per FAMILY: `dynamics-<region>` for the adversary reading,
    `dynamics-ally-<region>` for the ally reading (commit / affirm / withhold).

    A model whose gate did not pass is NOT loaded — the artifact records the
    failure so the run is auditable, and the game falls back to the counted
    kernel it has always had. That is the same rule the intensity model
    follows, for the same reason: an unchecked model and a checked-and-failed
    one must not be indistinguishable at inference.
    """
    from core.models import registry

    stem = "dynamics" if family != "ally" else "dynamics-ally"
    target = registry.MODELS_DIR / f"{stem}-{region}.json"
    if not target.exists():
        return None
    with open(target, encoding="utf-8") as fh:
        artifact = json.load(fh)
    registry.verify_hash(artifact, what=target.name)
    if not artifact.get("gate_passed"):
        return None
    identity = f"{artifact['name']}@{artifact['hash']}"
    return {
        "model": dynamics_module.Dynamics.from_payload(
            artifact["model"], artifact=identity
        ),
        "identity": identity,
        "summary": artifact.get("gate_summary", ""),
    }


def dyad_features(context: dict[str, Any], dyad_id: str) -> dict[str, Any] | None:
    """This pair's own measured record, as of the table's last quarter.

    The same four facts `scripts/train_dynamics.py` fitted on, computed the
    same way — through `dynamics.row_features`, so the training window and the
    inference window cannot drift apart.
    """
    own = sorted(
        (r for r in context["table"] if r["dyad_id"] == dyad_id),
        key=lambda r: r["q"],
    )
    if not own:
        return None
    scale = state_module.dyad_scale([float(r["intensity"]) for r in own])
    band = state_module.intensity_band(float(own[-1]["intensity"]), scale)
    window = own[-dynamics_module.WINDOW_QUARTERS:]
    return {
        "band": band,
        "scale": scale,
        "features": dynamics_module.row_features(window, band, scale),
    }


def joint_for(context: dict[str, Any], space: family_module.ActionSpace) -> dict[Any, Any]:
    """The joint-action map in a family's space — the adversary's for the
    adversary and rival games, the ally reading for the ally game."""
    if space.family in ("ally", "rival"):
        return dict(context.get("joint_by_space", {}).get(space.family) or {})
    return dict(context["joint"])


def kernel_for(
    context: dict[str, Any], dyad_id: str,
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> tuple[Any, dict[str, Any] | None]:
    """(kernel, audit) — THIS PAIR's transition kernel, in the family's space.

    THE ALLY KERNEL HAS ITS OWN MODEL OR NONE. The adversary reading's
    per-pair residual is a function of (band, escalate/hold/de-escalate) and
    applying it to a kernel whose indices mean commit/affirm/withhold would be
    a shape match and a semantic lie — so the ally kernel takes
    `dynamics-ally-<region>` (trained on the ally reading, gated the same
    way) where it ships and passed, and the bridge's action-blind scalar tilt
    otherwise; the audit says which ran. The rival reading is the adversary's
    on renamed actions, so the adversary model applies to it as is.

    The counted kernel is one table for a whole region: US-Japan and North
    Korea-South Korea were solved over the same transitions, and at band 2 it
    returned an expected next band of 0.60 for every pair on the board. The
    dynamics model conditions that table on the pair's own record, so the
    solver receives a kernel that knows which pair it is for.

    Falls back to the ML->game BRIDGE (one bounded scalar per dyad, from the
    frozen intensity model's trajectory) where no dynamics artifact ships. The
    two never compound: the bridge's tilt was measured saturating at its bound
    for 5 of 12 china dyads, so where the dynamics model is available it is
    the instrument, and the audit line says which one ran.
    """
    from core.games import bridge as bridge_module

    if space.family == "ally":
        base = context.get("kernel_by_space", {}).get("ally")
        if base is None:
            base = context["kernel"]
        loaded_ally = context.get("dynamics_ally")
        if loaded_ally is not None:
            facts = dyad_features(context, dyad_id)
            if facts is not None:
                model = loaded_ally["model"]
                used = {*dynamics_module.FEATURES, *dynamics_module.INTERACTIONS}
                return model.kernel_for(base, facts["features"]), {
                    "model": loaded_ally["identity"],
                    "space": "ally",
                    "features": {k: round(float(v), 4)
                                 for k, v in facts["features"].items() if k in used},
                    "max_tilt": dynamics_module.MAX_TILT,
                    "gate": loaded_ally["summary"],
                    "ordering_horizon": dynamics_module.ORDERING_HORIZON_QUARTERS,
                    "method": (
                        "P(next) = softmax(log P_counted(next | band, a, b) + x.W) over "
                        "the ALLY reading (commit / affirm / withhold): the counted ally "
                        "kernel enters as an OFFSET and the residual is this pair's own "
                        "measured record over four quarters."
                    ),
                }
        eta = bridge_module.eta_from_trajectory(
            context.get("model_trajectories", {}).get(dyad_id, [])
        )
        audit = bridge_module.audit(eta, context.get("model_identity"))
        if audit is not None:
            audit = {**audit, "space": "ally",
                     "note": ("counted over the ally reading (commit / affirm / "
                              "withhold); no ally-space dynamics model ships for this "
                              "region, so the per-pair residual is not applied")}
        return bridge_module.tilted_kernel(base, eta), audit

    loaded = context.get("dynamics")
    if loaded is not None:
        facts = dyad_features(context, dyad_id)
        if facts is not None:
            model = loaded["model"]
            kernel = model.kernel_for(context["kernel"], facts["features"])
            # ONLY THE FEATURES THE MODEL ACTUALLY READS. `row_features`
            # computes `level` too — it is kept because the ablation reads it
            # — and listing it here put a number in the audit sentence that
            # the model does not use, which is the one thing this line exists
            # not to do.
            used = {*dynamics_module.FEATURES, *dynamics_module.INTERACTIONS}
            return kernel, {
                "model": loaded["identity"],
                "features": {k: round(float(v), 4)
                             for k, v in facts["features"].items() if k in used},
                "max_tilt": dynamics_module.MAX_TILT,
                "gate": loaded["summary"],
                "ordering_horizon": dynamics_module.ORDERING_HORIZON_QUARTERS,
                "method": (
                    "P(next) = softmax(log P_counted(next | band, a, b) + x.W): "
                    "the counted kernel enters as an OFFSET, so W = 0 is the "
                    "counted kernel exactly and the residual can only add what "
                    "counting does not know. x is this pair's own measured "
                    "record over four quarters; declared standing, capability "
                    "ratio and network centrality were measured and excluded "
                    "(they move within-dyad ordering by nothing or down)."
                ),
            }
    eta = bridge_module.eta_from_trajectory(
        context.get("model_trajectories", {}).get(dyad_id, [])
    )
    return (bridge_module.tilted_kernel(context["kernel"], eta),
            bridge_module.audit(eta, context.get("model_identity")))


def fitted_payoffs(region: str, family: str = "adversary") -> dict[str, float]:
    """The fitted payoffs a solve starts from, so "no change" means the frozen
    forecast rather than an arbitrary guess. Verifies the artifact's hash.

    Per FAMILY: `models/game-<region>.json` is the adversary game's fit (the
    rival game plays it too, and says so); `models/game-ally-<region>.json`
    is the ally game's, falling back to the ally defaults where no fit ships.
    """
    from core.models import registry

    space = family_module.space_for(family)
    stem = (f"game-{region}.json" if space.family == "adversary"
            else f"game-{space.family}-{region}.json")
    target = registry.MODELS_DIR / stem
    if not target.exists():
        base = solve_module.defaults_for(space)
        return {
            "discount": base.discount,
            "cost_resolute": base.cost_resolute,
            "cost_irresolute": base.cost_irresolute,
            "stake": base.stake,
            "audience": base.audience,
        }
    with open(target, encoding="utf-8") as fh:
        artifact = json.load(fh)
    registry.verify_hash(artifact, what=target.name)
    return dict(artifact["payoffs"])


def payoffs_fitted(region: str, family: str = "adversary") -> bool:
    """Whether a fitted artifact backs this family's payoffs for the region —
    the rival game ships on defaults (its declared pairs count a kernel 19%
    measured, under the fit's bar), and the payload should say so."""
    from core.models import registry

    space = family_module.space_for(family)
    stem = (f"game-{region}.json" if space.family == "adversary"
            else f"game-{space.family}-{region}.json")
    return (registry.MODELS_DIR / stem).exists()


def active_dyads(context: dict[str, Any], limit: int) -> list[str]:
    """The region's most active dyads, the panel's own ordering."""
    return [
        d["dyad_id"] for d in panel_module.dyad_summary(context["table"])[:limit]
    ]
