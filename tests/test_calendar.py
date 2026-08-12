"""Calendar handling — the load-bearing modern-era logic. The Abqaiq shape
(Saturday event → Gulf trades a full business day before the US) is the case
the whole module exists for."""

from __future__ import annotations

import datetime as dt

import pytest
import yaml

from core.ontology.kuzu_schema import SCHEMA_PATH
from core.transmission import calendar


def test_us_weekend_gulf_weekend():
    saturday = dt.date(2019, 9, 14)
    assert not calendar.is_trading_day("us", saturday)
    assert not calendar.is_trading_day("gulf", saturday)
    friday = dt.date(2019, 9, 13)
    assert calendar.is_trading_day("us", friday)
    assert not calendar.is_trading_day("gulf", friday)
    sunday = dt.date(2019, 9, 15)
    assert calendar.is_trading_day("gulf", sunday)
    assert not calendar.is_trading_day("us", sunday)


def test_abqaiq_first_sessions():
    attack = dt.date(2019, 9, 14)  # Saturday
    assert calendar.first_session("gulf", attack) == dt.date(2019, 9, 15)  # Sunday
    assert calendar.first_session("us", attack) == dt.date(2019, 9, 16)    # Monday


def test_abqaiq_first_mover_is_the_gulf():
    movers = calendar.first_movers(
        {"^TASI.SR": "gulf", "^GSPC": "us", "BZ=F": "global_futures"},
        dt.date(2019, 9, 14),
    )
    assert movers == {"^TASI.SR": True, "^GSPC": False, "BZ=F": False}


def test_weekday_event_ties_are_all_first_movers():
    movers = calendar.first_movers(
        {"^TASI.SR": "gulf", "^GSPC": "us"}, dt.date(2020, 1, 6)  # a Monday
    )
    assert movers == {"^TASI.SR": True, "^GSPC": True}


def test_event_on_a_trading_day_reacts_same_session():
    monday = dt.date(2020, 1, 6)
    assert calendar.first_session("us", monday) == monday


def test_unknown_calendar_raises():
    with pytest.raises(KeyError):
        calendar.first_session("mars", dt.date(2020, 1, 1))


def test_calendar_names_match_the_ontology_enum():
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = yaml.safe_load(fh)
    enum_values = set(schema["enums"]["TradingCalendar"]["permissible_values"])
    assert set(calendar.TRADING_DAYS) == enum_values


# ── a market's week changes across the archive ────────────────────────────────


def test_a_market_without_eras_keeps_one_calendar_forever():
    market = {"trading_calendar": "gulf"}
    for year in (1985, 2019, 2025):
        assert calendar.calendar_for(market, dt.date(year, 6, 1)) == "gulf"


def test_dubai_changed_its_week_in_2022():
    # The UAE changed its working week on 2022-01-03 and its exchanges moved
    # with it; Saudi Arabia did not.
    dfm = {"trading_calendar": "gulf", "calendar_eras": '{"2000": "gulf", "2022": "uae"}'}
    assert calendar.calendar_for(dfm, dt.date(2019, 9, 14)) == "gulf"
    assert calendar.calendar_for(dfm, dt.date(2025, 6, 22)) == "uae"


def test_the_emirati_week_is_neither_gulf_nor_us():
    # Measured, not assumed: DFM is shut on Sunday (so not `gulf`) and shut on
    # Friday (so not `us`) — 0 Friday sessions out of 78 in the loaded panel.
    friday, saturday, sunday, monday = (
        dt.date(2025, 6, 20), dt.date(2025, 6, 21),
        dt.date(2025, 6, 22), dt.date(2025, 6, 23),
    )
    assert not calendar.is_trading_day("uae", friday)
    assert not calendar.is_trading_day("uae", saturday)
    assert not calendar.is_trading_day("uae", sunday)
    assert calendar.is_trading_day("uae", monday)
    assert calendar.first_session("uae", friday) == monday


def test_the_pack_knows_dubai_is_shut_on_the_case_study_sunday():
    # Verified against the feed: DFM has no session on Sunday 2025-06-22 while
    # Tadawul does. Without the era override the engine would look for DFM's
    # reaction on a day the exchange is closed.
    from core import packs

    markets = {m["ticker"]: m for m in packs.load("mena").markets}
    sunday = dt.date(2025, 6, 22)
    dfm = calendar.calendar_for(markets["DFMGI.AE"], sunday)
    tasi = calendar.calendar_for(markets["^TASI.SR"], sunday)
    assert not calendar.is_trading_day(dfm, sunday)
    assert calendar.is_trading_day(tasi, sunday)
    # ...and in 2019 both were shut on the Friday before Abqaiq.
    friday = dt.date(2019, 9, 13)
    assert not calendar.is_trading_day(
        calendar.calendar_for(markets["DFMGI.AE"], friday), friday
    )


def test_eras_before_the_first_entry_fall_back_to_the_base_calendar():
    market = {"trading_calendar": "gulf", "calendar_eras": '{"2022": "us"}'}
    assert calendar.calendar_for(market, dt.date(2010, 1, 1)) == "gulf"


def test_the_twelve_day_war_flips_first_mover_between_its_two_events():
    # THE PHASE 0 READING, pinned. Rising Lion opens on a Friday: US and
    # global futures trade, the Gulf waits for Sunday. Midnight Hammer closes
    # it on a Sunday: Tadawul trades a full session before New York.
    from core import packs

    markets = {m["ticker"]: m for m in packs.load("mena").markets}

    def movers_on(date: dt.date) -> set[str]:
        calendars = {t: calendar.calendar_for(m, date) for t, m in markets.items()}
        return {t for t, first in calendar.first_movers(calendars, date).items() if first}

    friday = movers_on(dt.date(2025, 6, 13))   # Operation Rising Lion
    sunday = movers_on(dt.date(2025, 6, 22))   # Operation Midnight Hammer
    # Friday: the Mon–Fri markets price it and Tadawul cannot.
    assert "^GSPC" in friday and "BZ=F" in friday
    assert "^TASI.SR" not in friday
    # Sunday: Tadawul alone, a full session before New York.
    assert sunday == {"^TASI.SR"}
