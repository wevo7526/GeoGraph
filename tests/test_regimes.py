"""Regime segmentation: complete over the archive, gapless, and correct on
the dates everyone will check first."""

from __future__ import annotations

import pytest

from core.reasoning import regimes


def test_both_kinds_exist():
    assert set(regimes.segmentation()) == {"monetary_order", "polarity_epoch"}


@pytest.mark.parametrize("kind", ["monetary_order", "polarity_epoch"])
def test_no_gaps_from_1905_to_now(kind):
    entries = regimes.segmentation()[kind]
    assert entries[0]["start"] <= "1905-01-01"
    for prev, cur in zip(entries, entries[1:], strict=False):
        assert prev["end"] == cur["start"], f"gap between {prev['id']} and {cur['id']}"
    assert entries[-1]["end"] is None, f"{kind} has no current regime"


def test_the_dates_everyone_checks():
    assert regimes.regime_at("1955-06-01", "monetary_order")["id"] == "bretton-woods"
    assert regimes.regime_at("1955-06-01", "polarity_epoch")["id"] == "bipolar-cold-war"
    assert regimes.regime_at("1912-01-01", "monetary_order")["id"] == "classical-gold-standard"
    assert regimes.regime_at("2026-01-01", "monetary_order")["id"] == "fiat-floating"
    assert regimes.regime_at("2026-01-01", "polarity_epoch")["id"] == "multipolar-drift"


def test_boundary_days_belong_to_the_new_regime():
    assert regimes.regime_at("1971-08-15", "monetary_order")["id"] == "fiat-floating"


def test_before_the_archive_is_none_not_a_guess():
    assert regimes.regime_at("1809-01-01", "monetary_order") is None


def test_comparable_is_the_admissibility_gate():
    assert regimes.comparable("1950-01-01", "1965-01-01")          # both Bretton Woods
    assert not regimes.comparable("1950-01-01", "1995-01-01")      # across the Nixon shock
    assert not regimes.comparable("1809-01-01", "1950-01-01")      # outside the archive


def test_unknown_kind_raises():
    with pytest.raises(KeyError):
        regimes.regime_at("1950-01-01", "vibe_epoch")


def test_as_nodes_shape_matches_the_regime_table():
    rows = regimes.as_nodes()
    assert all(row["node_id"].startswith("regime:") for row in rows)
    assert {row["kind"] for row in rows} == {"monetary_order", "polarity_epoch"}
