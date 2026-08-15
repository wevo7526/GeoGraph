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
from core.games import transition as transition_module
from core.graph import kuzu_store
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
