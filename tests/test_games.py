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
