"""The transmission engine. Synthetic prices, hand-computable numbers: no
network, no database, and every assertion checkable with a calculator.

What these pin is the engine's honesty, not its plumbing — a skip is a result,
an overlap is flagged rather than averaged away, first_mover follows the
calendar in force on the day, and the same panel always produces the same
number.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics

import pytest

from core.transmission import calendar as trading_calendar
from core.transmission import event_study

#: Alternating ±1% gives a zero mean and a known standard deviation, so the
#: abnormal return equals the raw return and the t-stat is computable by hand.
_ESTIMATION = [0.01, -0.01] * 60
_GAP = [0.0] * event_study.ESTIMATION_GAP_SESSIONS


def _sessions(
    calendar: str, first: dt.date, count: int, *, backwards: bool = False
) -> list[dt.date]:
    """`count` trading days from `first`, in either direction."""
    step = dt.timedelta(days=-1 if backwards else 1)
    out: list[dt.date] = []
    date = first
    while len(out) < count:
        if trading_calendar.is_trading_day(calendar, date):
            out.append(date)
        date += step
    return list(reversed(out)) if backwards else out


def _series(
    event_date: dt.date, pre: list[float], post: list[float], calendar: str = "us"
) -> list[dict[str, object]]:
    """A price path ANCHORED TO THE EVENT.

    `pre` are the returns before the event, `post` the returns from the first
    tradable session on or after it — so session 0 lands exactly where the
    engine will look for it, and the numbers stay hand-checkable.
    """
    session_zero = trading_calendar.first_session(calendar, event_date)
    post_dates = _sessions(calendar, session_zero, len(post))
    # One extra pre-session for the base price the first return is measured from.
    pre_dates = _sessions(
        calendar, session_zero - dt.timedelta(days=1), len(pre) + 1, backwards=True
    )
    dates, returns = pre_dates + post_dates, pre + post
    price = 100.0
    out = [{"obs_date": dates[0].isoformat(), "price": price}]
    for when, ret in zip(dates[1:], returns, strict=True):
        price *= 1.0 + ret
        out.append({"obs_date": when.isoformat(), "price": price})
    return out


def _market(
    ticker: str = "^GSPC",
    *,
    calendar: str = "us",
    inception: str = "2000-01-01",
    eras: str | None = None,
    freq: str | None = None,
) -> dict[str, str]:
    market = {
        "ticker": ticker, "trading_calendar": calendar, "inception_date": inception,
        "native_frequency": freq or '{"2000": "day"}',
    }
    if eras:
        market["calendar_eras"] = eras
    return market


def _event(node_id: str = "event:test", date: str = "2025-06-13") -> dict[str, str]:
    return {"node_id": node_id, "event_time": date}


# ── the CAR arithmetic ───────────────────────────────────────────────────────


def test_the_abnormal_return_is_the_raw_return_minus_the_baseline():
    # 120 estimation returns averaging zero, then a +5% session and a flat one.
    prices = _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.05, 0.0])
    effects, skips = event_study.compute_effects(
        _event(), [_market()], prices={"^GSPC": prices}
    )
    # Two post-event sessions exist, so only the two-session window computes;
    # the longer ones correctly report that they had nothing to measure.
    assert [e.window for e in effects] == ["car_0_1"]
    assert {s.window for s in skips} == {"car_0_3", "car_0_5"}
    car_0_1 = effects[0]

    sigma = statistics.stdev(_ESTIMATION)
    assert car_0_1.raw_return == pytest.approx(0.05)
    assert car_0_1.expected_return == pytest.approx(0.0, abs=1e-12)
    assert car_0_1.abnormal_return == pytest.approx(0.05)
    # CAR over k sessions is scaled by sigma·√k, so a two-session window
    # divides by √2 rather than by 2.
    assert car_0_1.t_stat == pytest.approx(0.05 / (sigma * math.sqrt(2)), rel=1e-9)
    assert 0.0 < car_0_1.p_value < 0.01


def test_a_nonzero_baseline_is_subtracted():
    # Every estimation session drifts +0.1%, so a +0.1% event session is
    # NORMAL: the abnormal return is zero even though the raw return is not.
    drift = [0.001] * 120
    prices = _series(dt.date(2025, 6, 13), drift + _GAP, [0.001, 0.001])
    effects, _ = event_study.compute_effects(
        _event(), [_market()], prices={"^GSPC": prices}
    )
    car_0_1 = next(e for e in effects if e.window == "car_0_1")
    assert car_0_1.raw_return == pytest.approx(0.002)
    assert car_0_1.expected_return == pytest.approx(0.002)
    assert car_0_1.abnormal_return == pytest.approx(0.0, abs=1e-12)


def test_the_engine_reports_a_fall_as_readily_as_a_rise():
    # The honesty rule: measure the realized effect, never assert a sign.
    prices = _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [-0.08, 0.0])
    effects, _ = event_study.compute_effects(
        _event(), [_market()], prices={"^GSPC": prices}
    )
    car_0_1 = next(e for e in effects if e.window == "car_0_1")
    assert car_0_1.abnormal_return == pytest.approx(-0.08)
    assert car_0_1.t_stat < 0


def test_each_window_spans_the_sessions_it_names():
    prices = _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.01] * 8)
    effects, _ = event_study.compute_effects(
        _event(), [_market()], prices={"^GSPC": prices}
    )
    spans = {e.window: e.raw_return for e in effects}
    assert spans["car_0_1"] == pytest.approx(0.02)   # 2 sessions
    assert spans["car_0_3"] == pytest.approx(0.04)   # 4 sessions
    assert spans["car_0_5"] == pytest.approx(0.06)   # 6 sessions


def test_the_same_panel_always_produces_the_same_numbers():
    prices = _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.05, 0.0])
    first, _ = event_study.compute_effects(_event(), [_market()], prices={"^GSPC": prices})
    second, _ = event_study.compute_effects(_event(), [_market()], prices={"^GSPC": prices})
    assert first == second


def test_the_method_line_can_be_used_to_recompute_the_number():
    prices = _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.05, 0.0])
    effects, _ = event_study.compute_effects(
        _event(), [_market()], prices={"^GSPC": prices}
    )
    method = effects[0].method
    for part in ("constant-mean", "est=120day", "gap=5", "car=sum-simple",
                 "session0=", "calendar=us", "dist="):
        assert part in method


# ── skips are results ────────────────────────────────────────────────────────


def test_a_market_that_did_not_exist_is_skipped_not_measured():
    # Tadawul in 1973: the honest answer is "there was no market", and it is
    # recorded rather than left absent.
    tasi = _market("^TASI.SR", calendar="gulf", inception="1985-01-01")
    effects, skips = event_study.compute_effects(
        _event(date="1973-10-17"), [tasi], prices={}
    )
    assert effects == []
    assert len(skips) == 1
    assert skips[0].status == "skipped_no_market"
    assert "did not exist" in skips[0].reason


def test_a_ticker_with_no_sessions_is_skipped_with_the_date_it_looked_for():
    prices = _series(dt.date(2024, 6, 13), _ESTIMATION, [])  # ends long before the event
    effects, skips = event_study.compute_effects(
        _event(date="2025-06-13"), [_market()], prices={"^GSPC": prices}
    )
    assert effects == []
    assert skips[0].status == "skipped_no_data"
    assert "2025-06-13" in skips[0].reason


def test_a_market_with_too_little_history_is_skipped():
    prices = _series(dt.date(2025, 6, 6), [0.01], [0.01, 0.01])
    effects, skips = event_study.compute_effects(
        _event(date="2025-06-06"), [_market()], prices={"^GSPC": prices}
    )
    assert effects == []
    assert "estimation observations" in skips[0].reason


def test_a_truncated_window_is_skipped_rather_than_shortened():
    # Only three post-event sessions exist: car_0_1 and car_0_3 compute,
    # car_0_5 is a skip. A six-session window measured over four would be a
    # different statistic wearing the same name.
    prices = _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.01] * 4)
    effects, skips = event_study.compute_effects(
        _event(), [_market()], prices={"^GSPC": prices}
    )
    assert {e.window for e in effects} == {"car_0_1", "car_0_3"}
    assert [s.window for s in skips] == ["car_0_5"]
    assert "needs 6 sessions" in skips[0].reason


def test_coarse_resolutions_say_they_are_not_built_yet():
    # A monthly-era market must not receive a daily number under a monthly
    # label; Phase 3 lands the coarse windows.
    shiller = _market("^GSPC", inception="1871-01-01", freq='{"1871": "month"}')
    effects, skips = event_study.compute_effects(
        _event(date="1912-06-13"), [shiller], prices={}
    )
    assert effects == []
    assert skips[0].resolution == "month"
    assert "Phase 3" in skips[0].reason


# ── calendars and first_mover ────────────────────────────────────────────────


def test_first_mover_follows_the_calendar_in_force_on_the_day():
    # Friday event: the Mon-Fri markets react that session, Tadawul cannot
    # until Sunday, and Dubai (Mon-Thu since 2022) not until Monday.
    gspc = _market("^GSPC", calendar="us")
    tasi = _market("^TASI.SR", calendar="gulf", inception="1985-01-01")
    dfm = _market("DFMGI.AE", calendar="gulf", inception="2000-03-26",
                  eras='{"2000": "gulf", "2022": "uae"}')
    prices = {
        "^GSPC": _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.01] * 8, "us"),
        "^TASI.SR": _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.01] * 8, "gulf"),
        "DFMGI.AE": _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.01] * 8, "uae"),
    }
    effects, _ = event_study.compute_effects(
        _event(date="2025-06-13"), [gspc, tasi, dfm], prices=prices
    )
    movers = {e.market_ticker: e.first_mover for e in effects if e.window == "car_0_1"}
    assert movers["^GSPC"] is True
    assert movers["^TASI.SR"] is False
    assert movers["DFMGI.AE"] is False


def test_a_sunday_event_makes_tadawul_the_first_mover():
    # The mirror case, and the reason the engine cannot use a same-day
    # benchmark: on Tadawul's Sunday session no US market has traded yet.
    gspc = _market("^GSPC", calendar="us")
    tasi = _market("^TASI.SR", calendar="gulf", inception="1985-01-01")
    prices = {
        "^GSPC": _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.01] * 8, "us"),
        "^TASI.SR": _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.01] * 8, "gulf"),
    }
    effects, _ = event_study.compute_effects(
        _event(date="2025-06-22"), [gspc, tasi], prices=prices
    )
    movers = {e.market_ticker: e.first_mover for e in effects if e.window == "car_0_1"}
    assert movers["^TASI.SR"] is True
    assert movers["^GSPC"] is False


def test_a_market_that_did_not_exist_cannot_win_first_mover():
    # first_mover is resolved over the markets alive at event time, so a
    # market skipped for non-existence does not shift the flag onto itself.
    gspc = _market("^GSPC", calendar="us")
    future = _market("FUTURE.X", calendar="gulf", inception="2030-01-01")
    prices = {"^GSPC": _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.01] * 8, "us")}
    effects, skips = event_study.compute_effects(
        _event(date="2025-06-13"), [gspc, future], prices=prices
    )
    assert all(e.first_mover for e in effects if e.market_ticker == "^GSPC")
    assert [s.market_ticker for s in skips] == ["FUTURE.X"]


# ── overlapping windows ──────────────────────────────────────────────────────


def test_an_event_inside_the_window_is_flagged_and_not_averaged():
    prices = _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.01] * 8)
    effects, _ = event_study.compute_effects(
        _event(node_id="event:first", date="2025-06-13"),
        [_market()],
        prices={"^GSPC": prices},
        other_event_dates={
            "event:first": dt.date(2025, 6, 13),
            "event:second": dt.date(2025, 6, 20),   # inside car_0_5, not car_0_1
        },
    )
    flags = {e.window: e.overlapping for e in effects}
    assert flags["car_0_1"] is False
    assert flags["car_0_5"] is True
    # Flagged, not dropped: the number is still there to be judged.
    assert all(e.abnormal_return is not None for e in effects)


def test_an_event_does_not_overlap_itself():
    prices = _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.01] * 8)
    effects, _ = event_study.compute_effects(
        _event(node_id="event:solo", date="2025-06-13"),
        [_market()],
        prices={"^GSPC": prices},
        other_event_dates={"event:solo": dt.date(2025, 6, 13)},
    )
    assert not any(e.overlapping for e in effects)


# ── the fidelity gradient ────────────────────────────────────────────────────


def test_native_resolution_is_read_per_era():
    gspc = _market("^GSPC", inception="1871-01-01",
                   freq='{"1871": "month", "1927": "day"}')
    assert event_study.native_resolution(gspc, dt.date(1912, 1, 1)) == "month"
    assert event_study.native_resolution(gspc, dt.date(2025, 1, 1)) == "day"


def test_the_finest_window_follows_the_era():
    gspc = _market("^GSPC", inception="1871-01-01",
                   freq='{"1871": "month", "1927": "day"}')
    assert event_study.finest_window(dt.date(1912, 1, 1), gspc) == "monthly"
    assert event_study.finest_window(dt.date(2025, 1, 1), gspc) == "car_0_1"


def test_an_event_before_every_era_raises_rather_than_guessing():
    market = _market("X", freq='{"2000": "day"}')
    with pytest.raises(event_study.StudyError, match="no native frequency"):
        event_study.native_resolution(market, dt.date(1905, 1, 1))


# ── degenerate inputs ────────────────────────────────────────────────────────


def test_a_flat_history_yields_no_t_stat_rather_than_a_division_by_zero():
    # Zero variance in the estimation window means the test statistic is
    # undefined; NaN is the honest answer and _finite() nulls it in the panel.
    prices = _series(dt.date(2025, 6, 13), [0.0] * 125, [0.05, 0.0])
    effects, _ = event_study.compute_effects(
        _event(), [_market()], prices={"^GSPC": prices}
    )
    car_0_1 = next(e for e in effects if e.window == "car_0_1")
    assert car_0_1.abnormal_return == pytest.approx(0.05)
    assert math.isnan(car_0_1.t_stat)
    assert math.isnan(car_0_1.p_value)
