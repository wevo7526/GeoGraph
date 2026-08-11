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
