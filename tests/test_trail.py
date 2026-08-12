"""The forecast trail's calibration pass: outcomes resolved by the same
episode arithmetic the base rates were counted with, open horizons left
visibly open, and retrodictions attached to the long-horizon nodes."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from core import packs
from core.graph import kuzu_store
from core.reasoning import calibration

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_outcomes_resolve_by_episode_quarters_and_brier_follows():
    score_forecasts = _load("score_forecasts")
    scenarios = [
        {"scenario_name": "further_escalation:dyad:a--b", "likelihood": 0.8},
        {"scenario_name": "reversion_to_baseline:dyad:a--b", "likelihood": 0.2},
    ]
    # The dyad escalated again two quarters after the cutoff (2020-Q4 ->
    # 2021-Q2), inside a 12-quarter horizon: escalation TRUE, reversion FALSE.
    episodes = {"dyad:a--b": [2020 * 4 + 3, 2021 * 4 + 1]}
    outcomes = score_forecasts._near_term_outcomes(
        scenarios, episodes, as_of="2020-11-15", horizon_quarters=12
    )
    assert outcomes == {
        "further_escalation:dyad:a--b": True,
        "reversion_to_baseline:dyad:a--b": False,
    }
    # Brier by hand: ((0.8-1)^2 + (0.2-0)^2) / 2.
    assert calibration.score_forecast(scenarios, outcomes) == pytest.approx(0.04)

    # An episode BEYOND the horizon does not resolve the scenario true.
    outside = {"dyad:a--b": [2020 * 4 + 3, 2026 * 4 + 0]}
    outcomes = score_forecasts._near_term_outcomes(
        scenarios, outside, as_of="2020-11-15", horizon_quarters=12
    )
    assert outcomes["further_escalation:dyad:a--b"] is False


def test_the_scoring_pass_leaves_open_horizons_open(tmp_path, monkeypatch, capsys):
    seed_pack = _load("seed_pack")
    path = tmp_path / "trail.kuzu"
    conn = kuzu_store.connect(path)
    try:
        seed_pack.seed(conn, packs.load("mena"))
    finally:
        kuzu_store.close(conn)
    _load("run_forecasts").freeze(path, region_pack="mena")

    monkeypatch.setenv("KUZU_DB_PATH", str(path))
    _load("score_forecasts").main()
    printed = capsys.readouterr().out
    assert "horizon open" in printed  # the fresh near-term call is NOT scored

    from fastapi.testclient import TestClient

    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        rows = client.get("/api/forecasts?region=mena").json()["rows"]
        by_mode = {r["mode"]: r for r in rows}
        # Unscored is null, never zero — an open question is not a grade.
        assert by_mode["near_term"]["brier_score"] is None
        # The long-horizon node carries its retrodiction (whatever verdict the
        # thin test archive supports — attaching the record IS the contract).
        assert by_mode["long_horizon"]["retrodiction"] is not None
        assert by_mode["long_horizon"]["retrodiction"]["as_of"].endswith("-12-31")
