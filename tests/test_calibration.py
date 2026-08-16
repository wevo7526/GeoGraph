from __future__ import annotations

from typing import Any

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


# ── the calibration walk (2026-08-15) ───────────────────────────────────────


def _episode_rows() -> list[dict[str, Any]]:
    """A synthetic archive: one dyad that keeps escalating, one that stops."""
    rows: list[dict[str, Any]] = []
    for year in range(1990, 2027):
        for quarter, month in enumerate(("03", "06", "09", "12")):
            rows.append({
                "event_id": f"e-hot-{year}-{month}",
                "event_time": f"{year}-{month}-15",
                "dyad_id": "dyad:hot",
                "dyad_name": "Hot – Pair",
                "direction": "escalating",
                "magnitude": 6.0 + quarter,
                "baseline": -2.0,
                "region_pack": "test",
            })
            if year < 2000:
                rows.append({
                    "event_id": f"e-calm-{year}-{month}",
                    "event_time": f"{year}-{month}-15",
                    "dyad_id": "dyad:calm",
                    "dyad_name": "Calm – Pair",
                    "direction": "escalating" if year < 1995 else "stable",
                    "magnitude": 5.0,
                    "baseline": -1.0,
                    "region_pack": "test",
                })
    return rows


def test_the_walk_scores_only_closed_horizons():
    # A cutoff whose three-year window still runs past the archive's edge would
    # be scored against a future that has not happened — that is how a
    # walk-forward quietly becomes a lookahead.
    from core.reasoning import calibration

    rows = _episode_rows()
    out = calibration.walk(rows, region_pack="test", horizon_years=3)
    assert out["cutoffs"] > 8
    last_event = max(str(r["event_time"]) for r in rows)
    assert out["span"][1][:4] <= str(int(last_event[:4]) - 3)


def test_the_walk_scores_one_call_per_dyad_not_a_claim_and_its_complement():
    # THE FLAW THE FIRST WALK HAD. Each focal dyad names `further_escalation`
    # at p AND `reversion_to_baseline` at 1 − p: the same claim twice. Scoring
    # both forces the sample's base rate to exactly 0.5 whatever the world did
    # and credits the estimator for arithmetic — measured as an apparent skill
    # of 0.76 over a "base rate" that was an artifact of the pairing.
    from core.reasoning import calibration

    scenarios = [
        {"scenario_name": "further_escalation:dyad:hot", "likelihood": 0.8},
        {"scenario_name": "reversion_to_baseline:dyad:hot", "likelihood": 0.2},
    ]
    episodes = {"dyad:hot": [2000 * 4 + 2]}
    independent = calibration.near_term_outcomes(
        scenarios, episodes, as_of="2000-03-31", horizon_quarters=12,
    )
    assert list(independent) == ["further_escalation:dyad:hot"]
    both = calibration.near_term_outcomes(
        scenarios, episodes, as_of="2000-03-31", horizon_quarters=12,
        independent_only=False,
    )
    assert len(both) == 2 and both["reversion_to_baseline:dyad:hot"] is False


def test_the_walk_reports_the_recent_era_beside_the_whole_archive():
    # Density is coverage, not history. The whole-walk number is dominated by
    # the sparse deep past; a reader deciding whether to believe today's call
    # wants the recent one — which is how the estimator's NEGATIVE recent skill
    # (china -0.74, mena -0.19 on 2026-08-15) became visible at all.
    from core.reasoning import calibration

    out = calibration.walk(_episode_rows(), region_pack="test", horizon_years=3)
    assert "recent" in out and out["recent"]["years"] == 20
    for block in (out, out["recent"]):
        if not block.get("calls"):
            continue
        assert 0.0 <= block["brier"] <= 1.0
        # Skill is measured against predicting the sample's own frequency, so
        # a negative number is a real verdict rather than a broken one.
        assert block["skill"] is None or block["skill"] <= 1.0
        for band in block["reliability"]:
            assert 0.0 <= band["observed_rate"] <= 1.0
            assert band["calls"] > 0


def test_a_walk_with_too_few_closed_cutoffs_refuses_to_report_a_score():
    from core.reasoning import calibration

    thin = [{
        "event_id": "e1", "event_time": "2020-01-01", "dyad_id": "dyad:x",
        "dyad_name": "X – Y", "direction": "escalating", "magnitude": 5.0,
        "baseline": -1.0, "region_pack": "test",
    }]
    out = calibration.walk(thin, region_pack="test")
    assert "brier" not in out and "too few" in out["note"]
