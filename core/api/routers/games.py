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

from core.games import bridge as bridge_module
from core.games import context as context_module
from core.games import duration as duration_module
from core.games import opening as opening_module
from core.games import paths as paths_module
from core.games import pricing as pricing_module
from core.games import solve as solve_module
from core.games import state as state_module
from core.models import panel as panel_module

router = APIRouter(tags=["games"])

_CACHE = context_module.CACHE


def _context(request: Request, region: str) -> dict[str, Any]:
    """The region context (kernel, joint actions, effects, model tilt), built
    by `core.games.context.build` — shared with the scenario map so the
    persisted solutions and the live counterfactuals see one archive."""
    try:
        return context_module.build(request.app.state.graph, region)
    except context_module.GraphNeeded as exc:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or str(exc)
        ) from exc
    except context_module.NothingToSolve as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _defaults(region: str) -> dict[str, float]:
    return context_module.fitted_payoffs(region)


def _panel() -> Any | None:
    """A panel connection, or None when Postgres is unset/unreachable — the
    scenario endpoints then solve live and say `persisted: false`."""
    from core import settings as settings_module
    from core.panel import pg_store

    try:
        return pg_store.connect(settings_module.load())
    except pg_store.PanelUnavailable:
        return None


def _live_region(request: Request, region: str, dyads: int) -> dict[str, Any]:
    from core.games import scenarios

    context = _context(request, region)
    payoffs = solve_module.Payoffs(**_defaults(region))
    return scenarios.region_map(
        context, region=region, payoffs=payoffs, graph_conn=request.app.state.graph,
        dyad_ids=context_module.active_dyads(context, dyads),
    )


@router.get("/games/region")
def region_scenarios(
    request: Request,
    region: str = "mena",
    live: bool = False,
    dyads: int = Query(8, ge=1, le=40),
) -> dict[str, Any]:
    """The region's future-event map: every active dyad solved under the LP
    correlated equilibrium (nash_gap reported) and the fitted QRE, courses
    named as scenarios with likelihoods, priced to the measured market map,
    explained from the numbers (core/games/scenarios.py).

    PERSISTED FIRST: `scripts/solve_games.py` (the boot's games step) writes
    the map to Postgres, and that is what every caller sees — same numbers
    for everyone, dated. `live=true` re-solves on request for `dyads` pairs
    (slower; flagged `persisted: false`), which is also the fallback when
    nothing is persisted yet.
    """
    from core.games import scenarios
    from core.panel import pg_store

    if not live:
        panel = _panel()
        if panel is not None:
            try:
                stored = pg_store.game_solution(
                    panel, region, scope="region", version=scenarios.PAYLOAD_VERSION
                )
            finally:
                panel.close()
            if stored is not None:
                return stored
    solved = _live_region(request, region, dyads)
    out = solved["region"]
    out["persisted"] = False
    out["note"] = (
        "solved on request — no persisted scenario map of this shape for the "
        f"region (payload version {scenarios.PAYLOAD_VERSION}); re-solve with "
        "scripts/solve_games.py, or a boot with GEOGRAPH_GAMES_ON_BOOT=1"
    )
    return out


@router.get("/games/dyad")
def dyad_solution(
    request: Request, dyad: str, region: str = "mena", live: bool = False
) -> dict[str, Any]:
    """One dyad's full solved game — both concepts, opening matrices, belief
    trajectories, priced courses, named scenarios and the explanation."""
    from core.games import scenarios
    from core.panel import pg_store

    if not live:
        panel = _panel()
        if panel is not None:
            try:
                stored = pg_store.game_solution(
                    panel, region, scope="dyad", dyad_id=dyad,
                    version=scenarios.PAYLOAD_VERSION,
                )
            finally:
                panel.close()
            if stored is not None:
                return stored
    context = _context(request, region)
    payoffs = solve_module.Payoffs(**_defaults(region))
    solved = scenarios.solve_dyad(
        context, region=region, dyad_id=dyad, payoffs=payoffs,
        graph_conn=request.app.state.graph,
    )
    if solved is None:
        raise HTTPException(status_code=404, detail=f"no series for {dyad} in {region}")
    solved["persisted"] = False
    return solved


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
        # The yield curve's answer to "how long do these crises last" —
        # measured tenors (front/belly/long) per event, honest about its own
        # absence. Existed since the duration module landed and was served
        # NOWHERE the games surface could reach; bonds are part of the ask.
        "duration": duration_module.report(
            context["effects"],
            pricing_module.dyad_of_event(context["effects"]),
        ),
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
    capability: int | None = Query(None, ge=0, le=2),
    belief_a: float | None = Query(None, ge=0.0, le=1.0),
    belief_b: float | None = Query(None, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """Re-solve for this dyad with any parameter overridden.

    A call with NO overrides is THE BASELINE: the fitted payoffs at the
    dyad's data-driven opening state (CINC capability, beliefs filtered from
    its observed actions, the gated model's kernel tilt where one is frozen)
    — the same construction as the frozen sequence forecast, and the
    response says `baseline: true`. Any override makes it a counterfactual
    and the response says that instead; the old behaviour stamped the
    counterfactual label on everything, untouched fitted defaults included,
    so the page could never show the model's own call. Bounds match the
    fit's own clips, so a caller cannot explore a region of the parameter
    space the estimator was never allowed to reach.
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
    # The opening state, from data. Beliefs are filtered with the FITTED
    # payoffs, deliberately: the baseline's opening state must stay pinned
    # while the cost sliders move, or the reference point drifts under the
    # lever and no comparison means anything.
    fitted_payoffs = solve_module.Payoffs(**fitted)
    data_capability = opening_module.capability_state(
        request.app.state.graph, dyad
    )
    data_beliefs = opening_module.filtered_beliefs(
        context["joint"], dyad, fitted_payoffs
    )
    effective_capability = (
        capability if capability is not None else int(data_capability["band"])
    )
    effective_belief_a = (
        belief_a if belief_a is not None else float(data_beliefs["a"])
    )
    effective_belief_b = (
        belief_b if belief_b is not None else float(data_beliefs["b"])
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
    if capability is not None and capability != int(data_capability["band"]):
        changed["capability"] = capability
    if belief_a is not None and abs(belief_a - float(data_beliefs["a"])) > 1e-9:
        changed["belief_a"] = belief_a
    if belief_b is not None and abs(belief_b - float(data_beliefs["b"])) > 1e-9:
        changed["belief_b"] = belief_b
    baseline = not changed

    scale = state_module.dyad_scale([float(r["intensity"]) for r in own])
    latest = max(own, key=lambda r: r["q"])
    band = state_module.intensity_band(float(latest["intensity"]), scale)

    # The ML→game bridge: a gated frozen trajectory tilts this dyad's kernel.
    eta = bridge_module.eta_from_trajectory(
        context["model_trajectories"].get(dyad, [])
    )
    kernel = bridge_module.tilted_kernel(context["kernel"], eta)
    tilt = bridge_module.audit(eta, context["model_identity"])

    equilibrium = solve_module.solve(kernel, payoffs, horizon=4)
    result = paths_module.enumerate_paths(
        equilibrium, kernel, intensity=band, capability=effective_capability,
        belief_a=effective_belief_a, belief_b=effective_belief_b, payoffs=payoffs,
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
        "capability": effective_capability,
        "beliefs": {"a": effective_belief_a, "b": effective_belief_b},
        # Where the opening state CAME FROM — measured or defaulted, the
        # reader sees which, and the page pins its baseline to these.
        "opening": {
            "intensity_band": band,
            "capability": data_capability,
            "beliefs": data_beliefs,
            "tilt": tilt,
        },
        "marginal": paths_module.marginal_intensity(priced, 4),
        "escalation_propensity": {
            state_module.TYPES[t]: [
                round(
                    float(
                        equilibrium["policy"][0, b, effective_capability, t][escalate]
                    ),
                    4,
                )
                for b in range(len(state_module.INTENSITY_EDGES))
            ]
            for t in range(len(state_module.TYPES))
        },
        **priced,
        "kernel": context["coverage"],
        "frozen": False,
        "baseline": baseline,
        "boundary_statement": (
            (
                "THE BASELINE: the fitted payoffs at this dyad's data-driven "
                "opening state — the same construction as the frozen sequence "
                "forecast. Move a lever to explore a counterfactual; the "
                "transition kernel is counted from the archive and is not "
                "adjustable."
            )
            if baseline
            else (
                "A COUNTERFACTUAL, not a forecast. Re-solved on request with "
                "the payoffs shown; nothing here is persisted, scored or "
                "comparable to the frozen sequence forecast. The transition "
                "kernel is counted from the archive and is not adjustable — "
                "what escalation has led to historically is evidence, not a "
                "setting."
            )
        ),
    }
