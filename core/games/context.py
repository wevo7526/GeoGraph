"""The per-region game context: kernel, joint actions, measured effects, and
the frozen model's trajectories — built once per process per region.

Extracted from the games router so the scenario map (`scenarios.py`,
`scripts/solve_games.py`) and the live counterfactual endpoint solve over
IDENTICAL inputs: same corpus table, same counted kernel, same effects, same
tilt. Two builders would drift; this is the one.
"""

from __future__ import annotations

import json
from typing import Any

from core.games import pricing as pricing_module
from core.games import solve as solve_module
from core.games import state as state_module
from core.games import transition as transition_module
from core.graph import kuzu_store
from core.models import dynamics as dynamics_module
from core.models import panel as panel_module
from core.wire import serving

#: Per-region context cache. Counting the kernel walks every dyad-quarter in
#: the archive (seconds); it cannot change under a single process anyway (the
#: corpus is immutable for the process's life, Kuzu is single-writer).
CACHE: dict[str, dict[str, Any]] = {}


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
    if table is None or joint is None:
        if conn is None:
            raise GraphNeeded(f"no corpus artifact for {region} and the graph is closed")
        table = panel_module.build(panel_module.dyad_event_rows(conn), region_pack=region)
        joint = transition_module.joint_actions(
            transition_module.event_rows(conn), quarter_of=panel_module.quarter_index
        )
    if not table:
        raise NothingToSolve(f"no modelable dyad in {region} — nothing to solve over")
    kernel, observed = transition_module.kernel(transition_module.count(table, joint))

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
        "joint": joint,
        "coverage": transition_module.coverage(observed),
        # Measured market effects live in the graph alone. An open graph adds
        # them; without one the game still solves, priced over no effects
        # rather than refusing — and it is a LIST, as every consumer iterates
        # (an empty dict here once meant "iterate the keys of nothing").
        "effects": (
            pricing_module.measured_effects(conn, region_pack=region) if conn is not None else []
        ),
        "model_trajectories": model_trajectories,
        "model_identity": model_identity,
        "graph_was_open": conn is not None,
        "as_of": max(row["date"] for row in table),
    }
    CACHE[region] = context
    return context


def load_dynamics(region: str) -> dict[str, Any] | None:
    """The region's transition model, or None if it does not ship or failed.

    A model whose gate did not pass is NOT loaded — the artifact records the
    failure so the run is auditable, and the game falls back to the counted
    kernel it has always had. That is the same rule the intensity model
    follows, for the same reason: an unchecked model and a checked-and-failed
    one must not be indistinguishable at inference.
    """
    from core.models import registry

    target = registry.MODELS_DIR / f"dynamics-{region}.json"
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


def kernel_for(context: dict[str, Any], dyad_id: str) -> tuple[Any, dict[str, Any] | None]:
    """(kernel, audit) — THIS PAIR's transition kernel.

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

    loaded = context.get("dynamics")
    if loaded is not None:
        facts = dyad_features(context, dyad_id)
        if facts is not None:
            model = loaded["model"]
            kernel = model.kernel_for(context["kernel"], facts["features"])
            return kernel, {
                "model": loaded["identity"],
                "features": {k: round(float(v), 4)
                             for k, v in facts["features"].items()},
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


def fitted_payoffs(region: str) -> dict[str, float]:
    """The fitted payoffs a solve starts from, so "no change" means the frozen
    forecast rather than an arbitrary guess. Verifies the artifact's hash."""
    from core.models import registry

    target = registry.MODELS_DIR / f"game-{region}.json"
    if not target.exists():
        base = solve_module.Payoffs()
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


def active_dyads(context: dict[str, Any], limit: int) -> list[str]:
    """The region's most active dyads, the panel's own ordering."""
    return [
        d["dyad_id"] for d in panel_module.dyad_summary(context["table"])[:limit]
    ]
