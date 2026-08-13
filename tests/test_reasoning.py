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


@pytest.fixture()
def dense_db_path(tmp_path):
    """An archive DENSE enough for the event-derived components to be
    measurements rather than anecdotes.

    The real archive's density is uneven by construction — the GDELT wire runs
    1979–2005 and the years after it hold only a curated spine — so the sample
    floor in structural.py drops most of the small fixture above. This one
    carries a wire-like twelve events a year with capability estimates over the
    same span, which is what the pressure composite is designed to read.
    """
    path = tmp_path / "dense.kuzu"
    years = range(1950, 2026)
    hot = set(range(1965, 1976)) | set(range(2000, 2011))
    conn = kuzu_store.connect(path)
    try:
        seed_pack.seed(conn, packs.load("mena"))
        kuzu_store.merge_nodes(conn, "AttributeEstimate", [
            {"node_id": f"estimate:clout:{actor}:{year}", "attribute": "clout",
             "value_mean": mean, "value_std": 0.0,
             "as_of": f"{year}-12-31", "method": "cinc_seed"}
            for year in years
            for actor, mean in (
                ("cow-2", 0.30 + 0.001 * (year - 1950)),
                ("cow-630", 0.10 + 0.002 * (year - 1950)),
                ("cow-645", 0.05),
            )
        ])
        kuzu_store.merge_edges(conn, "HAS_ESTIMATE", [
            {"src": f"actor:{actor}", "dst": f"estimate:clout:{actor}:{year}"}
            for year in years for actor in ("cow-2", "cow-630", "cow-645")
        ])
        # Twelve events a year; the escalating share and the conflict intensity
        # both rise inside the hot stretches, which is the shape retrodiction
        # is meant to catch.
        kuzu_store.merge_nodes(conn, "Event", [
            {
                "node_id": f"event:dense-{year}-{i:02d}",
                "name": f"Synthetic {year} #{i}",
                "event_time": f"{year}-{1 + i % 12:02d}-15",
                "action_cameo_code": "190",
                "goldstein": -9.0 if year in hot else -4.0,
                "quad_class": "material_conflict",
                "region_pack": "mena",
                "fidelity_tier": "deep_structured",
                "temporal_resolution": "day",
                "source_scale": "cow_hostility",
                "escalation_direction": (
                    "escalating" if i % 12 < (8 if year in hot else 3) else "stable"
                ),
                "escalation_magnitude": 2.0,
                "escalation_baseline": -5.0,
            }
            for year in years for i in range(12)
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


def test_as_of_truncates_every_series(dense_db_path):
    full = structural.pressure_components(dense_db_path)
    truncated = structural.pressure_components(dense_db_path, as_of="1980-12-31")
    for name in ("concentration", "transition_proximity", "conflict_intensity"):
        assert max(truncated[name]) <= 1980 < max(full[name]), name


def test_a_thin_window_yields_no_measurement_at_all(db_path):
    # The curated spine is a dozen events across 120 years. Its five-year
    # windows hold single figures, so they produce NO share and NO intensity —
    # dropped, never averaged into a number that would then be percentile-
    # ranked against the wire era's thousands. This is the fix for a composite
    # that read 0.93 (its all-time high) off two events in 2025.
    components = structural.pressure_components(db_path)
    assert components["conflict_intensity"] == {}
    assert components["escalation_share"] == {}
    # …and the capability components, which have real per-year data, survive.
    assert components["concentration"]


def test_the_composite_is_the_same_composite_in_every_year(dense_db_path):
    # A mean over "whatever component exists this year" silently changes
    # definition where a source ends. Every pressure year must therefore hold
    # all four components; the years that cannot are reported as coverage gaps
    # naming what was missing, not quietly averaged over fewer terms.
    forecast = structural.structural_forecast(dense_db_path, region_pack="mena")
    components = forecast["components"]
    for year in forecast["pressure"]:
        for name, series in components.items():
            assert str(year) in series or int(year) in series, (year, name)
    for missing in forecast["coverage"].values():
        assert missing, "a coverage row exists only to name what was absent"
        assert int(list(forecast["coverage"])[0]) not in forecast["pressure"]
    span = forecast["pressure_span"]
    assert span and span[0] <= span[1]


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


def test_retrodiction_reports_hits_beside_the_base_rate(dense_db_path):
    report = retrodict(dense_db_path, as_of="2015-01-01", region_pack="mena")
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


def test_dyads_with_different_records_get_different_likelihoods():
    from core.reasoning import forecasting

    # Two dyads, opposite histories, inside one monetary order: one escalates
    # in consecutive quarters for years, the other twice a decade apart. A
    # pooled rate hands both the same number — which is what the frozen MENA
    # call did, reading an identical 0.9347 for three unrelated dyads.
    rows = []
    for i, (dyad, years, months) in enumerate((
        ("dyad:chronic", range(1990, 2004), (1, 4, 7, 10)),
        # Five episodes, each more than the 3-year horizon from the next, so
        # none of them continues.
        ("dyad:rare", (1980, 1986, 1992, 1998, 2004), (6,)),
    )):
        for year in years:
            for month in months:
                rows.append({
                    "dyad_id": dyad, "dyad_name": dyad, "baseline": -8.0 - i,
                    "event_id": f"event:{dyad}-{year}-{month}",
                    "event_time": f"{year}-{month:02d}-15",
                    "direction": "escalating", "magnitude": 9.0,
                    "region_pack": "mena",
                })
    payload = forecasting.forecast_from_rows(rows, "q", region_pack="mena")
    rates = {
        s["scenario_name"].split(":", 1)[1]: s["likelihood"]
        for s in payload["scenarios"]
        if s["scenario_name"].startswith("further_escalation")
    }
    assert rates["dyad:chronic"] > rates["dyad:rare"], rates
    # Both are still recomputable by hand from the frozen counts.
    counts = payload["frozen_inputs"]["dyad_counts"]
    assert counts["dyad:chronic"][1] > counts["dyad:rare"][1]


def test_a_routine_escalation_is_not_an_episode():
    from core.reasoning import forecasting

    # Departures below the in-regime top decile do not open an episode. Without
    # this, a dyad continuously in the wire scored ~99% on every horizon
    # because "another escalating quarter within 3y" is what being in the wire
    # MEANS — the estimate measured coverage, not conflict.
    chatter = [
        {
            "dyad_id": "dyad:noisy", "dyad_name": "noisy", "baseline": -6.0,
            "event_id": f"event:noisy-{year}-{month}",
            "event_time": f"{year}-{month:02d}-15",
            "direction": "escalating", "magnitude": 0.5, "region_pack": "mena",
        }
        for year in range(1990, 2005) for month in (1, 4, 7, 10)
    ]
    ruptures = [
        {
            "dyad_id": "dyad:noisy", "dyad_name": "noisy", "baseline": -6.0,
            "event_id": f"event:rupture-{year}", "event_time": f"{year}-02-15",
            "direction": "escalating", "magnitude": 20.0, "region_pack": "mena",
        }
        for year in range(1980, 2008, 4)
    ]
    payload = forecasting.forecast_from_rows(chatter + ruptures, "q", region_pack="mena")
    frozen = payload["frozen_inputs"]
    # 67 escalating events, 7 of them real departures — the top decile lands on
    # the ruptures and the sixty quarters of chatter open no episode at all.
    assert frozen["significance_threshold"] == 20.0
    assert frozen["episodes"] == len(ruptures)
    # Every rupture is four years from the next, past the 3-year horizon.
    assert frozen["continuations"] == 0


def test_a_dyad_the_archive_never_watched_escalate_is_not_focal():
    from core.reasoning import forecasting

    # A dyad whose only claim is a very negative baseline used to lead the
    # forecast on zero episodes, presenting the pooled prior as a finding
    # about it.
    rows = [
        {
            "dyad_id": "dyad:evidenced", "dyad_name": "evidenced", "baseline": -5.0,
            "event_id": f"event:e-{year}", "event_time": f"{year}-03-15",
            "direction": "escalating", "magnitude": 9.0, "region_pack": "mena",
        }
        for year in range(1990, 2000)
    ] + [
        {
            "dyad_id": "dyad:silent", "dyad_name": "silent", "baseline": -10.0,
            "event_id": "event:s-1995", "event_time": "1995-03-15",
            "direction": "stable", "magnitude": 0.1, "region_pack": "mena",
        }
    ]
    payload = forecasting.forecast_from_rows(rows, "q", region_pack="mena")
    named = {s["scenario_name"].split(":", 1)[1] for s in payload["scenarios"]}
    assert named == {"dyad:evidenced"}


def test_near_term_is_deterministic_and_clock_free(db_path):
    from core.reasoning import forecasting

    first = forecasting.forecast(db_path, "q", region_pack="mena")
    second = forecasting.forecast(db_path, "q", region_pack="mena")
    assert first == second
    assert "generated_at" not in first  # the caller stamps at freeze time


# ── freezing (build-spec §17: frozen at generation, scorable later) ──────────


def test_freezing_persists_both_modes_with_recountable_inputs(db_path):
    import json

    freeze = _load("run_forecasts").freeze

    written = freeze(db_path, region_pack="mena")
    assert {w["mode"] for w in written} == {"near_term", "long_horizon"}
    # Same archive state -> same node ids: a re-freeze merges onto itself.
    again = freeze(db_path, region_pack="mena")
    assert [w["node_id"] for w in again] == [w["node_id"] for w in written]

    conn = kuzu_store.connect(db_path, read_only=True)
    try:
        rows = kuzu_store.query(
            conn,
            "MATCH (f:Forecast) RETURN f.node_id AS node_id, f.mode AS mode, "
            "f.scenarios_json AS scenarios, f.frozen_inputs_json AS inputs, "
            "f.boundary_statement AS boundary",
        )
    finally:
        kuzu_store.close(conn)
    assert len(rows) == 2
    by_mode = {r["mode"]: r for r in rows}
    long_inputs = json.loads(by_mode["long_horizon"]["inputs"])
    assert long_inputs["pressure"], "the trajectory rides in the frozen inputs"
    assert by_mode["long_horizon"]["boundary"]
    near = json.loads(by_mode["near_term"]["scenarios"])
    assert all(s["likelihood"] is not None for s in near)


# ── the paper book: frozen implications, marked ──────────────────────────────


#: The MENA books, restated here BY HAND so the blend assertions stay
#: hand-derivable — and checked against the pack so the two cannot drift.
_ESCALATION = {"BZ=F": 0.40, "GC=F": 0.20, "^TASI.SR": -0.20, "DFMGI.AE": -0.20}
_REVERSION = {"^TASI.SR": 0.50, "DFMGI.AE": 0.50}


def test_the_books_come_from_the_pack_not_from_core():
    from core import packs

    books = packs.load("mena").paper_books
    assert books == {"escalation": _ESCALATION, "reversion": _REVERSION}
    # And the second region declares its OWN translation — strait risk, not
    # an oil shock. Different tickers is the point.
    china = packs.load("china").paper_books
    assert china is not None
    assert "BZ=F" not in china["escalation"]
    assert set(china["escalation"]) & {"^TWII", "^HSI"}


def test_the_paper_book_blends_the_books_by_the_frozen_likelihood():
    from core.reasoning import paper

    p, net = paper.build_book(
        [
            {"scenario_name": "further_escalation:dyad:a", "likelihood": 0.3},
            {"scenario_name": "reversion_to_baseline:dyad:a", "likelihood": 0.7},
        ],
        escalation_book=_ESCALATION,
        reversion_book=_REVERSION,
    )
    assert p == 0.3
    # Net = p*escalation + (1-p)*reversion, by hand:
    assert net["BZ=F"] == pytest.approx(0.12)          # 0.3*0.4
    assert net["^TASI.SR"] == pytest.approx(0.29)      # 0.3*-0.2 + 0.7*0.5
    assert net["GC=F"] == pytest.approx(0.06)
    assert net["DFMGI.AE"] == pytest.approx(0.29)


def test_the_paper_book_refuses_the_long_horizon_shape():
    from core.reasoning import paper

    with pytest.raises(ValueError, match="NEAR-TERM"):
        paper.build_book(
            [{"scenario_name": "pressure_release", "likelihood": None}],
            escalation_book=_ESCALATION,
            reversion_book=_REVERSION,
        )


def test_marking_enters_after_the_cutoff_and_skips_the_unfillable():
    from core.reasoning import paper

    series = {
        "BZ=F": [
            {"obs_date": "2025-06-20", "price": 70.0},   # BEFORE cutoff: not a fill
            {"obs_date": "2025-06-23", "price": 80.0},
            {"obs_date": "2025-08-01", "price": 88.0},
        ],
        "GC=F": [{"obs_date": "2025-06-23", "price": 3000.0}],  # one close: no mark
    }
    report = paper.mark_book(
        {"BZ=F": 0.5, "GC=F": 0.2, "^TASI.SR": 0.1},
        series,
        entry_after="2025-06-22",
    )
    marked = {p["ticker"]: p for p in report["positions"]}
    brent = marked["BZ=F"]
    assert brent["entry_date"] == "2025-06-23"          # first close AFTER cutoff
    assert brent["pnl_usd"] == pytest.approx(0.5 * 1_000_000 * (88 / 80 - 1), abs=0.01)
    assert marked["GC=F"]["status"] == "skipped"
    assert marked["^TASI.SR"]["status"] == "skipped"
    assert report["pnl_usd"] == brent["pnl_usd"]
    assert "not advice" in paper.method_for(_ESCALATION, _REVERSION)


def test_mark_through_bounds_the_mark_for_the_backtest():
    from core.reasoning import paper

    series = {
        "BZ=F": [
            {"obs_date": "2025-06-23", "price": 80.0},
            {"obs_date": "2025-08-01", "price": 88.0},
            {"obs_date": "2025-11-15", "price": 120.0},  # NEXT quarter — unseen
        ],
    }
    report = paper.mark_book(
        {"BZ=F": 1.0}, series, entry_after="2025-06-22", mark_through="2025-09-30"
    )
    position = report["positions"][0]
    assert position["mark_date"] == "2025-08-01"
    assert position["mark"] == 88.0


def test_the_paper_endpoint_is_honest_without_a_panel(db_path, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    freeze = _load("run_forecasts").freeze
    written = freeze(db_path, region_pack="mena")
    near = next(w["node_id"] for w in written if w["mode"] == "near_term")

    monkeypatch.setenv("KUZU_DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        response = client.get(f"/api/forecasts/{near}/paper")
        assert response.status_code == 503  # no panel, no invented fills
        long = near.replace("near-term", "long-horizon")
        assert client.get(f"/api/forecasts/{long}/paper").status_code == 400
