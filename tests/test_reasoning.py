"""The deterministic reasoning core (Phase 5): structural pressure and its
retrodiction, regime-gated analogy retrieval, and the market-as-sensor loop.
Real embedded graphs, synthetic where the deep tier would be — every number
checkable by hand, no LLM anywhere."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from core import packs
from core.graph import kuzu_store
from core.reasoning import analogy, sensor_loop, structural
from core.reasoning.calibration import retrodict
from core.transmission.effects import write_effects
from core.transmission.event_study import EffectResult

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_pack = _load("seed_pack")


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "reasoning.kuzu"
    conn = kuzu_store.connect(path)
    try:
        seed_pack.seed(conn, packs.load("mena"))
        # A synthetic slice of deep history: rising CINC concentration into a
        # cluster of escalating conflicts — the Turchin shape, hand-built so
        # the pressure arithmetic is checkable.
        kuzu_store.merge_nodes(conn, "AttributeEstimate", [
            {"node_id": f"estimate:clout:cow-2:{year}", "attribute": "clout",
             "value_mean": 0.10 + 0.02 * (year - 1950), "value_std": 0.0,
             "as_of": f"{year}-12-31", "method": "cinc_seed"}
            for year in range(1950, 1960)
        ] + [
            {"node_id": f"estimate:clout:cow-630:{year}", "attribute": "clout",
             "value_mean": 0.10, "value_std": 0.0,
             "as_of": f"{year}-12-31", "method": "cinc_seed"}
            for year in range(1950, 1960)
        ])
        kuzu_store.merge_edges(conn, "HAS_ESTIMATE", [
            {"src": "actor:cow-2", "dst": f"estimate:clout:cow-2:{y}"}
            for y in range(1950, 1960)
        ] + [
            {"src": "actor:cow-630", "dst": f"estimate:clout:cow-630:{y}"}
            for y in range(1950, 1960)
        ])
        # A Bretton-Woods-era event: admissible to nothing modern.
        kuzu_store.merge_nodes(conn, "Event", [{
            "node_id": "event:test-1962", "name": "Synthetic 1962 crisis",
            "event_time": "1962-10-05", "action_cameo_code": "190",
            "goldstein": -10.0, "quad_class": "material_conflict",
            "region_pack": "mena", "fidelity_tier": "deep_structured",
            "temporal_resolution": "day", "source_scale": "cow_hostility",
            "escalation_direction": "escalating", "escalation_magnitude": 5.0,
            "escalation_baseline": -5.0,
        }])
        kuzu_store.merge_edges(conn, "INITIATED_BY", [
            {"src": "event:test-1962", "dst": "actor:cow-2", "source_id": "source:cow-mid"},
        ])
        kuzu_store.merge_edges(conn, "DIRECTED_AT", [
            {"src": "event:test-1962", "dst": "actor:cow-630", "source_id": "source:cow-mid"},
        ])
        kuzu_store.merge_edges(conn, "DERIVED_FROM", [
            {"src": "event:test-1962", "dst": "source:cow-mid"},
        ])
    finally:
        kuzu_store.close(conn)
    return path


# ── structural pressure ──────────────────────────────────────────────────────


def test_pressure_components_have_the_shapes_the_data_implies(db_path):
    components = structural.pressure_components(db_path)
    # Concentration rises as the US pulls away from a flat Iran.
    concentration = components["concentration"]
    assert concentration[1959] > concentration[1950]
    # The challenger/leader ratio FALLS over the same stretch.
    proximity = components["transition_proximity"]
    assert proximity[1959] < proximity[1950]
    assert all(0 <= v <= 1 for v in proximity.values())


def test_as_of_truncates_every_series(db_path):
    full = structural.pressure_components(db_path)
    truncated = structural.pressure_components(db_path, as_of="1955-12-31")
    assert max(truncated["concentration"]) == 1955
    assert max(full["concentration"]) == 1959
    # The modern spine exists in full but not in the 1955 view — where this
    # fixture holds no material conflict at all, and an empty series is the
    # correct answer, not a fabricated zero.
    assert max(full["conflict_intensity"]) >= 2025
    assert not truncated["conflict_intensity"] or max(truncated["conflict_intensity"]) <= 1955


def test_the_forecast_carries_the_boundary_and_no_likelihoods(db_path):
    forecast = structural.structural_forecast(db_path, region_pack="mena")
    assert forecast["mode"] == "long_horizon"
    assert forecast["boundary_statement"] == structural.BOUNDARY_STATEMENT
    assert forecast["scenarios"], "a scenario space is the output"
    for scenario in forecast["scenarios"]:
        assert scenario["likelihood"] is None  # long-horizon: never a dated point
        assert scenario["rationale"]
    for window in forecast["windows"]:
        assert window["level"] in {"elevated", "high"}
        assert window["start"] <= window["end"]
    assert all(0.0 <= v <= 1.0 for v in forecast["pressure"].values())


def test_the_forecast_is_deterministic(db_path):
    first = structural.structural_forecast(db_path, region_pack="mena")
    second = structural.structural_forecast(db_path, region_pack="mena")
    assert first == second


# ── retrodiction ─────────────────────────────────────────────────────────────


def test_retrodiction_reports_hits_beside_the_base_rate(db_path):
    report = retrodict(db_path, as_of="2015-01-01", region_pack="mena")
    assert report["boundary_statement"] == structural.BOUNDARY_STATEMENT
    assert report["base_rate"] is not None
    if report["flagged_years"]:
        assert report["hit_rate"] is not None
        assert set(report["hits"]) <= set(report["flagged_years"])
    # Both sides of the comparison are reported — never a bare verdict.
    assert "hot_years" in report and "method" in report


def test_retrodiction_refuses_a_horizon_it_cannot_see(db_path):
    report = retrodict(db_path, as_of="2030-01-01", region_pack="mena")
    assert report["hit_rate"] is None
    assert "insufficient" in report["verdict"]


# ── analogy: admissibility before similarity ─────────────────────────────────


def test_analogues_stay_inside_the_regime(db_path):
    rows = analogy.find_analogues(
        db_path, "event:mena-2025-midnight-hammer", region_pack="mena", k=10,
    )
    assert rows, "the modern spine offers admissible analogues"
    matched = {r["event_id"] for r in rows}
    # 1973 is fiat-floating like 2025 — admissible. 1962 is Bretton Woods —
    # refused by the gate no matter how similar its shape.
    assert "event:test-1962" not in matched
    for row in rows:
        assert 0.0 <= row["similarity"] <= 1.0
        assert row["regime_matched"] == "monetary_order"
        assert row["rationale"]


def test_analogues_persist_and_rank_deterministically(db_path):
    first = analogy.find_analogues(
        db_path, "event:mena-2025-midnight-hammer", region_pack="mena", k=3,
    )
    second = analogy.find_analogues(
        db_path, "event:mena-2025-midnight-hammer", region_pack="mena", k=3,
    )
    assert [r["node_id"] for r in first] == [r["node_id"] for r in second]
    conn = kuzu_store.connect(db_path, read_only=True)
    try:
        count = kuzu_store.query(
            conn, "MATCH (a:Analogue) RETURN count(*) AS n"
        )[0]["n"]
    finally:
        kuzu_store.close(conn)
    assert count == len(first)  # re-running merged onto itself


def test_an_unknown_query_event_raises(db_path):
    with pytest.raises(KeyError, match="no such event"):
        analogy.find_analogues(db_path, "event:nope", region_pack="mena")


# ── the sensor loop ──────────────────────────────────────────────────────────


def _effect(event: str, p_value: float, t_stat: float = -5.0) -> EffectResult:
    return EffectResult(
        event_node_id=event, market_ticker="BZ=F", window="car_0_1",
        resolution="day", raw_return=-0.1, expected_return=0.0,
        abnormal_return=-0.1, t_stat=t_stat, p_value=p_value,
        first_mover=False, overlapping=False, method="test",
    )


def test_a_significant_surprise_updates_resolve_for_both_parties(db_path):
    conn = kuzu_store.connect(db_path)
    try:
        pack = packs.load("mena")
        write_effects(
            conn, [_effect("event:mena-2025-midnight-hammer", p_value=0.001)],
            market_node_ids={m["ticker"]: m["id"] for m in pack.markets},
            source_id="source:yfinance",
        )
        written = sensor_loop.update_from_effect(conn, "event:mena-2025-midnight-hammer")
        assert len(written) == 2  # initiator and target
        for row in written:
            assert row["method"] == "sensor_update"
            assert row["value_mean"] == pytest.approx(0.1 * 3.0)  # capped |t| * step
            assert row["value_std"] < 1.0  # a realized observation tightens belief
        # The estimates LAND in the graph, linked to their actors.
        linked = kuzu_store.query(
            conn,
            "MATCH (:Actor)-[:HAS_ESTIMATE]->(s:AttributeEstimate) "
            "WHERE s.method = 'sensor_update' RETURN count(*) AS n",
        )[0]["n"]
        assert linked == 2
    finally:
        kuzu_store.close(conn)


def test_an_insignificant_effect_updates_nothing(db_path):
    conn = kuzu_store.connect(db_path)
    try:
        pack = packs.load("mena")
        write_effects(
            conn, [_effect("event:mena-2025-rising-lion", p_value=0.6, t_stat=-0.5)],
            market_node_ids={m["ticker"]: m["id"] for m in pack.markets},
            source_id="source:yfinance",
        )
        assert sensor_loop.update_from_effect(conn, "event:mena-2025-rising-lion") == []
    finally:
        kuzu_store.close(conn)


def test_an_unmeasured_event_is_refused(db_path):
    # NEVER from the model's own predictions — and with no realized outcome
    # at all, the loop has nothing it is allowed to learn from.
    conn = kuzu_store.connect(db_path)
    try:
        with pytest.raises(ValueError, match="REALIZED"):
            sensor_loop.update_from_effect(conn, "event:mena-1973-embargo")
    finally:
        kuzu_store.close(conn)


# ── near-term base rates ─────────────────────────────────────────────────────


def test_near_term_scenarios_are_base_rates_that_sum_to_one(db_path):
    from core.reasoning import forecasting

    payload = forecasting.forecast(
        db_path, "Where does Iran's confrontation arc go?", region_pack="mena",
    )
    assert payload["mode"] == "near_term"
    assert payload["scenarios"], "focal dyads exist in the seeded spine"
    # Scenario PAIRS: likelihoods complement within each focal dyad.
    by_dyad: dict[str, float] = {}
    for scenario in payload["scenarios"]:
        assert scenario["likelihood"] is not None
        assert 0.0 <= scenario["likelihood"] <= 1.0
        dyad = scenario["scenario_name"].split(":", 1)[1]
        by_dyad[dyad] = by_dyad.get(dyad, 0.0) + scenario["likelihood"]
        assert "recount" in scenario["rationale"] or "complement" in scenario["rationale"]
    for total in by_dyad.values():
        assert total == pytest.approx(1.0)
    # Frozen inputs carry the counting, so a later scorer can audit it.
    frozen = payload["frozen_inputs"]
    assert frozen["episodes"] >= frozen["continuations"] >= 0
    assert payload["as_of"] == frozen["as_of"]


def test_near_term_is_deterministic_and_clock_free(db_path):
    from core.reasoning import forecasting

    first = forecasting.forecast(db_path, "q", region_pack="mena")
    second = forecasting.forecast(db_path, "q", region_pack="mena")
    assert first == second
    assert "generated_at" not in first  # the caller stamps at freeze time
