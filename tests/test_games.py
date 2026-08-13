"""The game layer: the state space, the counted kernel, the equilibrium, and
the path distribution.

Most of these pin a bug that was actually in the code. A game is a machine
for producing plausible-looking output from wrong assumptions, so the tests
that matter are the ones asserting the MECHANISM points the right way, not
that a number came out.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pytest

from core.games import paths, solve, state, transition


def _realistic_kernel() -> np.ndarray:
    """Escalation pushes intensity up, de-escalation pulls it down.

    A uniform-random kernel cannot test any of this: if actions do not move
    the state, no policy can prefer one, and a broken payoff looks fine.
    """
    bands, actions = len(state.INTENSITY_EDGES), len(state.ACTIONS)
    kern = np.zeros((bands, actions, actions, bands))
    for x in range(bands):
        for a in range(actions):
            for b in range(actions):
                centre = np.clip(x + (a - 1) + (b - 1), 0, bands - 1)
                row = np.exp(-0.8 * np.abs(np.arange(bands) - centre))
                kern[x, a, b] = row / row.sum()
    return kern


# ── the state space ──────────────────────────────────────────────────────────


def test_the_action_axis_increases_toward_conflict():
    # Goldstein runs NEGATIVE for conflictual, so the raw codebook means order
    # escalation lowest. Used unflipped in a "who pressed harder" term they
    # reward backing down, which is what the first implementation did.
    positions = state.action_positions()
    order = {name: value for name, value in zip(state.ACTIONS, positions, strict=True)}
    assert order["escalate"] > order["hold"] > order["de-escalate"]


def test_the_action_spacing_is_not_uniform():
    # The whole reason to read spacing off Goldstein rather than assume
    # (-1, 0, +1): the step from talking to shooting is not the step from
    # cooperating to talking, and the codebook says so.
    positions = state.action_positions()
    gaps = np.diff(positions)
    assert not np.isclose(gaps[0], gaps[1], atol=0.05), (
        f"spacing {positions} is effectively uniform — the codebook is not being read"
    )


def test_intensity_is_relative_to_the_dyads_own_scale():
    # The same absolute departure is routine for a rivalry and a rupture for a
    # quiet pair. A shared absolute grid would make a state mean different
    # things for different dyads — the error that scored 0.92 pooled while
    # ranking each dyad's own quarters backwards.
    assert state.intensity_band(10.0, scale=10.0) < state.intensity_band(10.0, scale=2.0)
    # A dyad with no history has nothing to be a departure from.
    assert state.intensity_band(10.0, scale=0.0) == 0


def test_the_dyad_scale_ignores_quiet_quarters():
    # Quiet quarters are the majority of any dyad's history; including them
    # would drag the yardstick to nothing.
    assert state.dyad_scale([0.0, 0.0, 0.0, 4.0, 6.0]) == pytest.approx(5.0)
    assert state.dyad_scale([0.0, 0.0]) == 0.0


def test_the_state_space_stays_inside_its_solve_budget():
    # Indirect inference needs thousands of solves. The discretisation is a
    # budget decision and this is the budget.
    assert state.state_count() <= 500


# ── the counted kernel ───────────────────────────────────────────────────────


def test_material_conflict_outranks_a_pile_of_talk():
    # A quarter with one shooting incident and forty statements is an
    # escalation; a majority vote over event counts would call it cooperation
    # because talk is cheap and frequent.
    assert transition.action_from_quads(
        {"material_conflict": 1, "verbal_cooperation": 40}
    ) == "escalate"
    assert transition.action_from_quads({"verbal_cooperation": 3}) == "de-escalate"
    assert transition.action_from_quads({}) == "hold"


def test_the_joint_action_is_read_per_side_not_per_dyad():
    # A dyad-quarter is not one action, it is two. Reading the dyad's events
    # in aggregate would make an exchange where one side shoots and the other
    # sues for peace indistinguishable from both doing each.
    rows = [
        {"dyad_id": "d", "actor_a": "A", "actor_b": "B", "initiator": "A",
         "event_time": "2020-02-15", "quad_class": "material_conflict"},
        {"dyad_id": "d", "actor_a": "A", "actor_b": "B", "initiator": "B",
         "event_time": "2020-03-15", "quad_class": "verbal_cooperation"},
    ]
    actions = transition.joint_actions(rows, quarter_of=lambda d: int(d[:4]) * 4)
    assert actions[("d", 2020 * 4)] == ("escalate", "de-escalate")


def test_a_side_that_initiated_nothing_is_holding_not_conceding():
    # Absence of initiative is not restraint, but it is the only reading the
    # record supports — inventing a de-escalation from silence would put a
    # decision in the archive nobody observed.
    rows = [
        {"dyad_id": "d", "actor_a": "A", "actor_b": "B", "initiator": "A",
         "event_time": "2020-02-15", "quad_class": "material_conflict"},
    ]
    actions = transition.joint_actions(rows, quarter_of=lambda d: int(d[:4]) * 4)
    assert actions[("d", 2020 * 4)] == ("escalate", "hold")


def test_a_thin_cell_falls_back_to_the_pool_and_says_so():
    bands = len(state.INTENSITY_EDGES)
    counts = {
        (2, 2, 2): np.array([0.0, 0.0, 5.0, 30.0, 0.0, 0.0]),   # well observed
        (2, 0, 0): np.array([2.0, 0.0, 0.0, 0.0, 0.0, 0.0]),    # two observations
    }
    kern, observed = transition.kernel(counts)
    assert kern.shape == (bands, 3, 3, bands)
    # Every cell is a proper distribution, measured or not — the solver can
    # never be handed a row that does not sum to one.
    assert np.allclose(kern.sum(axis=3), 1.0)
    report = transition.coverage(observed)
    assert report["measured"] == 1
    assert report["fallback"] == report["cells"] - 1
    # The thin cell took the pooled row rather than its own two observations.
    assert not np.allclose(kern[2, 0, 0], [1.0, 0, 0, 0, 0, 0])


def test_a_band_the_archive_never_left_is_uniform_not_invented():
    kern, _observed = transition.kernel({})
    assert np.allclose(kern[0, 0, 0], 1.0 / len(state.INTENSITY_EDGES))


# ── the equilibrium ──────────────────────────────────────────────────────────


def test_the_resolute_type_escalates_more_than_the_irresolute_one():
    # THE MECHANISM. This failed in the first implementation because the cost
    # term read current intensity only, so it was identical across actions,
    # shifted both types' payoffs uniformly, and could not influence which
    # action either picked — the solver had the IRRESOLUTE type escalating
    # more. Escalating has to cost you now, in proportion to what it costs you.
    equilibrium = solve.solve(_realistic_kernel(), solve.Payoffs(), horizon=4)
    escalate = state.ACTIONS.index("escalate")
    for band in (1, 3, 4):
        irresolute = equilibrium["policy"][0, band, 1, 0][escalate]
        resolute = equilibrium["policy"][0, band, 1, 1][escalate]
        assert resolute > irresolute, f"band {band}: {resolute} !> {irresolute}"


def test_escalation_is_evidence_of_resolve():
    payoffs = solve.Payoffs()
    escalate = state.ACTIONS.index("escalate")
    back_down = state.ACTIONS.index("de-escalate")
    assert solve.posterior(0.5, escalate, payoffs) > 0.5
    assert solve.posterior(0.5, back_down, payoffs) < 0.5
    # Certainty is absorbing — a side already judged resolute stays so.
    assert solve.posterior(1.0, back_down, payoffs) == pytest.approx(1.0)


def test_every_policy_row_is_a_distribution():
    equilibrium = solve.solve(_realistic_kernel(), solve.Payoffs(), horizon=4)
    assert np.allclose(equilibrium["policy"].sum(axis=-1), 1.0)


def test_the_solve_is_fast_enough_for_indirect_inference():
    # Thousands of solves to fit five parameters. A solver that takes a second
    # makes the estimation in docs/game-spec.md section 2.1 impossible.
    kernel = _realistic_kernel()
    started = time.perf_counter()
    for _ in range(20):
        solve.solve(kernel, solve.Payoffs(), horizon=4)
    per_solve = (time.perf_counter() - started) / 20
    assert per_solve < 0.25, f"{per_solve:.3f}s per solve is too slow to fit"


def test_the_equilibrium_is_deterministic():
    kernel = _realistic_kernel()
    first = solve.solve(kernel, solve.Payoffs(), horizon=4)
    second = solve.solve(kernel, solve.Payoffs(), horizon=4)
    assert np.array_equal(first["policy"], second["policy"])


# ── the path distribution ────────────────────────────────────────────────────


def _paths() -> dict[str, Any]:
    kernel = _realistic_kernel()
    equilibrium = solve.solve(kernel, solve.Payoffs(), horizon=4)
    return paths.enumerate_paths(
        equilibrium, kernel, intensity=3, capability=1,
        belief_a=0.5, belief_b=0.5, payoffs=solve.Payoffs(),
    )


def test_paths_survive_their_own_threshold():
    # The threshold is a share of the retained distribution, not a raw weight.
    # Compared against an absolute floor, a path through four periods of
    # three-way mixtures scores ~1e-4 and EVERY path was discarded — the
    # forecast came back empty.
    result = _paths()
    assert result["paths"], "no path survived — the threshold is absolute again"
    assert result["retained_probability"] > 0.0


def test_the_forecast_is_a_distribution_not_a_path():
    result = _paths()
    assert len(result["paths"]) > 1
    assert sum(p["probability"] for p in result["paths"]) <= 1.0 + 1e-9
    # What the top N leaves out is stated rather than implied.
    assert 0.0 < result["retained_probability"] <= 1.0


def test_every_step_carries_a_quad_class_the_archive_codes():
    coded = set(state.ACTION_QUAD.values())
    for path in _paths()["paths"]:
        for step in path["steps"]:
            assert step["quad"] in coded


def test_the_marginal_fan_sums_to_one_per_period():
    result = _paths()
    for row in paths.marginal_intensity(result, 4):
        assert sum(row["distribution"]) == pytest.approx(1.0, abs=1e-3)
        assert 0 <= row["modal_band"] < len(state.INTENSITY_EDGES)


def test_no_price_is_attached_here():
    # The game emits EVENTS. Prices come from measured AFFECTED edges
    # downstream, so nothing on a surface carries a model-originated price.
    for path in _paths()["paths"]:
        for step in path["steps"]:
            assert not {"price", "return", "market", "effect"} & set(step)


# ── indirect inference ───────────────────────────────────────────────────────


def test_action_frequencies_are_shares_per_band():
    from core.games import estimate

    bands = [0, 0, 3, 3]
    actions = [(2, 2), (2, 1), (0, 0), (0, 1)]
    freq = estimate.action_frequencies(bands, actions)
    grid = freq.reshape(len(state.INTENSITY_EDGES), len(state.ACTIONS))
    assert np.allclose(grid.sum(axis=1), 1.0)
    # Band 0 saw three escalations and one hold across the two sides.
    assert grid[0][state.ACTIONS.index("escalate")] == pytest.approx(0.75)
    # A band never observed is uniform, not zero — absence of evidence must
    # not read as "never escalates".
    assert np.allclose(grid[5], 1.0 / len(state.ACTIONS))


def test_the_payoffs_move_the_frequencies():
    # THE IDENTIFICATION TEST, and the reason this moment replaced the decay.
    # Sweeping the whole parameter space moved the ridge's decay only between
    # 46.4% and 50.4% — no leverage, because the measured kernel dominates how
    # intensity evolves. What the payoffs control is which ACTION is played.
    from core.games import estimate

    kernel = _realistic_kernel()
    seen = []
    for payoffs in (
        solve.Payoffs(cost_resolute=0.05, cost_irresolute=0.2),   # cheap war
        solve.Payoffs(),
        solve.Payoffs(cost_resolute=2.0, cost_irresolute=5.0),    # costly war
    ):
        equilibrium = solve.solve(kernel, payoffs, horizon=4)
        rows = estimate.simulate(equilibrium, kernel, payoffs, seed=7, dyads=20, quarters=40)
        seen.append(estimate.simulated_frequencies(rows))
    escalate = state.ACTIONS.index("escalate")
    stride = len(state.ACTIONS)
    bands = range(len(state.INTENSITY_EDGES))
    cheap = float(np.mean([seen[0][b * stride + escalate] for b in bands]))
    costly = float(np.mean([seen[2][b * stride + escalate] for b in bands]))
    assert cheap > costly + 0.05, f"frequencies carry no signal: {cheap} vs {costly}"


def test_simulation_is_reproducible_from_its_seed():
    # Frozen forecasts have to be recomputable, and a simulation that needs a
    # clock could not be.
    from core.games import estimate

    kernel = _realistic_kernel()
    equilibrium = solve.solve(kernel, solve.Payoffs(), horizon=4)
    first = estimate.simulate(equilibrium, kernel, solve.Payoffs(), seed=11, dyads=10, quarters=20)
    second = estimate.simulate(equilibrium, kernel, solve.Payoffs(), seed=11, dyads=10, quarters=20)
    assert [r["band"] for r in first] == [r["band"] for r in second]
    other = estimate.simulate(equilibrium, kernel, solve.Payoffs(), seed=12, dyads=10, quarters=20)
    assert [r["band"] for r in first] != [r["band"] for r in other]


def test_the_type_ordering_cannot_be_relabelled_by_the_optimiser():
    # Without the constraint, a fit can swap which type is resolute and report
    # a perfect distance with the meanings inverted.
    from core.games import estimate

    payoffs = estimate._theta_to_payoffs(
        np.array([0.9, 1.5, -0.8, 1.0, 0.3]), solve.Payoffs()
    )
    assert payoffs.cost_resolute < payoffs.cost_irresolute


def test_a_simulation_that_cannot_be_fitted_is_penalised_not_skipped():
    from core.games import estimate

    bad = np.array([np.nan, 1.0, 1.0, 1.0])
    assert estimate.distance(bad, np.ones(4)) >= 1e6


# ── pricing: the path that had never returned a row ─────────────────────────


def _effect(event: str, quad: str, magnitude: float, market: str, ret: float,
            when: str = "2019-06-15") -> dict[str, Any]:
    return {
        "event_id": event, "event_time": when, "quad_class": quad,
        "magnitude": magnitude, "market_id": market,
        "market_name": market.replace("market:", "").upper(),
        "abnormal_return": ret, "window": "0_1", "resolution": "day",
    }


def test_a_step_is_priced_from_measured_effects():
    # Built against an archive whose AFFECTED table was empty, so until this
    # test existed the branch that actually RETURNS market rows had never run.
    from core.games import pricing

    effects = [
        _effect(f"e{i}", "material_conflict", 9.0, "market:brent", 0.01 + i * 0.002)
        for i in range(12)
    ]
    index = pricing.build_index(effects, as_of="2019-12-31", scale=9.0)
    assert index, "no cell was indexed — the regime gate rejected everything"
    band = state.intensity_band(9.0, 9.0)
    rows = pricing.price_step(
        {"quad": "material_conflict", "intensity_band": band}, index,
        {"market:brent": "Brent"},
    )
    assert rows, "the priced branch still returns nothing"
    row = rows[0]
    assert row["n"] == 12 and not row["thin"] and row["match"] == "quad+band"
    assert row["min"] <= row["median"] <= row["max"]


def test_a_thin_cell_loosens_the_match_and_says_so():
    from core.games import pricing

    effects = (
        [_effect("a", "material_conflict", 9.0, "market:brent", 0.02)]
        + [_effect(f"b{i}", "material_conflict", 1.0, "market:brent", -0.01)
           for i in range(10)]
    )
    index = pricing.build_index(effects, as_of="2019-12-31", scale=9.0)
    rows = pricing.price_step(
        {"quad": "material_conflict", "intensity_band": state.intensity_band(9.0, 9.0)},
        index, {},
    )
    # One measurement in the exact cell is not a market implication, so the
    # match widens to the quad and the row admits it did.
    assert rows[0]["match"] == "quad only"
    assert rows[0]["n"] == 11


def test_pricing_refuses_evidence_from_another_regime():
    from core.games import pricing

    # Bretton Woods evidence for a modern question — the same admissibility
    # gate the analogy engine applies, not a similarity score.
    old = [_effect(f"e{i}", "material_conflict", 9.0, "market:brent", 0.02,
                   when="1960-06-15") for i in range(12)]
    assert pricing.build_index(old, as_of="2019-12-31", scale=9.0) == {}


def test_paths_without_measured_effects_say_so_rather_than_pricing_nothing():
    from core.games import pricing

    priced = pricing.price_paths(
        {"paths": [{"probability": 1.0, "steps": [
            {"period": 1, "quad": "material_conflict", "intensity_band": 3}]}]},
        [], as_of="2019-12-31", scale=9.0,
    )
    assert priced["pricing"]["measurements"] == 0
    assert priced["pricing"]["note"], "an empty index must state itself"
    assert priced["paths"][0]["steps"][0]["market"] == []


# ── counterfactuals ──────────────────────────────────────────────────────────


def _game_app(tmp_path: Any, monkeypatch: Any) -> Any:
    """A graph with one dyad rich enough to solve over."""
    import importlib.util
    from pathlib import Path

    from core import packs
    from core.graph import kuzu_store

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("seed_pack", root / "scripts" / "seed_pack.py")
    assert spec and spec.loader
    seed_pack = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_pack)

    path = tmp_path / "games.kuzu"
    conn = kuzu_store.connect(path)
    try:
        seed_pack.seed(conn, packs.load("mena"))
        kuzu_store.merge_nodes(conn, "Dyad", [{
            "node_id": "dyad:g", "name": "Game Dyad", "ewma_baseline": -5.0,
            "actor_a_id": "actor:cow-630", "actor_b_id": "actor:cow-645",
        }])
        events = [{
            "node_id": f"event:g-{q}", "name": f"g{q}",
            "event_time": f"{1990 + q // 4}-{(q % 4) * 3 + 1:02d}-15",
            "action_cameo_code": "190", "goldstein": -8.0,
            "quad_class": "material_conflict" if q % 3 else "verbal_cooperation",
            "region_pack": "mena", "fidelity_tier": "deep_structured",
            "temporal_resolution": "day", "source_scale": "cow_hostility",
            "escalation_direction": "escalating",
            "escalation_magnitude": 9.0 if q % 3 == 0 else 2.0,
            "escalation_baseline": -5.0,
        } for q in range(60)]
        kuzu_store.merge_nodes(conn, "Event", events)
        kuzu_store.merge_edges(conn, "OF_DYAD", [
            {"src": e["node_id"], "dst": "dyad:g"} for e in events
        ])
        kuzu_store.merge_edges(conn, "INITIATED_BY", [
            {"src": e["node_id"], "dst": "actor:cow-630", "source_id": "source:cow-mid"}
            for e in events
        ])
    finally:
        kuzu_store.close(conn)

    monkeypatch.setenv("KUZU_DB_PATH", str(path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app
    from core.api.routers import games as games_router

    games_router._CACHE.clear()
    return create_app()


def test_a_sparse_kernel_refuses_to_solve_rather_than_guess(tmp_path, monkeypatch):
    # THE MOST IMPORTANT BEHAVIOUR ON THIS ENDPOINT. One dyad cannot fill 54
    # cells at twelve observations each, so almost every transition would be
    # the pooled fallback — and a game solved over fallback transitions
    # describes the fallback, not the region. It refuses, with the coverage
    # in the message, instead of returning a confident-looking path set.
    from fastapi.testclient import TestClient

    with TestClient(_game_app(tmp_path, monkeypatch)) as client:
        response = client.get("/api/games/explore?region=mena&dyad=dyad:g")
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "measured" in detail and "fallback" in detail


def test_the_defaults_open_on_the_fitted_payoffs(tmp_path, monkeypatch):
    # "No change" has to reproduce the frozen forecast, or a comparison
    # against it means nothing.
    import json

    from fastapi.testclient import TestClient

    from core.games import solve as solve_module
    from core.models import registry

    with TestClient(_game_app(tmp_path, monkeypatch)) as client:
        body = client.get("/api/games/defaults?region=mena").json()
    assert set(body["payoffs"]) == {
        "discount", "cost_resolute", "cost_irresolute", "stake", "audience",
    }
    artifact = registry.MODELS_DIR / "game-mena.json"
    if artifact.exists():
        # The FITTED payoffs, so a slider left alone reproduces the frozen
        # forecast's equilibrium rather than an arbitrary starting point.
        with open(artifact, encoding="utf-8") as fh:
            assert body["payoffs"] == json.load(fh)["payoffs"]
    else:
        # No artifact: the code's own defaults, never a guess.
        assert body["payoffs"]["discount"] == solve_module.Payoffs().discount
    assert body["kernel"]["share_measured"] <= 1.0
    assert body["bands"] and body["actions"]


def test_out_of_range_payoffs_are_refused(tmp_path, monkeypatch):
    # Bounds match the estimator's own clips, so a reader cannot explore a
    # region of the space the fit was never allowed to reach.
    from fastapi.testclient import TestClient

    with TestClient(_game_app(tmp_path, monkeypatch)) as client:
        assert client.get(
            "/api/games/explore?region=mena&dyad=dyad:g&discount=2.0"
        ).status_code == 422
        assert client.get(
            "/api/games/explore?region=mena&dyad=dyad:g&cost_resolute=99"
        ).status_code == 422


def test_the_counterfactual_payload_declares_itself():
    # The frozen sequence forecast is the call of record and gets scored; a
    # counterfactual persists nothing. Without the label a slider quietly
    # becomes a prediction nobody committed to. Asserted on the router's own
    # constant so it cannot be dropped in a refactor.
    from core.api.routers import games as games_router

    source = games_router.__doc__ or ""
    assert "persists nothing" in source
    import inspect
    body = inspect.getsource(games_router.explore)
    assert '"frozen": False' in body
    assert "COUNTERFACTUAL, not a forecast" in body
