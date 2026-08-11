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

#: TradingCalendar enum value → Python weekday numbers (Mon=0 … Sun=6) on
#: which the market trades. Mirrors the ontology's TradingCalendar enum; the
#: ontology test asserts the two stay in step.
TRADING_DAYS: dict[str, frozenset[int]] = {
    "us": frozenset({0, 1, 2, 3, 4}),             # Mon–Fri
    "gulf": frozenset({6, 0, 1, 2, 3}),           # Sun–Thu
    "global_futures": frozenset({0, 1, 2, 3, 4}),  # daily bars Mon–Fri
}


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
