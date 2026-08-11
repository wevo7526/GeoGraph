from __future__ import annotations

import pytest

from core.reasoning import calibration


def test_brier_anchors():
    assert calibration.brier_score([(1.0, True), (0.0, False)]) == 0.0
    assert calibration.brier_score([(0.0, True)]) == 1.0
    assert calibration.brier_score([(0.5, True), (0.5, False)]) == pytest.approx(0.25)


def test_brier_rejects_bad_probabilities():
    with pytest.raises(ValueError):
        calibration.brier_score([(1.5, True)])
    with pytest.raises(ValueError):
        calibration.brier_score([])


def test_score_forecast_skips_unresolved_scenarios():
    scenarios = [
        {"scenario_name": "escalation", "likelihood": 0.7},
        {"scenario_name": "detente", "likelihood": 0.3},
    ]
    score = calibration.score_forecast(scenarios, {"escalation": True})
    assert score == pytest.approx(0.09)


def test_long_horizon_shapes_are_refused_not_scored():
    # A scenario without a likelihood is the long-horizon shape; point
    # calibration does not apply and pretending it does would be the exact
    # dishonesty the spec forbids.
    with pytest.raises(ValueError, match="retrodiction"):
        calibration.score_forecast(
            [{"scenario_name": "pressure-window", "likelihood": None}],
            {"pressure-window": True},
        )
