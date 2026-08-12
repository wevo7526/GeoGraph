"""The transmission engine: a DETERMINISTIC event study — build-spec section 11.

This is the layer that makes the geopolitics-to-money link SHOWN, not
asserted. For each event and each market that existed at event time, compute
the effect at the finest frequency the era allows:

  intraday_open_close   recent only (~60 days of yfinance intraday)
  car_0_1/car_0_3/car_0_5  daily CAR — the modern-era workhorse
  monthly               Shiller era, US
  annual                JST era, advanced economies

HONESTY RULES, locked:
  - Measure realized effects; NEVER assert a sign.
  - SKIP a market that did not exist at event time (Market.inception_date) —
    recorded as a skip in event_study_runs, not silently absent.
  - FLAG overlapping event windows (`overlapping` on the edge) rather than
    averaging them away.
  - Every number on an AFFECTED edge is computed HERE. The AI never
    originates one.

DEVIATION FROM §11, STATED: the spec names a market-model expected return.
This computes a CONSTANT-MEAN-RETURN model, because a one-factor market model
is not merely worse here, it is undefined on the days the archive cares most
about. The markets keep different weeks — Tadawul trades Sunday–Thursday, New
York Monday–Friday, Dubai Monday–Thursday since 2022 — so on Tadawul's Sunday
session, the first session after a Saturday or Sunday event, NO benchmark
return exists to regress against. That Sunday is precisely where the
cross-market lead-lag signal lives (Abqaiq 2019, Midnight Hammer 2025), so a
model that cannot speak there is the wrong model. Constant-mean is a standard
event-study estimator (Brown & Warner 1985), it is well defined for every
market on every session, and `method` records it on every edge. A market model
against a calendar-aligned or lagged benchmark is a Phase 1 refinement, and it
belongs behind an explicit benchmark declaration in the pack rather than a
guess made here.

Reads the panel from Postgres (core.panel.pg_store) and writes through
core.transmission.effects — the one direction numbers cross.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from dataclasses import dataclass
from typing import Any

from core.transmission import calendar as trading_calendar


@dataclass(frozen=True)
class EffectResult:
    """One measured effect: one event, one market, one window."""

    event_node_id: str
    market_ticker: str
    window: str            # EffectWindow enum value
    resolution: str        # TemporalResolution enum value
    raw_return: float
    expected_return: float
    abnormal_return: float
    t_stat: float
    p_value: float
    first_mover: bool
    overlapping: bool
    method: str            # estimation window, model, frequency — reproducibility line


@dataclass(frozen=True)
class Skip:
    """A measurement NOT taken, and why. Coverage is data, not absence."""

    event_node_id: str
    market_ticker: str
    window: str
    resolution: str
    status: str            # skipped_no_market | skipped_no_data
    reason: str


#: Estimation-window lengths per resolution, in periods of that resolution.
#: Scaled, not shared: 120 daily observations and 10 annual ones are both
#: "the pre-event window", at very different confidence — which the t-stat
#: then carries honestly.
ESTIMATION_PERIODS: dict[str, int] = {
    "intraday": 60,
    "day": 120,
    "month": 60,
    "year": 10,
}

#: Sessions left between the end of the estimation window and the event, so
#: immediate pre-event drift does not set the baseline the event is measured
#: against. It does NOT clean a window contaminated by a prior event — that is
#: what `overlapping` reports.
ESTIMATION_GAP_SESSIONS = 5

#: EffectWindow → how many sessions it spans, counting the first tradable
#: session after the event as session 0. car_0_1 is two sessions.
WINDOW_SESSIONS: dict[str, int] = {"car_0_1": 2, "car_0_3": 4, "car_0_5": 6}

#: TemporalResolution → the windows measurable at it, finest first.
WINDOWS_BY_RESOLUTION: dict[str, tuple[str, ...]] = {
    "intraday": ("intraday_open_close", "car_0_1", "car_0_3", "car_0_5"),
    "day": ("car_0_1", "car_0_3", "car_0_5"),
    "month": ("monthly",),
    "year": ("annual",),
}


class StudyError(RuntimeError):
    """The study cannot run. The message names the fix."""


def native_resolution(market: dict[str, Any], event_date: dt.date) -> str:
    """The finest resolution this market's data supports at this date.

    `native_frequency` is era-keyed — US equities are monthly on Shiller from
    1871 and daily from 1927 — so the answer is a function of the date, not of
    the market alone. This is the fidelity gradient, read off the pack.
    """
    raw = market.get("native_frequency")
    if not raw:
        return "day"
    table = json.loads(raw) if isinstance(raw, str) else dict(raw)
    applicable = [(int(year), res) for year, res in table.items() if int(year) <= event_date.year]
    if not applicable:
        raise StudyError(
            f"{market['ticker']} has no native frequency at {event_date}: its "
            f"eras start at {min(int(y) for y in table)}. An event before a "
            "market's data exists is a skip, not a measurement."
        )
    return str(max(applicable)[1])


def finest_window(event_date: dt.date, market: dict[str, Any]) -> str:
    """The finest EffectWindow the era and this market allow."""
    return WINDOWS_BY_RESOLUTION[native_resolution(market, event_date)][0]


def _returns(prices: list[dict[str, Any]]) -> list[tuple[str, float]]:
    """Consecutive simple returns. Dates label the LATER observation."""
    out: list[tuple[str, float]] = []
    for earlier, later in zip(prices, prices[1:], strict=False):
        if earlier["price"] == 0:
            continue
        out.append((later["obs_date"], later["price"] / earlier["price"] - 1.0))
    return out


def _p_value(t_stat: float, df: int) -> tuple[float, str]:
    """Two-sided p-value, and the name of the distribution used.

    scipy gives the exact Student-t tail; without it the normal approximation
    is used, which at df≈119 differs in the third decimal. Either way the
    choice is recorded on the edge rather than left to be inferred.
    """
    if not math.isfinite(t_stat):
        return float("nan"), "undefined"
    try:
        from scipy import stats
    except ModuleNotFoundError:  # pragma: no cover - depends on extras
        return math.erfc(abs(t_stat) / math.sqrt(2.0)), "normal-approx"
    return float(2.0 * stats.t.sf(abs(t_stat), df)), f"student-t(df={df})"


def compute_effects(
    event: dict[str, Any],
    markets: list[dict[str, Any]],
    *,
    prices: dict[str, list[dict[str, Any]]],
    other_event_dates: dict[str, dt.date] | None = None,
) -> tuple[list[EffectResult], list[Skip]]:
    """Run the study for one event across every market alive at its date.

    Deterministic and reproducible: same panel in, same numbers out — no
    randomness, no clock, no model. `prices` maps ticker → the panel series
    already read (so this function does no I/O and can be tested exactly);
    `other_event_dates` maps other event node_ids → their dates, for the
    overlap flag.

    Returns (effects, skips). A skip is a RESULT: it says the archive looked
    and found no measurable market, which is the honest answer for Tadawul in
    1973 and for a delisted ticker in 2025 alike.
    """
    event_date = dt.date.fromisoformat(str(event["event_time"])[:10])
    others = other_event_dates or {}

    # first_mover is a property of the SET of markets being measured, so it is
    # resolved once, across the markets that actually existed at event time.
    def _inception(market: dict[str, Any]) -> dt.date:
        return dt.date.fromisoformat(str(market["inception_date"])[:10])

    alive = [m for m in markets if _inception(m) <= event_date]
    calendars = {
        m["ticker"]: trading_calendar.calendar_for(m, event_date) for m in alive
    }
    movers = trading_calendar.first_movers(calendars, event_date)

    effects: list[EffectResult] = []
    skips: list[Skip] = []

    for market in markets:
        ticker = market["ticker"]
        inception = _inception(market)

        if inception > event_date:
            # No resolution is meaningful for a market that did not exist, so
            # the skip carries the finest one the era could have offered.
            skips.append(Skip(
                event_node_id=event["node_id"], market_ticker=ticker,
                window="car_0_1", resolution="day",
                status="skipped_no_market",
                reason=(
                    f"{ticker} did not exist on {event_date} — its data begins "
                    f"{inception}. Measuring it would invent a market."
                ),
            ))
            continue

        resolution = native_resolution(market, event_date)
        window_names = WINDOWS_BY_RESOLUTION.get(resolution, ("car_0_1",))
        series = prices.get(ticker) or []
        # Only the daily windows are implemented; monthly and annual land with
        # the deep tier (Phase 3), and saying so beats emitting a daily number
        # under a monthly label.
        measurable = [w for w in window_names if w in WINDOW_SESSIONS]
        if not measurable:
            skips.append(Skip(
                event_node_id=event["node_id"], market_ticker=ticker,
                window=window_names[0], resolution=resolution,
                status="skipped_no_data",
                reason=(
                    f"{resolution} resolution is not measurable yet (Phase 3 — "
                    "monthly and annual windows land with the deep tier)."
                ),
            ))
            continue

        session_calendar = calendars.get(ticker) or trading_calendar.calendar_for(
            market, event_date
        )
        expected_session = trading_calendar.first_session(session_calendar, event_date)

        returns = _returns(series)
        # Session 0 is the first RETURN dated on or after the calendar's first
        # session: a return dated d measures the move into d's close, which is
        # the market's reaction to the event.
        index = next((i for i, (date, _) in enumerate(returns)
                      if dt.date.fromisoformat(date) >= expected_session), None)
        if index is None:
            skips.append(Skip(
                event_node_id=event["node_id"], market_ticker=ticker,
                window=measurable[0], resolution=resolution,
                status="skipped_no_data",
                reason=(
                    f"the panel holds no {ticker} session on or after "
                    f"{expected_session}, so there is nothing to measure."
                ),
            ))
            continue

        window_end_index = max(0, index - ESTIMATION_GAP_SESSIONS)
        window_start_index = max(0, window_end_index - ESTIMATION_PERIODS[resolution])
        estimation = returns[window_start_index:window_end_index]
        if len(estimation) < 2:
            skips.append(Skip(
                event_node_id=event["node_id"], market_ticker=ticker,
                window=measurable[0], resolution=resolution,
                status="skipped_no_data",
                reason=(
                    f"{len(estimation)} estimation observations before "
                    f"{expected_session}; a baseline needs a history. Load more "
                    "panel data for this ticker."
                ),
            ))
            continue

        mean = statistics.fmean(value for _, value in estimation)
        sigma = statistics.stdev([value for _, value in estimation])
        df = len(estimation) - 1

        for window in measurable:
            span = WINDOW_SESSIONS[window]
            observed = returns[index:index + span]
            if len(observed) < span:
                skips.append(Skip(
                    event_node_id=event["node_id"], market_ticker=ticker,
                    window=window, resolution=resolution, status="skipped_no_data",
                    reason=(
                        f"{window} needs {span} sessions from {expected_session}; "
                        f"the panel holds {len(observed)}."
                    ),
                ))
                continue

            raw = sum(value for _, value in observed)
            expected = mean * span
            abnormal = raw - expected
            scale = sigma * math.sqrt(span)
            t_stat = abnormal / scale if scale > 0 else float("nan")
            p_value, distribution = _p_value(t_stat, df)

            window_end = dt.date.fromisoformat(observed[-1][0])
            overlapping = any(
                other_id != event["node_id"] and event_date <= other_date <= window_end
                for other_id, other_date in others.items()
            )

            effects.append(EffectResult(
                event_node_id=event["node_id"],
                market_ticker=ticker,
                window=window,
                resolution=resolution,
                raw_return=raw,
                expected_return=expected,
                abnormal_return=abnormal,
                t_stat=t_stat,
                p_value=p_value,
                first_mover=bool(movers.get(ticker, False)),
                overlapping=overlapping,
                # The reproducibility line: everything needed to recompute this
                # number by hand from the panel, including the CAR convention
                # (a sum of simple returns, not a compounded one).
                method=(
                    f"constant-mean;est={len(estimation)}{resolution};"
                    f"gap={ESTIMATION_GAP_SESSIONS};window={window};"
                    f"car=sum-simple;session0={observed[0][0]};"
                    f"calendar={session_calendar};dist={distribution}"
                ),
            ))

    return effects, skips
