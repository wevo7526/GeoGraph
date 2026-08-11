"""Classifier Head B — escalation is relational, and these tests pin the
behaviours that make it so."""

from __future__ import annotations

import pytest

from core.classifier import escalation


def test_first_event_is_its_own_baseline():
    result = escalation.classify(-5.0, None)
    assert result["escalation_baseline"] == -5.0
    assert result["escalation_direction"] == "stable"
    assert result["escalation_magnitude"] == 0.0


def test_drop_below_baseline_escalates():
    result = escalation.classify(-8.0, -3.0)
    assert result["escalation_direction"] == "escalating"
    assert result["escalation_magnitude"] == 5.0


def test_rise_above_baseline_deescalates():
    result = escalation.classify(1.0, -6.0)
    assert result["escalation_direction"] == "deescalating"
    assert result["escalation_magnitude"] == 7.0


def test_noise_band_is_stable():
    assert escalation.classify(-3.3, -3.0)["escalation_direction"] == "stable"


def test_ewma_moves_toward_the_score():
    baseline = escalation.update_baseline(-2.0, -10.0, alpha=0.25)
    assert baseline == pytest.approx(-4.0)
    assert escalation.update_baseline(None, -7.0) == -7.0


def test_same_score_different_dyads_different_meaning():
    # The point of the design: -6 is business as usual for a rivalry and a
    # rupture for an alliance.
    tracker = escalation.DyadTracker()
    for _ in range(6):
        tracker.observe("dyad:us-iran", -6.0)
    for _ in range(6):
        tracker.observe("dyad:us-uk", 4.0)
    rivalry = tracker.observe("dyad:us-iran", -6.0)
    alliance = tracker.observe("dyad:us-uk", -6.0)
    assert rivalry["escalation_direction"] == "stable"
    assert alliance["escalation_direction"] == "escalating"
    assert alliance["escalation_magnitude"] > 9.0


def test_tracker_folds_events_in_order():
    tracker = escalation.DyadTracker(alpha=0.5)
    tracker.observe("d", -2.0)
    tracker.observe("d", -6.0)
    assert tracker.baseline("d") == pytest.approx(-4.0)
