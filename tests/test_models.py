"""The learned layer: panel construction, leak-free features, the within-dyad
gate, and the artifact contract.

The tests that matter most here are the ones about LEAKAGE and about the
gate — a forecaster that quietly sees the future, or one that ships without
clearing its own bar, fails in a way no amount of downstream care recovers.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pytest

from core.models import features as feature_module
from core.models import intensity, panel, registry


def _events(dyad: str, quarters: dict[int, float], year: int = 1990) -> list[dict[str, Any]]:
    """Escalating events at (year, quarter) → magnitude."""
    return [
        {
            "dyad_id": dyad, "dyad_name": dyad.replace("dyad:", ""),
            "event_time": f"{year + q // 4}-{(q % 4) * 3 + 1:02d}-15",
            "direction": "escalating" if magnitude > 0 else "stable",
            "magnitude": magnitude, "goldstein": -5.0,
            "quad_class": "material_conflict", "region_pack": "mena",
        }
        for q, magnitude in sorted(quarters.items())
    ]


# ── the panel ────────────────────────────────────────────────────────────────


def test_quiet_quarters_are_rows_not_gaps():
    # A panel built only from occupied quarters is a positive-only sample, and
    # a model trained on one learns that escalation never stops.
    rows = _events("dyad:a", {0: 9.0, 8: 7.0})
    table = panel.build(rows, min_occupied=2)
    assert len(table) == 9, "first quarter through last, inclusive"
    assert [r["intensity"] for r in table][1:8] == [0.0] * 7
    assert table[0]["intensity"] == 9.0
    assert table[-1]["intensity"] == 7.0


def test_intensity_is_the_quarters_largest_departure():
    # A rupture inside a noisy quarter is the event being forecast; averaging
    # it against the chatter around it hides exactly that.
    rows = _events("dyad:a", {0: 2.0}) + _events("dyad:a", {0: 11.0}) + _events("dyad:a", {0: 3.0})
    table = panel.build(rows, min_occupied=1)
    assert table[0]["intensity"] == 11.0


def test_a_dyad_without_enough_history_is_absent():
    table = panel.build(_events("dyad:thin", {0: 9.0, 1: 9.0}), min_occupied=8)
    assert table == []


def test_cutoff_truncates_the_panel():
    rows = _events("dyad:a", {q: 9.0 for q in range(12)})
    full = panel.build(rows, min_occupied=2)
    truncated = panel.build(rows, cutoff="1991-06-30", min_occupied=2)
    assert max(r["date"] for r in truncated) < "1991-07"
    assert max(r["date"] for r in full) > "1991-07"


# ── features: the leak is the thing to test ──────────────────────────────────


def test_features_never_read_the_future():
    # Every expanding statistic must be computable from the row's own past.
    # Truncating the series must therefore leave the surviving rows IDENTICAL —
    # if a later quarter changes an earlier row's features, that row was
    # normalised against data it could not have had.
    rows = _events("dyad:a", {q: (9.0 if q % 3 == 0 else 0.0) for q in range(24)})
    table = panel.build(rows, min_occupied=2)
    full = feature_module.build(table)
    short = feature_module.build([r for r in table if r["q"] <= table[11]["q"]])
    for early, late in zip(short, full[: len(short)], strict=True):
        assert early["q"] == late["q"]
        assert early["x"] == pytest.approx(late["x"]), f"quarter {early['q']} saw the future"


def test_the_first_row_has_no_history_to_deviate_from():
    table = panel.build(_events("dyad:a", {q: 9.0 for q in range(10)}), min_occupied=2)
    first = feature_module.build(table)[0]
    names = feature_module.FEATURE_NAMES
    assert first["x"][names.index("base_level")] == 0.0
    assert first["base"] == 0.0


def test_the_deviation_target_is_the_level_minus_the_running_mean():
    table = panel.build(_events("dyad:a", {q: 9.0 for q in range(16)}), min_occupied=2)
    rows_ = feature_module.build(table)
    row = rows_[8]
    for horizon in feature_module.HORIZONS:
        if row["y_level"][horizon] is not None:
            assert row["y_deviation"][horizon] == pytest.approx(
                row["y_level"][horizon] - row["base"]
            )


def test_the_shipped_feature_set_is_a_subset_of_the_built_one():
    assert set(feature_module.SHIPPED_FEATURES) <= set(feature_module.FEATURE_NAMES)
    assert feature_module.shipped_columns() == sorted(feature_module.shipped_columns()), (
        "columns must stay in FEATURE_NAMES order — weights are positional"
    )


# ── the estimator and its gate ───────────────────────────────────────────────


def test_within_dyad_score_ignores_dyads_that_never_move():
    predicted = np.array([1.0, 2.0, 3.0, 4.0, 9.0, 9.0, 9.0, 9.0])
    actual = np.array([1.0, 2.0, 3.0, 4.0, 0.0, 0.0, 0.0, 0.0])
    dyads = np.array(["a"] * 4 + ["flat"] * 4)
    score, n = intensity.within_dyad_score(predicted, actual, dyads)
    # 'flat' never varies, so it has no ordering to get right and is skipped
    # rather than scored as a coin flip.
    assert n == 1
    assert score == pytest.approx(1.0)


def test_a_model_that_cannot_order_does_not_pass():
    folds = [{"within_dyad": -0.2, "within_dyad_persistence": 0.4,
              "rmse": 1.0, "rmse_persistence": 2.0}]
    passed, reason = intensity.passes_gate(folds)
    assert not passed and "not above zero" in reason


def test_a_model_that_orders_but_does_not_improve_magnitude_does_not_pass():
    # This is the shipped model's actual contribution — without it there is
    # nothing to add over persistence, so it must not ship.
    folds = [{"within_dyad": 0.42, "within_dyad_persistence": 0.42,
              "rmse": 3.3, "rmse_persistence": 3.2}]
    passed, reason = intensity.passes_gate(folds)
    assert not passed and "magnitude" in reason


def test_matching_persistence_ordering_with_better_error_passes():
    folds = [{"within_dyad": 0.4236, "within_dyad_persistence": 0.4253,
              "rmse": 2.548, "rmse_persistence": 3.210}]
    passed, reason = intensity.passes_gate(folds)
    assert passed and "rmse" in reason


def test_a_deviation_prediction_may_be_negative_but_a_level_may_not():
    x = np.array([[1.0, -5.0]])
    weights = np.array([0.0, 1.0])
    assert intensity.predict(x, weights, target="deviation")[0] < 0
    assert intensity.predict(x, weights, target="level")[0] == 0.0
    assert intensity.to_level(np.array([-9.0]), np.array([2.0]))[0] == 0.0


def test_the_scaler_is_fitted_on_training_rows_only():
    train = np.array([[1.0, 0.0], [1.0, 10.0]])
    mean, sd = intensity.scaler(train)
    assert mean[0] == 0.0 and sd[0] == 1.0, "the intercept is never scaled"
    assert mean[1] == pytest.approx(5.0)
    standardized = intensity.standardize(train, mean, sd)
    assert standardized[:, 1].mean() == pytest.approx(0.0)


# ── the artifact contract ────────────────────────────────────────────────────


def _artifact() -> dict[str, Any]:
    return registry.build_artifact(
        name="test",
        weights={1: [0.1, 0.2, 0.3]},
        scaler_mean=[0.0, 1.0, 2.0],
        scaler_sd=[1.0, 2.0, 3.0],
        target="deviation",
        folds=[],
        gate=(True, "ordering +0.4 vs +0.4; rmse 2.5 vs 3.2"),
        train_span=("1979-01-01", "2005-12-31"),
        residual_sd={1: 2.5},
        rows=100,
        dyads=10,
    )


def test_an_artifact_round_trips(tmp_path):
    written = registry.save(_artifact(), tmp_path / "test.json")
    loaded = registry.load("test", written)
    assert registry.weights_of(loaded) == {1: [0.1, 0.2, 0.3]}
    assert registry.residual_sd_of(loaded) == {1: 2.5}
    assert registry.scaler_of(loaded) == ([0.0, 1.0, 2.0], [1.0, 2.0, 3.0])


def test_an_artifact_edited_by_hand_is_refused(tmp_path):
    path = tmp_path / "test.json"
    registry.save(_artifact(), path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["weights"]["1"][1] = 99.0  # a weight nudged in a text editor
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(registry.ArtifactError, match="hash"):
        registry.load("test", path)


def test_an_artifact_from_a_different_feature_order_is_refused(tmp_path):
    path = tmp_path / "test.json"
    artifact = _artifact()
    artifact["features"] = ["intercept", "base_level", "level_now"]  # swapped
    artifact["hash"] = registry._digest(artifact)
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(registry.ArtifactError, match="positional"):
        registry.load("test", path)


def test_a_missing_artifact_names_the_script_that_makes_one(tmp_path):
    with pytest.raises(registry.ArtifactError, match="train_forecaster"):
        registry.load("absent", tmp_path / "absent.json")


def test_the_shipped_artifact_passed_its_gate():
    # The repo's committed model is the one the boot will freeze from. If it
    # ever lands with a failed gate, this fails here rather than on the surface.
    artifact = registry.load("intensity")
    assert artifact["gate_passed"], artifact["gate_reason"]
    assert artifact["features"] == list(feature_module.SHIPPED_FEATURES)
    assert set(registry.weights_of(artifact)) == set(feature_module.HORIZONS)


# ── the surfaces that serve it ───────────────────────────────────────────────


@pytest.fixture()
def graph_with_panel(tmp_path):
    """A graph holding one dyad with enough quarters to project."""
    import importlib.util
    from pathlib import Path

    from core import packs
    from core.graph import kuzu_store

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("seed_pack", root / "scripts" / "seed_pack.py")
    assert spec and spec.loader
    seed_pack = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_pack)

    path = tmp_path / "panel.kuzu"
    conn = kuzu_store.connect(path)
    try:
        seed_pack.seed(conn, packs.load("mena"))
        kuzu_store.merge_nodes(conn, "Dyad", [
            {"node_id": "dyad:test", "name": "Test Dyad", "ewma_baseline": -5.0,
             "actor_a_id": "actor:cow-630", "actor_b_id": "actor:cow-645"},
        ])
        events = [
            {
                "node_id": f"event:panel-{q}", "name": f"panel {q}",
                "event_time": f"{1990 + q // 4}-{(q % 4) * 3 + 1:02d}-15",
                "action_cameo_code": "190", "goldstein": -8.0,
                "quad_class": "material_conflict", "region_pack": "mena",
                "fidelity_tier": "deep_structured", "temporal_resolution": "day",
                "source_scale": "cow_hostility", "escalation_direction": "escalating",
                "escalation_magnitude": 9.0 if q % 6 == 0 else 1.0,
                "escalation_baseline": -5.0,
            }
            # Every other quarter only, so the panel has QUIET quarters to fill
            # — a fixture active in all 24 could not catch a zero-filling bug.
            for q in range(0, 24, 2)
        ]
        kuzu_store.merge_nodes(conn, "Event", events)
        kuzu_store.merge_edges(conn, "OF_DYAD", [
            {"src": e["node_id"], "dst": "dyad:test"} for e in events
        ])
    finally:
        kuzu_store.close(conn)
    return path


def test_the_panel_endpoints_serve_quarters_and_a_series(graph_with_panel, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KUZU_DB_PATH", str(graph_with_panel))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app
    from core.api.routers import dyads as dyads_router

    dyads_router._CACHE.clear()
    with TestClient(create_app()) as client:
        listed = client.get("/api/panel/dyads?region=mena").json()
        assert any(r["dyad_id"] == "dyad:test" for r in listed["rows"])

        series = client.get("/api/panel/dyads/dyad:test/series?region=mena").json()
        assert series["dyad_name"] == "Test Dyad"
        # Quiet quarters are rows, not gaps: events land in 12 quarters, the
        # panel spans all 23 from the first to the last.
        assert len(series["rows"]) == 23
        assert any(r["intensity"] == 0.0 for r in series["rows"])
        assert series["peak"] == 9.0

        missing = client.get("/api/panel/dyads/dyad:nope/series")
        assert missing.status_code == 404
        assert "history" in missing.json()["detail"]


def test_precedent_reports_episodes_and_says_when_markets_are_unmeasured(
    graph_with_panel, monkeypatch
):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KUZU_DB_PATH", str(graph_with_panel))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app
    from core.api.routers import dyads as dyads_router

    dyads_router._CACHE.clear()
    with TestClient(create_app()) as client:
        body = client.get("/api/precedent?dyad=dyad:test&region=mena").json()
        assert body["dyad_name"] == "Test Dyad"
        assert body["episodes"], "a dyad spiking every third quarter has precedent"
        for episode in body["episodes"]:
            assert len(episode["aftermath"]) == 9, "episodes without full aftermath are dropped"
        for row in body["fan"]:
            assert row["min"] <= row["median"] <= row["max"]
            assert row["n"] >= 1
        # No transmission run against this graph, so the honest answer is a
        # stated absence rather than an empty list the reader must interpret.
        assert body["markets"] == []
        assert body["markets_note"]
