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
from typing import Any

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


def test_a_coarse_era_with_no_panel_rows_is_a_skip_at_that_resolution():
    # The monthly window is BUILT now (Phase 3); with nothing in the panel
    # the answer is still a skip, carried at the era's own resolution.
    shiller = _market("^GSPC", inception="1871-01-01", freq='{"1871": "month"}')
    effects, skips = event_study.compute_effects(
        _event(date="1912-06-13"), [shiller], prices={}
    )
    assert effects == []
    assert skips[0].resolution == "month"
    assert skips[0].window == "monthly"
    assert skips[0].status == "skipped_no_data"


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


def test_an_alive_but_dataless_market_is_a_recorded_skip():
    # ^N225's exchange reopens in 1949 but the series starts 1965, so a 1953
    # event finds the market alive and its data absent. That is a skip the
    # study records — one such market must not kill the whole pack's run.
    n225 = _market("^N225", inception="1949-05-16", freq='{"1965": "day"}')
    effects, skips = event_study.compute_effects(
        _event(node_id="event:armistice", date="1953-07-27"),
        [n225],
        prices={"^N225": []},
    )
    assert effects == []
    assert [s.status for s in skips] == ["skipped_no_data"]
    assert "1965" in skips[0].reason


def test_a_dataless_market_does_not_claim_first_mover():
    # At a date where ^N225 is alive-but-dataless, the market that actually
    # printed prices is the set's first mover.
    n225 = _market("^N225", inception="1949-05-16", freq='{"1965": "day"}')
    gspc = _market("^GSPC", inception="1871-01-01", freq='{"1871": "day"}')
    prices = _series(dt.date(1953, 7, 27), _ESTIMATION + _GAP, [0.01, 0.0])
    effects, skips = event_study.compute_effects(
        _event(node_id="event:armistice", date="1953-07-27"),
        [n225, gspc],
        prices={"^N225": [], "^GSPC": prices},
    )
    assert any(e.market_ticker == "^GSPC" and e.first_mover for e in effects)
    assert {s.market_ticker for s in skips} >= {"^N225"}


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


# ── Phase 1: the whole spine, accounted for ──────────────────────────────────


def test_every_spine_event_and_market_is_measured_or_a_recorded_skip():
    """THE COVERAGE INVARIANT (build-spec §18 Phase 1). Run the engine over
    every marquee event × every pack market with an empty panel: nothing is
    measurable, so every single pair must come back as a recorded skip —
    never silently absent. Silence is how "we didn't look" gets mistaken for
    "nothing happened"."""
    from core import packs

    pack = packs.load("mena")
    all_dates = {
        e["id"]: dt.date.fromisoformat(str(e["date"])[:10]) for e in pack.marquee_events
    }
    assert len(pack.marquee_events) >= 15  # the spine, not the episode

    for event in pack.marquee_events:
        effects, skips = event_study.compute_effects(
            {"node_id": event["id"], "event_time": event["date"]},
            pack.markets,
            prices={m["ticker"]: [] for m in pack.markets},
            other_event_dates=all_dates,
        )
        assert effects == []
        accounted = {s.market_ticker for s in skips}
        assert accounted == {m["ticker"] for m in pack.markets}, (
            f"{event['id']}: unaccounted markets "
            f"{sorted({m['ticker'] for m in pack.markets} - accounted)}"
        )
        for skip in skips:
            assert skip.reason, f"{event['id']} {skip.market_ticker}: skip with no reason"


# ── the deep windows: monthly and annual (Phase 3) ───────────────────────────


def _monthly_series(months: list[tuple[str, float]]) -> list[dict[str, object]]:
    """Month-start observations at explicit levels — the Shiller shape."""
    return [{"obs_date": f"{ym}-01", "price": level} for ym, level in months]


def test_a_truncated_event_date_anchors_at_the_first_day_it_could_mean():
    assert event_study.parse_event_date("1911-07") == dt.date(1911, 7, 1)
    assert event_study.parse_event_date("1911") == dt.date(1911, 1, 1)
    assert event_study.parse_event_date("2025-06-13") == dt.date(2025, 6, 13)


def test_a_monthly_era_event_measures_one_month_against_its_mean():
    """Agadir's shape: a month-resolution 1911 event against Shiller-era
    monthly levels. Estimation months are FLAT (zero mean), the event month
    falls 10%% — so the abnormal return IS that fall, at month resolution."""
    # Alternating levels give the estimation window a nonzero variance and a
    # near-zero mean, so the t-stat is computable and honest.
    wobble = [(f"{1905 + i // 12}-{i % 12 + 1:02d}", 100.0 + (i % 2)) for i in range(78)]
    last: float = wobble[-1][1]
    series = _monthly_series(wobble + [("1911-08", last * 0.9)])  # July's move, on the Aug obs
    market = _market("^GSPC", inception="1871-01-01",
                     freq='{"1871": "month", "1927": "day"}')
    effects, skips = event_study.compute_effects(
        _event("event:agadir", "1911-07"), [market], prices={"^GSPC": series},
    )
    assert skips == []
    assert len(effects) == 1
    monthly = effects[0]
    assert monthly.window == "monthly"
    assert monthly.resolution == "month"
    assert monthly.raw_return == pytest.approx(-0.10, abs=0.001)
    assert monthly.abnormal_return == pytest.approx(-0.10, abs=0.005)
    assert monthly.p_value < 0.01  # a 10% move against a ±1% history


def test_a_daily_era_event_still_measures_daily_cars():
    # The same market, 2025: the era table sends it down the daily path.
    market = _market("^GSPC", inception="1871-01-01",
                     freq='{"1871": "month", "1927": "day"}')
    prices = _series(dt.date(2025, 6, 13), _ESTIMATION + _GAP, [0.05, 0.0])
    effects, _ = event_study.compute_effects(
        _event(), [market], prices={"^GSPC": prices},
    )
    assert {e.window for e in effects} <= {"car_0_1", "car_0_3", "car_0_5"}
    assert all(e.resolution == "day" for e in effects)


def test_an_annual_era_market_measures_one_year():
    market = _market("JST:GBR:EQ", inception="1870-01-01", freq='{"1870": "year"}')
    years = [{"obs_date": f"{1895 + i}-12-31", "price": 100.0 * (1.02 ** i)}
             for i in range(17)]  # 1895..1911, steady +2%
    last_level = 100.0 * (1.02 ** 16)
    years.append({"obs_date": "1912-12-31", "price": last_level * 0.85})
    effects, skips = event_study.compute_effects(
        _event("event:deep", "1912"), [market], prices={"JST:GBR:EQ": years},
    )
    assert len(effects) == 1
    annual = effects[0]
    assert annual.window == "annual"
    assert annual.resolution == "year"
    assert annual.raw_return == pytest.approx(-0.15)
    # Against a steady +2% baseline the ABNORMAL move is deeper than the raw.
    assert annual.abnormal_return == pytest.approx(-0.17, abs=0.005)


# ── which events a truncated run reaches (2026-08-15) ────────────────────────


def _study_module() -> Any:
    """scripts/ is not a package; load the runner by path."""
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "run_event_study.py"
    spec = importlib.util.spec_from_file_location("run_event_study", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_curated_spine_is_measured_before_the_deep_archive():
    # THE 2026-08-15 CASE-STUDY OUTAGE. The archive arrives in date order and a
    # boot's study slice measures until its budget runs out — so with a pure
    # chronological walk the events every narrated surface is built on (the
    # most recent in the archive) are the last ones reached. Production held
    # 632,586 measured effects, had walked as far as 2003, and served "a spine
    # and no numbers" on all three case studies. Curated first, then dates.
    study = _study_module()
    archive = [
        {"id": "event:cow-mid-1", "date": "1911-07-01", "goldstein": -9.0},
        {"id": "event:gdelt-quiet", "date": "1990-01-01", "goldstein": -1.0},
        {"id": "event:gdelt-loud", "date": "1990-01-02", "goldstein": -9.0},
        {"id": "event:mena-2025-rising-lion", "date": "2025-06-13", "goldstein": -10.0},
    ]
    chosen = study.select_all(
        archive, {"event:mena-2025-rising-lion"}, min_gdelt_goldstein=7.0,
    )
    assert [e["id"] for e in chosen] == [
        "event:mena-2025-rising-lion",   # curated, whatever its date
        "event:cow-mid-1",
        "event:gdelt-loud",
    ]  # the sub-materiality GDELT event is still excluded entirely


def test_the_pack_names_the_events_the_spine_run_measures():
    from core import packs

    study = _study_module()
    for name in packs.available():
        pack = packs.load(name)
        curated = study.curated_event_ids(pack)
        assert curated >= {str(e["id"]) for e in pack.marquee_events}
        case_study = pack.case_study
        if case_study:
            # The case study's own episodes are the ones a reader opens first.
            assert curated >= {str(e) for e in case_study["events"]}
