"""Counterfactuals: re-solve the game with the payoffs moved.

THE ONE THING A FITTED POLICY BUYS THAT A BLACK BOX CANNOT. "What if the
capability ratio closes." "What if this side is believed resolute." Those are
questions about a mechanism, and they are answerable here because the game
has parameters with meanings rather than weights with positions. A solve is
about 30ms, so it is genuinely interactive.

NOTHING HERE IS A FORECAST OF RECORD, and the payload says so on every
response. The frozen `sequence` Forecast is what gets scored later; this is
an exploration that persists nothing — the same posture as the what-if engine
in `reasoning.py`, which also computes on request and writes nothing into an
archive of things that happened. Without that separation a slider quietly
becomes a prediction nobody committed to.

The kernel is COUNTED FROM THE ARCHIVE and is not a parameter. A caller can
move what the sides want; they cannot move what escalation has historically
led to.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from core.games import paths as paths_module
from core.games import pricing as pricing_module
from core.games import solve as solve_module
from core.games import state as state_module
from core.games import transition as transition_module
from core.models import panel as panel_module
from core.wire import serving

router = APIRouter(tags=["games"])

#: Per-region kernel cache. Counting it walks every dyad-quarter in the
#: archive, which is seconds — far too slow for a slider, and it cannot
#: change under a single API process anyway (Kuzu is single-writer and the
#: API holds the lock).
_CACHE: dict[str, dict[str, Any]] = {}


def _context(request: Request, region: str) -> dict[str, Any]:
    conn = request.app.state.graph
    if region in _CACHE:
        return _CACHE[region]

    # THE CORPUS FIRST for the panel and the joint actions — the same order
    # `fit_game.py` reads, so the game a user explores here is solved over the
    # same table its committed payoffs were fitted to. The graph keeps two
    # jobs it is the only source for: the fallback when no artifact ships, and
    # the AFFECTED effects below, which the transmission engine writes there
    # and nothing else may.
    table = serving.table(region)
    joint = serving.joint_actions()
    if table is None or joint is None:
        if conn is None:
            raise HTTPException(
                status_code=503,
                detail=request.app.state.graph_error or "graph unavailable",
            )
        table = panel_module.build(
            panel_module.dyad_event_rows(conn), region_pack=region
        )
        joint = transition_module.joint_actions(
            transition_module.event_rows(conn), quarter_of=panel_module.quarter_index
        )
    if not table:
        raise HTTPException(
            status_code=404,
            detail=f"no modelable dyad in {region} — nothing to solve over",
        )
    kernel, observed = transition_module.kernel(
        transition_module.count(table, joint)
    )
    context = {
        "table": table,
        "kernel": kernel,
        "coverage": transition_module.coverage(observed),
        # Measured market effects live in the graph alone. An open graph adds
        # them; without one the game still solves, priced over no effects
        # rather than refusing the page.
        "effects": (
            pricing_module.measured_effects(conn, region_pack=region)
            if conn is not None else {}
        ),
        "as_of": max(row["date"] for row in table),
    }
    _CACHE[region] = context
    return context


def _defaults(region: str) -> dict[str, float]:
    """The fitted payoffs a slider starts from, so "no change" means the
    frozen forecast rather than an arbitrary guess."""
    import json

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
        return dict(json.load(fh)["payoffs"])


@router.get("/games/defaults")
def defaults(request: Request, region: str = "mena") -> dict[str, Any]:
    """The fitted starting point, the dyads worth asking about, and how much
    of the kernel is real — everything a control panel needs to open."""
    context = _context(request, region)
    return {
        "region": region,
        "payoffs": _defaults(region),
        "kernel": context["coverage"],
        "bands": list(state_module.INTENSITY_EDGES),
        "actions": list(state_module.ACTIONS),
        "dyads": [
            {k: d[k] for k in ("dyad_id", "dyad_name", "active_quarters")}
            for d in panel_module.dyad_summary(context["table"])[:20]
        ],
    }


@router.get("/games/explore")
def explore(
    request: Request,
    dyad: str,
    region: str = "mena",
    discount: float | None = Query(None, ge=0.5, le=0.99),
    cost_resolute: float | None = Query(None, ge=0.05, le=3.0),
    cost_irresolute: float | None = Query(None, ge=0.05, le=6.0),
    stake: float | None = Query(None, ge=0.1, le=3.0),
    audience: float | None = Query(None, ge=0.0, le=2.0),
    capability: int = Query(1, ge=0, le=2),
    belief_a: float = Query(0.5, ge=0.0, le=1.0),
    belief_b: float = Query(0.5, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """Re-solve for this dyad with any parameter overridden. NOT A FORECAST.

    Every parameter defaults to the region's fitted value, so a call with no
    overrides reproduces the frozen forecast's equilibrium — which is what
    makes a comparison meaningful. Bounds match the fit's own clips, so a
    caller cannot explore a region of the parameter space the estimator was
    never allowed to reach.
    """
    context = _context(request, region)
    if context["coverage"]["share_measured"] < 0.5:
        raise HTTPException(
            status_code=409,
            detail=(
                f"the {region} kernel is only "
                f"{context['coverage']['share_measured']:.0%} measured — solving "
                "over mostly-pooled transitions would describe the fallback, "
                "not the region"
            ),
        )

    own = [r for r in context["table"] if r["dyad_id"] == dyad]
    if not own:
        raise HTTPException(status_code=404, detail=f"no series for {dyad}")

    fitted = _defaults(region)
    payoffs = solve_module.Payoffs(
        discount=discount if discount is not None else fitted["discount"],
        cost_resolute=(
            cost_resolute if cost_resolute is not None else fitted["cost_resolute"]
        ),
        cost_irresolute=(
            cost_irresolute if cost_irresolute is not None else fitted["cost_irresolute"]
        ),
        stake=stake if stake is not None else fitted["stake"],
        audience=audience if audience is not None else fitted["audience"],
    )
    changed = {
        name: value
        for name, value in (
            ("discount", discount), ("cost_resolute", cost_resolute),
            ("cost_irresolute", cost_irresolute), ("stake", stake),
            ("audience", audience),
        )
        if value is not None and abs(value - fitted[name]) > 1e-9
    }

    scale = state_module.dyad_scale([float(r["intensity"]) for r in own])
    latest = max(own, key=lambda r: r["q"])
    band = state_module.intensity_band(float(latest["intensity"]), scale)

    equilibrium = solve_module.solve(context["kernel"], payoffs, horizon=4)
    result = paths_module.enumerate_paths(
        equilibrium, context["kernel"], intensity=band, capability=capability,
        belief_a=belief_a, belief_b=belief_b, payoffs=payoffs,
    )
    priced = pricing_module.price_paths(
        result, context["effects"], as_of=context["as_of"], scale=scale or 1.0
    )
    escalate = state_module.ACTIONS.index("escalate")
    return {
        "region": region,
        "dyad_id": dyad,
        "dyad_name": own[0]["dyad_name"],
        "opening_band": band,
        "payoffs": {
            "discount": payoffs.discount,
            "cost_resolute": payoffs.cost_resolute,
            "cost_irresolute": payoffs.cost_irresolute,
            "stake": payoffs.stake,
            "audience": payoffs.audience,
        },
        "changed": changed,
        "capability": capability,
        "beliefs": {"a": belief_a, "b": belief_b},
        "marginal": paths_module.marginal_intensity(priced, 4),
        "escalation_propensity": {
            state_module.TYPES[t]: [
                round(float(equilibrium["policy"][0, b, capability, t][escalate]), 4)
                for b in range(len(state_module.INTENSITY_EDGES))
            ]
            for t in range(len(state_module.TYPES))
        },
        **priced,
        "kernel": context["coverage"],
        # THE LABEL, on every response. A counterfactual is not scored, not
        # frozen, and not comparable to a call anyone committed to.
        "frozen": False,
        "boundary_statement": (
            "A COUNTERFACTUAL, not a forecast. Re-solved on request with the "
            "payoffs shown; nothing here is persisted, scored or comparable to "
            "the frozen sequence forecast. The transition kernel is counted "
            "from the archive and is not adjustable — what escalation has led "
            "to historically is evidence, not a setting."
        ),
    }
