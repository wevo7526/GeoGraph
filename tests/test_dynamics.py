"""The transition model: what it claims, and the two ways it must not lie.

The complaint that produced this model was concrete — the solved games "among
allies don't make sense at all, like the US with AUS, JPN" — and it was
correct for a measurable reason: `transition.kernel` counts one table for a
whole region, so every pair sitting in the same band got the same dynamics.
At band 2 the counted kernel returned an expected next band of 0.60 for every
pair on the board.

So the tests here are about the two properties that make the fix trustworthy
rather than merely different: the model cannot throw away the counted
evidence, and it must actually distinguish pairs.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from core.games import state as state_module
from core.games import transition
from core.models import dynamics, registry

BANDS = len(state_module.INTENSITY_EDGES)
ACTIONS = len(state_module.ACTIONS)


def _counted() -> np.ndarray:
    """A kernel with structure, so a tilt has something to move."""
    rng = np.random.default_rng(11)
    counts = {}
    for band in range(BANDS):
        for a in range(ACTIONS):
            for b in range(ACTIONS):
                row = rng.integers(1, 40, size=BANDS).astype(float)
                row[min(BANDS - 1, band + (1 if a == 0 else 0))] += 60
                counts[(band, a, b)] = row
    kernel, _observed = transition.kernel(counts)
    return kernel


def _features(volume: float, coercive: float, volatility: float, band: int):
    return {
        "volume": volume, "coercive": coercive, "volatility": volatility,
        "coercive_x_band": coercive * band,
        "volume_x_coercive": volume * coercive,
    }


def test_zero_weights_reproduce_the_counted_kernel_exactly():
    """THE OFFSET IS THE POINT. The counted kernel enters as log-probabilities
    the residual adds to, not as a baseline to beat, so W = 0 must return the
    counts unchanged — that is what makes "the model cannot be worse than
    counting" a structural fact rather than a hope.

    An additive residual WITHOUT the offset was tried first and lost to the
    plain counts, because the counted table encodes a band x action x action
    interaction a linear model in those variables cannot represent.
    """
    counted = _counted()
    names = dynamics.feature_names()
    model = dynamics.Dynamics(
        weights=np.zeros((len(names), BANDS)),
        mean=np.zeros(len(names)), scale=np.ones(len(names)),
        names=names, region="test",
    )
    tilted = model.kernel_for(counted, _features(5.0, 0.3, 0.2, 1))
    assert np.allclose(tilted, counted, atol=1e-9)


def test_the_kernel_now_depends_on_which_pair_it_is_for():
    """The whole complaint. Two pairs in the same band, with different records,
    must not be handed the same dynamics."""
    counted = _counted()
    names = dynamics.feature_names()
    weights = np.zeros((len(names), BANDS))
    weights[names.index("coercive"), -1] = 1.5   # coercion pushes mass up
    weights[names.index("coercive"), 0] = -1.5
    model = dynamics.Dynamics(
        weights=weights, mean=np.zeros(len(names)), scale=np.ones(len(names)),
        names=names, region="test",
    )
    quiet = model.kernel_for(counted, _features(5.0, 0.05, 0.2, 1))
    coercive = model.kernel_for(counted, _features(5.0, 0.60, 0.2, 1))

    expected = np.arange(BANDS)
    assert not np.allclose(quiet, coercive), "the pairs got the same kernel"
    assert float(coercive[1, 0, 0] @ expected) > float(quiet[1, 0, 0] @ expected)
    for kernel in (quiet, coercive):
        assert np.allclose(kernel.sum(axis=-1), 1.0), "rows must stay distributions"


def test_the_tilt_is_bounded_so_a_residual_cannot_erase_the_counts():
    """The cells the counted kernel is least sure of are exactly the ones an
    unclipped softmax will send to a corner."""
    counted = _counted()
    names = dynamics.feature_names()
    weights = np.zeros((len(names), BANDS))
    weights[names.index("volume"), -1] = 500.0
    model = dynamics.Dynamics(
        weights=weights, mean=np.zeros(len(names)), scale=np.ones(len(names)),
        names=names, region="test",
    )
    tilt = model.tilt(_features(9.0, 0.3, 0.2, 1), band=1)
    assert tilt.max() <= dynamics.MAX_TILT + 1e-9
    assert tilt.min() >= -dynamics.MAX_TILT - 1e-9
    kernel = model.kernel_for(counted, _features(9.0, 0.3, 0.2, 1))
    assert np.isfinite(kernel).all()
    assert np.allclose(kernel.sum(axis=-1), 1.0)


def test_the_gate_demands_within_dyad_ordering_not_just_pooled_loss():
    """docs/ml-spec.md's lesson, applied to this model.

    Pooled log-loss is the easy half — the graph features improved it too,
    while making within-dyad ordering WORSE (china +0.1740 -> +0.1591). A
    model whose purpose is to give each pair its own dynamics has to order
    that pair's own quarters better, so the gate asks for both.
    """
    better = [{"log_loss": 1.25, "log_loss_counted": 1.38,
               "rho": 0.12, "rho_counted": 0.09}]
    passed, summary = dynamics.passes_gate(better)
    assert passed, summary

    pooled_only = [{"log_loss": 1.25, "log_loss_counted": 1.38,
                    "rho": 0.07, "rho_counted": 0.09}]
    passed, summary = dynamics.passes_gate(pooled_only)
    assert not passed and "ordering" in summary

    no_loss_gain = [{"log_loss": 1.40, "log_loss_counted": 1.38,
                     "rho": 0.12, "rho_counted": 0.09}]
    passed, summary = dynamics.passes_gate(no_loss_gain)
    assert not passed and "log-loss" in summary


@pytest.mark.parametrize("region", ["mena", "china", "eurasia"])
def test_every_shipped_artifact_passed_its_gate_and_matches_its_hash(region):
    """A committed artifact is a claim; this is the claim being checked.

    An artifact whose gate FAILED is still written (the failure is the record)
    but must never be loaded — `context.load_dynamics` refuses it, and the
    game falls back to the counted kernel it has always had.
    """
    path = registry.MODELS_DIR / f"dynamics-{region}.json"
    if not path.exists():
        pytest.skip(f"{path.name} does not ship")
    artifact = json.loads(path.read_text(encoding="utf-8"))
    registry.verify_hash(artifact, what=path.name)
    assert artifact["gate_passed"], artifact["gate_summary"]
    assert artifact["model"]["names"] == list(dynamics.feature_names()), (
        "the artifact was fitted with a different feature order than the code "
        "would use at inference — retrain rather than reinterpreting weights"
    )
    for fold in artifact["folds"]:
        assert fold["log_loss"] < fold["log_loss_counted"]


def test_the_excluded_graph_features_are_recorded_with_the_reason():
    """A negative result is worth as much as the model, and only if it is
    written down: the next reader will otherwise re-add centrality."""
    path = registry.MODELS_DIR / "dynamics-mena.json"
    if not path.exists():
        pytest.skip("no artifact")
    artifact = json.loads(path.read_text(encoding="utf-8"))
    excluded = artifact["excluded"]
    assert "ally" in excluded["declared"] and "rival" in excluded["declared"]
    assert "betweenness" in excluded["structural"]
    assert "within-dyad" in excluded["why"]


def test_a_pack_with_no_artifact_still_solves():
    """The model is an improvement, not a dependency. A region without one
    gets the counted kernel — the same rule that keeps the two counted
    forecast modes independent of the learned one."""
    from core.games import context as context_module

    assert context_module.load_dynamics("no-such-region") is None


def test_the_explanation_handles_both_kernel_audits_and_neither():
    """THE REGRESSION THIS EXISTS FOR. `explain` read `tilt['eta']` directly,
    and the dynamics audit has no eta — it names features, a bound and the
    gate it passed. Production answered `KeyError: 'eta'` on the first solve
    after the model shipped.

    Two instruments can produce a kernel and a third case is neither, so the
    sentence has to cope with all three rather than assume the bridge.
    """
    from core.games import scenarios

    dynamics_audit = {
        "model": "dynamics-test@abc123", "method": "softmax over an offset",
        "features": {"volume": 6.1, "coercive": 0.09, "volatility": 0.2},
        "max_tilt": 2.5, "gate": "log-loss 1.25 vs counted 1.38",
        "ordering_horizon": dynamics.ORDERING_HORIZON_QUARTERS,
    }
    line = scenarios.describe_kernel(dynamics_audit)
    assert "dynamics-test@abc123" in line and "volume +6.10" in line
    assert "log-loss 1.25" in line
    # AND THE HORIZON THE CLAIM HOLDS FOR. The game applies this kernel four
    # times; the model was fitted one quarter ahead, and re-fitting at each
    # horizon showed the ORDERING edge gone by the fourth (china's goes
    # negative). A four-period fan must not imply four periods of edge.
    assert "quarter ahead" in line and "less informed" in line

    bridge_audit = {"eta": 0.42, "scale": 0.5, "model": "intensity@def456",
                    "method": "kernel rows tilted by exp(eta * band offset)"}
    line = scenarios.describe_kernel(bridge_audit)
    assert "+0.420" in line and "intensity@def456" in line

    line = scenarios.describe_kernel(None)
    assert "counted table" in line


def test_the_ordering_horizon_is_carried_into_every_solve():
    """The model is fitted ONE quarter ahead and the game applies its kernel
    four times. Re-fitting at each horizon showed the log-loss edge surviving
    and the within-dyad ORDERING edge gone by the fourth — china's turns
    negative. The number travels with the audit block so a four-period fan
    cannot quietly imply four periods of edge."""
    from pathlib import Path

    from core.games import context as context_module

    assert dynamics.ORDERING_HORIZON_QUARTERS == 1
    source = Path(context_module.__file__).read_text(encoding="utf-8")
    assert "ordering_horizon" in source, (
        "kernel_for must put the measured horizon in the audit block"
    )


def test_the_audit_line_names_only_the_features_the_model_reads():
    """`row_features` computes `level` as well — it is kept so the ablation
    can read it — and the first version of the audit block listed it, putting
    `level +1.02` in a sentence about what moved this pair's kernel when the
    model never sees it. A number in the audit has to be a number in the model.
    """
    from core.games import context as context_module

    row = {"events": 900, "conflict": 60, "intensity": 8.0, "tone": 1.0, "q": 1}
    features = dynamics.row_features([row] * 4, band=2, scale=10.0)
    assert "level" in features, "row_features still computes it for the ablation"

    context = {
        "kernel": _counted(),
        "table": [{**row, "dyad_id": "dyad:a--b", "q": q} for q in range(8)],
    }
    names = dynamics.feature_names()
    context["dynamics"] = {
        "identity": "dynamics-test@abc",
        "summary": "log-loss 1.2 vs counted 1.3",
        "model": dynamics.Dynamics(
            weights=np.zeros((len(names), BANDS)),
            mean=np.zeros(len(names)), scale=np.ones(len(names)),
            names=names, region="test",
        ),
    }
    _kernel, audit = context_module.kernel_for(context, "dyad:a--b")
    assert audit is not None
    assert "level" not in audit["features"], audit["features"]
    assert set(audit["features"]) == {*dynamics.FEATURES, *dynamics.INTERACTIONS}


def test_the_region_explanation_warns_when_an_alliance_leads_the_ranking():
    """The ranking counts events coded with one side as initiator and the
    other as target, and GDELT's actor pairing does not separate "A coerced B"
    from "A and B were both in a coercive event". Measured, china, year to
    2026-08: US–Australia's material-conflict record is 25 events of CAMEO 190
    ("use conventional military force: Australia → United States") and 13 of
    193 ("fight with small arms"); North Korea–South Korea's is 42 of 194 and
    14 of 150 ("exhibit military posture"). One of those is co-involvement in
    third-party operations and the other is the real thing.

    The count still ships — every fitted alternative scored worse out of
    sample — so the caveat travels with it rather than being left as a
    footnote nobody reads.
    """
    from core.games import scenarios

    def _aggregate(relation_type: str):
        return {
            "ranking": [{
                "dyad_id": "dyad:a--b", "dyad_name": "A–B",
                "opening_band": 1, "opening_label": "a mild departure",
                "sharp_departure_probability": 0.2,
                "coercive_events": 72, "coercive_share": 0.08,
                "standing": {"relations": [{"relation_type": relation_type}]},
                "posture": {"label": "mostly talk", "share": 0.08,
                            "events": 900, "quarters": 4, "tone": 1.0,
                            "thin": False},
                "top_scenario": None,
            }],
            "dyads_solved": 12, "dyads_cinc": 12, "dyads_tilted": 12,
            "primary_solver": "lp", "horizon": 4,
            "scenarios_escalatory": [], "scenarios_calming": [],
            "nash_gap": {"mean": 0.0, "max": 0.0},
            "kernel": {"share_measured": 0.8, "observations": 1000},
        }

    allied = " ".join(scenarios.explain_region(_aggregate("alliance")))
    assert "coerced" in allied and "actor pairing" in allied, allied

    rival = " ".join(scenarios.explain_region(_aggregate("rivalry")))
    assert "actor pairing" not in rival, (
        "the caveat belongs where it applies, not on every region"
    )


def test_a_course_named_step_down_is_never_listed_as_escalatory():
    """Production printed "Russia–Japan — step down at 92%" under the heading
    "the escalatory scenarios with the most mass".

    `kind` names the ACTIONS played; `delta_band` is where the pair ended up
    against its own baseline. A pair can play de-escalate every period and
    still drift up, so the two disagree — and the old filter let `delta_band`
    override the name. The kind wins. Same self-contradiction the old
    mean-Goldstein tone verdict produced, in a different place.
    """
    from core.games import scenarios

    def _sc(kind: str, delta: int, likelihood: float = 0.5):
        return {"kind": kind, "delta_band": delta, "likelihood": likelihood,
                "dyad_name": "A–B"}

    rows = [
        _sc("step_down", +1, 0.92),          # the production case
        _sc("drift_down", +1, 0.40),
        _sc("mutual_escalation", -1, 0.80),  # and its mirror
        _sc("one_sided_pressure", 0, 0.70),
        _sc("drift_up", +1, 0.60),           # name is silent: delta decides
        _sc("holding_pattern", -1, 0.30),
        _sc("probe_and_retreat", 0, 0.10),   # silent AND flat: neither list
    ]
    escalatory, calming = scenarios.sort_scenarios(rows)

    assert "step_down" not in [s["kind"] for s in escalatory]
    assert "drift_down" not in [s["kind"] for s in escalatory]
    assert "mutual_escalation" not in [s["kind"] for s in calming]
    # `mutual_escalation` stays escalatory even though its band fell — the
    # kind wins in BOTH directions, not only the flattering one.
    assert [s["kind"] for s in escalatory] == [
        "mutual_escalation", "one_sided_pressure", "drift_up"]
    assert [s["kind"] for s in calming] == ["step_down", "drift_down",
                                            "holding_pattern"]
    # Most mass first, in both.
    assert [s["likelihood"] for s in escalatory] == [0.80, 0.70, 0.60]
    assert [s["likelihood"] for s in calming] == [0.92, 0.40, 0.30]
    # A course whose name is silent and whose band did not move belongs to
    # neither list rather than to both.
    assert "probe_and_retreat" not in [s["kind"] for s in escalatory + calming]
