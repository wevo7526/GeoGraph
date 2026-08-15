"""Trading-calendar handling. LOAD-BEARING for the modern era — build-spec
section 11: Gulf markets trade Sunday–Thursday, US Monday–Friday, NO SHARED
SESSION. The first tradable session after an event is resolved PER MARKET,
and the market whose session comes first gets the `first_mover` flag — a real
cross-market lead-lag signal, not a curiosity.

Weekday sets only, deliberately: exchange holiday calendars (Eid, Thanksgiving)
are a Phase 1 refinement listed in event_study.py. Getting weekends right is
what changes conclusions — an event on a Saturday reaches Tadawul a full US
business day before it reaches New York.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
from collections.abc import Mapping
from typing import Any

#: TradingCalendar enum value → Python weekday numbers (Mon=0 … Sun=6) on
#: which the market trades. Mirrors the ontology's TradingCalendar enum; the
#: ontology test asserts the two stay in step.
TRADING_DAYS: dict[str, frozenset[int]] = {
    "us": frozenset({0, 1, 2, 3, 4}),             # Mon–Fri
    "gulf": frozenset({6, 0, 1, 2, 3}),           # Sun–Thu (Saudi Arabia)
    "uae": frozenset({0, 1, 2, 3}),               # Mon–Thu (UAE from 2022)
    "global_futures": frozenset({0, 1, 2, 3, 4}),  # daily bars Mon–Fri
}


def calendar_for(market: Mapping[str, Any], date: dt.date) -> str:
    """Which calendar a market kept ON A GIVEN DATE.

    A MARKET'S WEEK IS NOT CONSTANT ACROSS 120 YEARS. The UAE moved to a
    Monday–Friday working week on 2022-01-03 and its exchanges moved with it,
    while Saudi Arabia stayed Sunday–Thursday — so DFM is `gulf` for a 2019
    event and `us` for a 2025 one, and asking for its reaction on a Sunday in
    2025 finds an exchange that is shut.

    `calendar_eras` is era-keyed exactly like `native_frequency`: a start year
    maps to the calendar in force from then on. Absent, `trading_calendar`
    holds for all time.
    """
    base = str(market["trading_calendar"])
    raw = market.get("calendar_eras")
    if not raw:
        return base
    eras = (
        _parse_calendar_eras(raw)
        if isinstance(raw, str)
        else tuple((int(year), name) for year, name in dict(raw).items())
    )
    applicable = [(year, name) for year, name in eras if year <= date.year]
    if not applicable:
        return base
    return str(max(applicable)[1])


@functools.lru_cache(maxsize=512)
def _parse_calendar_eras(raw: str) -> tuple[tuple[int, str], ...]:
    """Parse the era-keyed calendar table once per distinct string — the same
    hot-loop memoization as event_study._parse_era_table: calendar_for runs per
    alive market per event over a hundred-thousand-event study."""
    table = json.loads(raw)
    return tuple((int(year), name) for year, name in table.items())


def is_trading_day(calendar: str, date: dt.date) -> bool:
    days = TRADING_DAYS.get(calendar)
    if days is None:
        raise KeyError(
            f"calendar {calendar!r} is not a TradingCalendar value. Valid: {sorted(TRADING_DAYS)}"
        )
    return date.weekday() in days


def first_session(calendar: str, event_date: dt.date) -> dt.date:
    """The first date on or after `event_date` the market can react.

    ON or after: an event during a trading day is reflected that same session
    at daily resolution. Intraday refinement (was the event before or after
    the close?) belongs to the intraday window, not here.
    """
    date = event_date
    for _ in range(8):  # no weekday gap is longer than a week
        if is_trading_day(calendar, date):
            return date
        date += dt.timedelta(days=1)
    raise KeyError(f"calendar {calendar!r} has no trading days")  # unreachable for valid calendars


def first_movers(calendars: dict[str, str], event_date: dt.date) -> dict[str, bool]:
    """Which market(s) trade first after an event.

    `calendars` maps market ticker → TradingCalendar value. Returns ticker →
    first_mover flag; every market whose first session ties for earliest is
    flagged (a weekday event flags US and Gulf together — correct, both trade
    that day at daily resolution).
    """
    sessions = {t: first_session(c, event_date) for t, c in calendars.items()}
    if not sessions:
        return {}
    earliest = min(sessions.values())
    return {t: session == earliest for t, session in sessions.items()}
