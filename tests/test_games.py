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


def test_material_conflict_outranks_talk_by_share_not_by_presence():
    # THE RULE MOVED ON 2026-08-15, ON EVIDENCE. One shooting among forty
    # statements used to read as escalation — right for a curated spine, and
    # on the wire it made allied exercises ("exhibit force posture", quad
    # material_conflict) turn a 135-event US–Japan quarter into an escalation,
    # saturate the belief filter and solve the game to escalate/escalate.
    # A single material event in a quarter that is otherwise talk is a hold;
    # a quarter that is one shooting and nothing else still escalates; a
    # material-conflict share above the bar escalates whatever the talk.
    assert transition.action_from_quads(
        {"material_conflict": 1, "verbal_cooperation": 40}
    ) == "de-escalate"  # cooperation is the clear majority; one record is noise
    assert transition.action_from_quads(
        {"material_conflict": 1, "verbal_conflict": 20, "verbal_cooperation": 20}
    ) == "hold"
    assert transition.action_from_quads({"material_conflict": 1}) == "escalate"
    assert transition.action_from_quads(
        {"material_conflict": 12, "verbal_cooperation": 40}
    ) == "escalate"
    assert transition.action_from_quads(
        {"material_conflict": 5, "verbal_cooperation": 95}
    ) == "escalate"  # the absolute count clears the bar
    assert transition.action_from_quads({"verbal_cooperation": 3}) == "de-escalate"
    assert transition.action_from_quads(
        {"verbal_cooperation": 3, "verbal_conflict": 3}
    ) == "hold"
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
    # NEVER CERTAIN (2026-08-15): the filter keeps a floor of doubt on both
    # sides, so a run of one action saturates at the ceiling, not at 1.0 —
    # a belief at exactly 1.0 collapsed every course onto one path.
    belief = 0.5
    for _ in range(30):
        belief = solve.posterior(belief, escalate, payoffs)
    assert belief == pytest.approx(solve.BELIEF_CEILING)
    assert solve.posterior(1.0, back_down, payoffs) < 1.0


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


def test_the_kind_shares_are_counted_over_every_course_not_the_printed_eight():
    # THE 1% HEADLINE. `top` is a reading cut — 1,645 courses were enumerated
    # for US–Iran and the eight printed held 1.4% of the mass between them, so
    # a scenario pooled from THEM said "most likely course … at 1%". The kind
    # shares are counted before the cut and sum to one.
    from core.games import scenarios

    kernel = _realistic_kernel()
    equilibrium = solve.solve(kernel, solve.Payoffs(), horizon=4)
    walked = paths.enumerate_paths(
        equilibrium, kernel, intensity=3, capability=1,
        belief_a=0.5, belief_b=0.5, payoffs=solve.Payoffs(),
        classify=lambda steps: scenarios.classify_course(steps, 3)[0],
    )
    assert walked["kinds"], "no kind was counted"
    assert sum(k["probability"] for k in walked["kinds"]) == pytest.approx(1.0, abs=1e-3)
    assert sum(k["courses"] for k in walked["kinds"]) == walked["paths_enumerated"]
    assert all(k["lead_probability"] <= k["probability"] + 1e-9 for k in walked["kinds"])
    # And the shares are strictly bigger than the printed courses' own mass,
    # which is the whole point of counting before the cut.
    assert max(k["probability"] for k in walked["kinds"]) >= walked["retained_probability"]
    named = scenarios.scenarios_for(
        walked, dyad_id="dyad:x--y", dyad_name="Xland – Yland", opening_band=3, bands=6,
    )
    assert sum(sc["likelihood"] for sc in named) == pytest.approx(1.0, abs=1e-3)
    # No classifier passed: the old, smaller pooling over the kept paths.
    assert not _paths()["kinds"]


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


# ── market-implied duration ──────────────────────────────────────────────────


def _tenor(event: str, market: str, ret: float) -> dict[str, Any]:
    return {"event_id": event, "market_id": market, "abnormal_return": ret}


def test_a_front_end_shock_reads_as_transient():
    # The market moved the bill and left the long bond: it expects this to
    # pass. That is what a low long-end share MEANS.
    from core.games import duration

    readings = {"front": 0.020, "long": 0.001}
    share = duration.implied_persistence(readings)
    assert share is not None and share < 0.1


def test_a_long_end_shock_reads_as_persistent():
    from core.games import duration

    share = duration.implied_persistence({"front": 0.001, "long": 0.020})
    assert share is not None and share > 0.9


def test_direction_does_not_change_duration():
    # A flight to quality and an inflation scare move yields opposite ways
    # while both say "this matters". Duration is about WHERE on the curve.
    from core.games import duration

    up = duration.implied_persistence({"front": 0.004, "long": 0.016})
    down = duration.implied_persistence({"front": -0.004, "long": -0.016})
    assert up == pytest.approx(down)


def test_an_event_with_only_one_end_is_not_a_duration():
    # The statistic is a ratio between the ends; one end alone says nothing.
    from core.games import duration

    only_front = [_tenor("e1", "market:dgs3mo", 0.02)]
    assert duration.curve_response(only_front) == {}


def test_a_thin_dyad_is_flagged_rather_than_dropped():
    from core.games import duration

    effects = []
    for i in range(3):
        effects += [_tenor(f"e{i}", "market:dgs3mo", 0.01),
                    _tenor(f"e{i}", "market:dgs10", 0.03)]
    rows = duration.by_dyad(effects, {f"e{i}": "dyad:x" for i in range(3)})
    assert rows and rows[0]["thin"] is True and rows[0]["n"] == 3


def test_an_unmeasured_curve_says_so():
    # The state this ships in until FRED yields reach the panel: not an error,
    # and not a silently empty table either.
    from core.games import duration

    body = duration.report([], {})
    assert body["events_with_a_curve_response"] == 0
    assert body["note"] and "FRED_API_KEY" in body["note"]
    assert body["calibration"], "the uncalibrated mapping must travel with it"


# -- kernel stochasticity, the opening state, and the ML bridge (2026-08-14) --


def test_the_fan_is_a_fan_not_a_modal_collapse():
    # The walk used to take each kernel row's MODAL band, which made every
    # period's marginal identical and band_spread the only trace of the
    # discarded distribution. The marginal must now be a real distribution
    # that evolves across periods.
    kern = _realistic_kernel()
    equilibrium = solve.solve(kern, solve.Payoffs(), horizon=4)
    result = paths.enumerate_paths(
        equilibrium, kern, intensity=2, capability=1,
        belief_a=0.5, belief_b=0.5, payoffs=solve.Payoffs(),
    )
    marginal = result["marginal"]
    assert len(marginal) == 4
    spread_bands = sum(1 for share in marginal[0]["distribution"] if share > 0.05)
    assert spread_bands >= 2, "period 1 must carry the kernel row's spread"
    assert any(
        marginal[i]["distribution"] != marginal[0]["distribution"]
        for i in range(1, 4)
    ), "the distribution must evolve across periods"
    # Shares are rounded to 4 decimals in the payload, so the sum can drift
    # by a few 1e-4 — the assertion is "a distribution", not "unrounded".
    assert all(
        abs(sum(row["distribution"]) - 1.0) < 2e-3 for row in marginal
    )


def test_the_tilt_is_bounded_audited_and_identity_at_zero():
    from core.games import bridge

    kern = _realistic_kernel()
    assert bridge.tilted_kernel(kern, 0.0) is kern

    path = [
        {"deviation": 3.0, "lo": -1.0, "hi": 1.0},
        {"deviation": 0.5, "lo": -1.0, "hi": 1.0},
    ]
    eta = bridge.eta_from_trajectory(path)
    # First step clips at 1.0, second is 0.5; mean 0.75 scaled by TILT_SCALE.
    assert eta == pytest.approx(bridge.TILT_SCALE * 0.75)
    tilted = bridge.tilted_kernel(kern, eta)
    assert np.allclose(tilted.sum(axis=-1), 1.0)
    bands = kern.shape[-1]
    expected_next = kern[2, 1, 1] @ np.arange(bands)
    expected_tilted = tilted[2, 1, 1] @ np.arange(bands)
    assert expected_tilted > expected_next, "positive drift leans the rows up"

    audit = bridge.audit(eta, {"name": "intensity", "hash": "abc123"})
    assert audit is not None and audit["model"] == "intensity@abc123"
    assert bridge.audit(0.0, {"name": "intensity", "hash": "abc123"}) is None


def test_beliefs_are_filtered_from_observed_actions():
    from core.games import opening

    payoffs = solve.Payoffs()
    joint: dict[tuple[str, int], tuple[str, str]] = {}
    for q in range(100, 112):
        joint[("dyad:hawks", q)] = ("escalate", "escalate")
        joint[("dyad:doves", q)] = ("de-escalate", "de-escalate")
    hawks = opening.filtered_beliefs(joint, "dyad:hawks", payoffs)
    doves = opening.filtered_beliefs(joint, "dyad:doves", payoffs)
    unseen = opening.filtered_beliefs(joint, "dyad:unseen", payoffs)
    assert hawks["a"] >= 0.85 and hawks["b"] >= 0.85
    assert doves["a"] <= 0.15 and doves["b"] <= 0.15
    # and never certain: the ceiling holds on both sides
    assert hawks["a"] <= solve.BELIEF_CEILING and doves["a"] >= 1.0 - solve.BELIEF_CEILING
    assert unseen == {
        "a": 0.5, "b": 0.5, "quarters_observed": 0, "source": "default",
    }
    assert hawks["source"] == "bayes_filter"


# ── the LP equilibrium and the scenario map (2026-08-15) ──────────────────────


def test_the_lp_stage_solution_is_a_correlated_equilibrium():
    # Every incentive constraint holds on the LP's joint distribution: a side
    # told its recommended action cannot gain by deviating.
    from core.games import equilibrium

    rng = np.random.default_rng(3)
    a = rng.normal(size=(3, 3))
    b = rng.normal(size=(3, 3))
    stage = equilibrium.solve_stage_lp(a, b)
    assert stage.status == "optimal"
    assert abs(stage.joint.sum() - 1.0) < 1e-6 and (stage.joint >= 0).all()
    for i in range(3):
        for i_alt in range(3):
            gain = sum(stage.joint[i, j] * (a[i_alt, j] - a[i, j]) for j in range(3))
            assert gain <= 1e-6
    for j in range(3):
        for j_alt in range(3):
            gain = sum(stage.joint[i, j] * (b[i, j_alt] - b[i, j]) for i in range(3))
            assert gain <= 1e-6
    assert 0.0 <= stage.nash_gap <= 1.0


def test_a_dominance_game_is_pure_and_reports_a_zero_nash_gap():
    # When one action strictly dominates, the CE polytope is a POINT: every
    # correlated equilibrium plays it, so the regularised selection returns the
    # pure profile with zero entropy and the gap says it sat on a Nash point.
    # The limit is the honest part — regularisation spreads a selection, never
    # a polytope the payoffs have collapsed.
    from core.games import equilibrium

    a = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [2.0, 2.0, 2.0]])
    stage = equilibrium.solve_stage_lp(a, a.T)
    assert stage.joint[2, 2] > 0.999
    assert stage.entropy < 1e-6
    assert stage.nash_gap < 1e-6


def test_welfare_tied_equilibria_are_kept_rather_than_broken_by_the_solver():
    # A prisoner's-dilemma-shaped stage where a pure profile and a correlated
    # play carry the SAME welfare. The bare welfare LP is a linear objective
    # over a polytope, so it returned whichever vertex HiGHS reached — one
    # joint action at certainty, which downstream became "its most likely
    # course is mutual escalation at 100%". The entropy term breaks the tie in
    # the only defensible direction: keep both, claim no more certainty than
    # the payoffs support.
    from core.games import equilibrium

    a = np.array([[3.0, 0.0, 0.0], [5.0, 1.0, 0.0], [5.0, 1.0, 1.0]])
    stage = equilibrium.solve_stage_lp(a, a.T)
    assert stage.status == "optimal"
    assert stage.entropy > 0.5
    assert float(stage.mix_a.max()) < 0.99
    # Still an exact correlated equilibrium, and its welfare still maximal.
    assert stage.ce_violation < 1e-6
    assert abs((stage.joint * (a + a.T)).sum() - 2.0) < 1e-6


def test_the_lp_solver_threads_through_the_recursion():
    # Same kernel, same payoffs, two concepts: both return proper policies,
    # the LP carries its nash_gap audit and B's marginal, and the QRE does not
    # pretend to.
    kernel = _realistic_kernel()
    lp = solve.solve(kernel, solve.Payoffs(), horizon=2, solver="lp")
    qre = solve.solve(kernel, solve.Payoffs(), horizon=2, solver="qre")
    for eq in (lp, qre):
        assert np.allclose(eq["policy"].sum(axis=-1), 1.0)
        assert np.allclose(eq["policy_b"].sum(axis=-1), 1.0)
        assert eq["opening_matrices"][0].shape[-2:] == (3, 3)
    assert lp["solver"] == "lp" and "nash_gap" in lp and lp["nash_gap"]["all_optimal"]
    assert "correlated-equilibrium" in lp["concept"]
    assert "entropy-regularised" in lp["concept"]
    # The degeneracy audit rides with the gap: a vertex selection would report
    # entropy 0 at every stage, and the surface would be quoting certainty.
    assert lp["nash_gap"]["ce_violation_max"] < 1e-6
    assert "entropy_mean" in lp["nash_gap"]
    assert "nash_gap" not in qre
    with pytest.raises(ValueError):
        solve.solve(kernel, solve.Payoffs(), horizon=2, solver="nope")


def test_paths_carry_the_belief_trajectory():
    result = _paths()
    for path in result["paths"]:
        for step in path["steps"]:
            assert 0.0 <= step["belief_a"] <= 1.0 and 0.0 <= step["belief_b"] <= 1.0


def test_scenarios_are_named_from_the_course_and_sum_over_the_retained_mass():
    from core.games import scenarios

    priced = _paths()
    named = scenarios.scenarios_for(
        priced, dyad_id="dyad:x--y", dyad_name="Xland – Yland", opening_band=2, bands=6,
    )
    # ONE SCENARIO PER KIND, pooling every course the classifier reads the same
    # way: `scenario_name` is `kind:dyad`, so one-per-course made the name
    # ambiguous and split one distribution across four rows of the region's
    # escalatory list. Names are unique, the pooled likelihoods still add to
    # the retained mass, and every course is accounted for.
    assert named and len(named) <= len(priced["paths"])
    assert len({sc["scenario_name"] for sc in named}) == len(named)
    assert sum(sc["courses"] for sc in named) == len(priced["paths"])
    assert all(sc["likelihood"] >= sc["lead_likelihood"] for sc in named)
    kinds = {sc["kind"] for sc in named}
    assert kinds <= {
        "mutual_escalation", "one_sided_pressure", "brinkmanship", "probe_and_retreat",
        "step_down", "drift_up", "drift_down", "holding_pattern",
    }
    assert all(sc["scenario_name"].endswith(":dyad:x--y") for sc in named)
    assert abs(sum(sc["likelihood"] for sc in named) - priced["retained_probability"]) < 1e-3
    assert all(sc["rationale"] and sc["end_label"] for sc in named)


def test_the_course_classifier_reads_who_pressed():
    from core.games import scenarios

    esc = {"action_a": "escalate", "action_b": "hold", "intensity_band": 3}
    hold = {"action_a": "hold", "action_b": "hold", "intensity_band": 2}
    de = {"action_a": "de-escalate", "action_b": "hold", "intensity_band": 1}
    assert scenarios.classify_course([esc, esc], 2)[0] == "one_sided_pressure"
    assert scenarios.classify_course([esc, de], 2)[0] == "probe_and_retreat"
    assert scenarios.classify_course([hold, hold], 2)[0] == "holding_pattern"
    both = {"action_a": "escalate", "action_b": "escalate", "intensity_band": 4}
    assert scenarios.classify_course([both, both], 2)[0] == "mutual_escalation"
    assert scenarios._presser([esc], "A", "B") == "A"
    assert scenarios._presser([both], "A", "B") is None


def test_the_explanation_is_written_from_the_payload():
    # Every number in the prose is a field of the solution: the check is that
    # the writer runs on a synthetic solution and cites its figures.
    from core.games import scenarios

    kernel = _realistic_kernel()
    payoffs = solve.Payoffs()
    context = {
        "table": [
            {"dyad_id": "dyad:x--y", "dyad_name": "Xland – Yland", "q": q,
             "date": f"{2000 + q // 4}-01-01", "intensity": 3.0 + (q % 3), "events": 4,
             "tone": -2.0}
            for q in range(30)
        ],
        "kernel": kernel,
        "joint": {},
        "effects": [],
        "model_trajectories": {},
        "model_identity": None,
        "coverage": {"cells": 54, "measured": 54, "fallback": 0, "share_measured": 1.0,
                     "observations": 999},
        "as_of": "2007-01-01",
    }
    solved = scenarios.solve_dyad(
        context, region="test", dyad_id="dyad:x--y", payoffs=payoffs, graph_conn=None,
        horizon=2,
    )
    assert solved is not None
    assert set(solved["concepts"]) == {"lp", "qre"}
    text = " ".join(solved["explanation"])
    assert "Xland" in text and "Yland" in text
    assert "nash_gap" in text
    assert "untilted" in text  # no frozen model in this context
    lp = solved["concepts"]["lp"]
    assert 0.0 <= lp["escalation_probability"] <= 1.0
    assert lp["opening_matrix"]["resolute"]["actions"] == list(state.ACTIONS)


def test_the_region_map_aggregates_every_solved_dyad(tmp_path, monkeypatch):
    from core.games import scenarios

    kernel = _realistic_kernel()
    table = []
    for d in ("dyad:a--b", "dyad:c--d"):
        table += [
            {"dyad_id": d, "dyad_name": d, "q": q, "date": f"{2000 + q // 4}-01-01",
             "intensity": 2.0 + (q % 4), "events": 3, "tone": -1.0}
            for q in range(24)
        ]
    context = {
        "table": table, "kernel": kernel, "joint": {}, "effects": [],
        "model_trajectories": {}, "model_identity": None,
        "coverage": {"cells": 54, "measured": 54, "fallback": 0, "share_measured": 1.0,
                     "observations": 100},
        "as_of": "2005-10-01",
    }
    solved = scenarios.region_map(
        context, region="test", payoffs=solve.Payoffs(), graph_conn=None,
        dyad_ids=["dyad:a--b", "dyad:c--d", "dyad:missing"], horizon=2,
    )
    agg = solved["region"]
    assert agg["dyads_solved"] == 2 and len(solved["dyads"]) == 2
    assert len(agg["ranking"]) == 2 and len(agg["heat"]) == 2
    assert len(agg["region_fan"]) == 2
    assert agg["explanation"] and agg["boundary_statement"]
    assert all(abs(sum(row["distribution"]) - 1.0) < 1e-3 for row in agg["region_fan"])


# ── a persisted solution outlives the code that wrote it (2026-08-15) ────────


class _Row:
    """The one row `game_solution` reads, through psycopg's cursor shape."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> _Row:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def cursor(self) -> _Row:
        return self

    def execute(self, *args: Any) -> None:
        return None

    def fetchone(self) -> tuple[Any, Any, str]:
        import datetime as dt

        return (self.payload, dt.datetime(2026, 8, 15, 19, 1, tzinfo=dt.UTC), "lp")


def test_a_persisted_solution_of_the_wrong_shape_is_a_miss_not_a_payload():
    # THE NaN THE SURFACE WORE. `escalation_probability` was renamed
    # `sharp_departure_probability`, the games boot step is opt-in and did not
    # re-solve, and Postgres kept serving the previous shape — so the region
    # map rendered "NaN%" for every pair beside courses named at 100% (those
    # rows also predate the belief ceiling). The reader now checks the version
    # and misses, which sends the endpoint to its live fallback.
    from core.games import scenarios
    from core.panel import pg_store

    stale = {"region": "mena", "escalation_probability": 0.7, "payload_version": "2026-08-15.1"}
    fresh = {**stale, "payload_version": scenarios.PAYLOAD_VERSION}

    assert pg_store.game_solution(
        _Row(stale), "mena", scope="region", version=scenarios.PAYLOAD_VERSION
    ) is None
    assert pg_store.game_solution(
        _Row({"region": "mena"}), "mena", scope="region", version=scenarios.PAYLOAD_VERSION
    ) is None
    served = pg_store.game_solution(
        _Row(fresh), "mena", scope="region", version=scenarios.PAYLOAD_VERSION
    )
    assert served is not None and served["persisted"] is True
    assert served["computed_at"].startswith("2026-08-15")
    # No version asked for: whatever is stored, for callers that want the row.
    assert pg_store.game_solution(_Row(stale), "mena", scope="region") is not None


def _region_context() -> dict[str, Any]:
    """One modelable dyad over thirty quarters — enough to solve and name."""
    return {
        "table": [
            {"dyad_id": "dyad:x--y", "dyad_name": "Xland – Yland", "q": q,
             "date": f"{2000 + q // 4}-01-01", "intensity": 3.0 + (q % 3), "events": 4,
             "tone": -2.0}
            for q in range(30)
        ],
        "kernel": _realistic_kernel(),
        "joint": {},
        "effects": [],
        "model_trajectories": {},
        "model_identity": None,
        "coverage": {"cells": 54, "measured": 54, "fallback": 0, "share_measured": 1.0,
                     "observations": 999},
        "as_of": "2007-01-01",
    }


def test_every_solved_payload_carries_its_version():
    from core.games import scenarios

    solved = scenarios.solve_dyad(
        _region_context(), region="test", dyad_id="dyad:x--y",
        payoffs=solve.Payoffs(), graph_conn=None, horizon=2,
    )
    assert solved is not None and solved["payload_version"] == scenarios.PAYLOAD_VERSION
    mapped = scenarios.region_map(
        _region_context(), region="test", payoffs=solve.Payoffs(), graph_conn=None,
        dyad_ids=["dyad:x--y"], horizon=2,
    )
    assert mapped["region"]["payload_version"] == scenarios.PAYLOAD_VERSION


# ── what a pair IS vs how its record reads (2026-08-15) ──────────────────────


def test_the_posture_is_the_coercive_share_not_the_mean_tone():
    # THE "FRIENDLY RIVALRY". Mean Goldstein ranks pairs by how much they TALK:
    # the wire codes meetings and statements in far greater number than
    # anything coercive, so 65% of china's pairs, 64% of eurasia's and 51% of
    # mena's scored "friendly" or better — the United States and China, a
    # declared rivalry, at +1.65. The coercive SHARE ranks them by what the
    # interactions were, which is what the word was trying to say.
    from core.games import opening

    def quarter(q: int, events: int, conflict: int, tone: float) -> dict[str, Any]:
        return {"q": q, "events": events, "conflict": conflict, "tone": tone}

    # US–China's actual last four quarters: 1,542 events, 82 coercive, tone +1.65.
    read = opening.posture([quarter(q, 385, 20, 1.65) for q in range(4)])
    assert read["thin"] is False
    assert read["share"] == pytest.approx(20 / 385, abs=1e-3)
    assert read["label"] == "mostly talk"
    assert read["tone"] == pytest.approx(1.65)  # kept, as a number

    # Russia–Ukraine's shape: a third of everything coded is material conflict.
    hot = opening.posture([quarter(q, 800, 290, -2.57) for q in range(4)])
    assert hot["label"] == "often coercive"
    assert opening.posture_label(0.0) == "almost all talk"
    assert opening.posture_label(0.6) == "mostly coercive"

    # A pair the wire barely covers gets no verdict at all — the thin pairs are
    # where a share of a handful looked most confident (Sweden–Norway 0.0%).
    thin = opening.posture([quarter(0, 6, 0, 4.76)])
    assert thin["thin"] and thin["share"] is None
    assert "coverage" in thin["label"]


def test_the_standing_is_the_graphs_declared_relation_in_force(monkeypatch):
    from core.games import opening
    from core.graph import kuzu_store

    declared = [
        {"relation_type": "rivalry", "valid_from": "2018-03-22", "valid_to": "",
         "source_id": "source:crs-taiwan", "from_id": "actor:cow-710",
         "to_id": "actor:cow-2"},
        # An expired COW alliance over the same pair: real, and not in force.
        {"relation_type": "alliance", "valid_from": "1962-07-23",
         "valid_to": "1964-07-12", "source_id": "source:cow-alliances",
         "from_id": "actor:cow-2", "to_id": "actor:cow-710"},
    ]
    monkeypatch.setattr(kuzu_store, "query", lambda *a, **k: declared)
    out = opening.standing(object(), "dyad:cow-2--cow-710", as_of="2026-07-01")
    assert [r["relation_type"] for r in out["relations"]] == ["rivalry"]
    assert out["relations"][0]["source_id"] == "source:crs-taiwan"
    # Asked in 1963 the same graph says something else, and says it from the
    # same rows — the window is the fact, not a footnote on it.
    then = opening.standing(object(), "dyad:cow-2--cow-710", as_of="1963-01-01")
    assert [r["relation_type"] for r in then["relations"]] == ["alliance"]
    # No graph is not "no relation".
    assert opening.standing(None, "dyad:cow-2--cow-710", as_of="2026-07-01") == {
        "relations": [], "source": "no graph",
    }


def test_a_declared_rivalry_is_never_described_by_its_mean_tone():
    # The regression the reader caught: "The call" and "Where it stands"
    # contradicting each other over the same pair. The sentence must lead with
    # the sourced relation and describe the wire as a record, never as a
    # character.
    from core.games import scenarios

    opening_state = {
        "standing": {"relations": [
            {"relation_type": "rivalry", "since": "2018-03-22", "until": None,
             "source_id": "source:crs-taiwan", "directed_from": "actor:cow-710"},
        ]},
        "posture": {"label": "mostly talk", "share": 0.0532, "events": 1542,
                    "coercive": 82, "tone": 1.651, "quarters": 4, "thin": False},
        "tone": 1.651,
    }
    said = scenarios.describe_standing(opening_state)
    assert said == "a declared rivalry since 2018"
    record = scenarios.describe_posture(opening_state)
    assert "coded interactions were coercive" in record
    assert "friendly" not in f"{said} {record}"
    # And with nothing declared, the absence is stated rather than filled.
    assert "no relation" in scenarios.describe_standing({"standing": {"relations": []}})
