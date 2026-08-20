"""Modern-tier market data: yfinance + FRED (section 5.2).

The pack's markets.yaml names the universe — ^GSPC and sector ETFs, ^TASI.SR
(Saudi, 1985), DFMGI.AE (Dubai, 2000), FADX15.FGI (Abu Dhabi, 2000), BZ=F
(Brent), GC=F (gold) — and FRED supplies DGS2/DGS10 yields. WHICH LOADER A
MARKET USES IS DERIVED FROM ITS market_type, not from a ticker list here: a
pack introducing a new index gets fetched without touching this file.

TWO RULES, both fidelity-gradient consequences:
  - VERIFY EACH TICKER'S DEPTH ON INGEST (decision: spec 5.2). A ticker's
    actual history start is recorded against the market's inception_date;
    silent short history is how a "40-year study" quietly becomes a 10-year
    one. `Depth` carries the comparison and `load_daily` returns one per
    ticker — the panel itself is the record of what data exists, which is why
    no "verified_from" slot was added to the ontology.
  - INTRADAY IS RECENT-ONLY (~60 days on yfinance). It lands in
    market_intraday and NOTHING may build a dependency on historical
    intraday existing.

A ticker that returns nothing is COUNTED AND REPORTED, never filled or
interpolated: a gap in the panel makes the event study skip a market, which is
a recorded outcome; a fabricated bar makes it produce a number.

Requires the `ingest` extra; FRED needs FRED_API_KEY.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any

from core import packs
from core.panel import pg_store
from core.settings import Settings

#: Source node ids these loaders cite. The same ids the graph's Source nodes
#: carry, so a panel row and an AFFECTED edge derived from it name one source.
SOURCE_YFINANCE = "source:yfinance"
SOURCE_FRED = "source:fred"

#: market_type values FRED serves. Everything else goes to yfinance.
_FRED_TYPES = frozenset({"sovereign_yield"})


class IngestError(RuntimeError):
    """A loader cannot run at all. The message names the fix."""


@dataclass(frozen=True)
class Depth:
    """What a ticker actually returned, against what the pack claimed."""

    ticker: str
    rows: int
    first: str | None
    last: str | None
    inception_date: str

    @property
    def empty(self) -> bool:
        return self.rows == 0

    @property
    def years_missing(self) -> float:
        """How much of the claimed history the feed does not actually have."""
        if self.first is None:
            return 0.0
        claimed = dt.date.fromisoformat(self.inception_date[:10])
        actual = dt.date.fromisoformat(self.first[:10])
        return max(0.0, (actual - claimed).days / 365.25)

    def report(self) -> str:
        if self.empty:
            return f"{self.ticker}: NO DATA (pack claims from {self.inception_date})"
        gap = (
            f" — {self.years_missing:.0f}y later than the pack's {self.inception_date}"
            if self.years_missing >= 1.0
            else ""
        )
        return f"{self.ticker}: {self.rows} rows, {self.first} → {self.last}{gap}"


def _is_number(value: Any) -> bool:
    """A usable observation. NaN and None are gaps, and gaps stay gaps."""
    try:
        return value is not None and not math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def _yfinance_history(ticker: str, start: str, end: str | None, interval: str) -> Any:
    try:
        import yfinance
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on extras
        raise IngestError('yfinance is not installed — pip install -e ".[ingest]"') from exc
    return yfinance.Ticker(ticker).history(
        start=start, end=end, interval=interval, auto_adjust=False, actions=False
    )


def _feed_symbols(market: dict[str, Any]) -> list[str]:
    """Symbols to ask the feed, in order. `ticker` is the panel key; extras
    are aliases that quote the same series (MOEX's .ME suffix among them)."""
    ticker = str(market["ticker"])
    extra = [str(s) for s in (market.get("feed_tickers") or []) if s]
    out: list[str] = []
    for symbol in [*extra, ticker]:
        if symbol not in out:
            out.append(symbol)
    return out


def _first_history(
    symbols: list[str], start: str, end: str | None, interval: str,
) -> Any:
    """The first symbol that returns bars. Empty frames are misses, not data."""
    last: Any = None
    for symbol in symbols:
        try:
            frame = _yfinance_history(symbol, start, end, interval)
        except Exception:  # noqa: BLE001 - a dead alias must not kill the pack
            continue
        last = frame
        if _has_bars(frame):
            return frame
    return last


def _has_bars(frame: Any) -> bool:
    """A usable yfinance frame — empty is a miss, including the test double."""
    if frame is None:
        return False
    empty = getattr(frame, "empty", None)
    if empty is not None:
        return not bool(empty)
    index = getattr(frame, "index", None)
    if index is not None:
        try:
            return len(index) > 0
        except TypeError:
            return False
    return False


def _daily_rows(ticker: str, frame: Any) -> list[dict[str, Any]]:
    """A yfinance frame → panel rows. Bars with no close are dropped."""
    rows: list[dict[str, Any]] = []
    for stamp, bar in frame.iterrows():
        if not _is_number(bar.get("Close")):
            continue
        rows.append({
            "market_ticker": ticker,
            "obs_date": stamp.date() if hasattr(stamp, "date") else stamp,
            "frequency": "daily",
            "open": float(bar["Open"]) if _is_number(bar.get("Open")) else None,
            "high": float(bar["High"]) if _is_number(bar.get("High")) else None,
            "low": float(bar["Low"]) if _is_number(bar.get("Low")) else None,
            "close": float(bar["Close"]),
            "value": None,
            "source_ref": SOURCE_YFINANCE,
        })
    return rows


def _depth(ticker: str, rows: list[dict[str, Any]], inception: str) -> Depth:
    dates = sorted(str(r["obs_date"]) for r in rows)
    return Depth(
        ticker=ticker,
        rows=len(rows),
        first=dates[0] if dates else None,
        last=dates[-1] if dates else None,
        inception_date=inception,
    )


def load_daily(
    settings: Settings,
    *,
    region_pack: str,
    start: str | None = None,
    end: str | None = None,
    tickers: list[str] | None = None,
) -> list[Depth]:
    """Daily bars for the pack's price markets → market_observations.

    `start` defaults per market to its own inception_date: there is no point
    asking a feed for data from before the exchange existed, and the pack
    already knows when that was. Returns one Depth per ticker attempted,
    including the ones that came back empty.
    """
    pack = packs.load(region_pack)
    conn = pg_store.connect(settings)
    wanted = set(tickers) if tickers else None
    report: list[Depth] = []
    try:
        for market in pack.markets:
            ticker = market["ticker"]
            if market["market_type"] in _FRED_TYPES:
                continue
            if wanted is not None and ticker not in wanted:
                continue
            frame = _first_history(
                _feed_symbols(market),
                start or str(market["inception_date"]),
                end,
                "1d",
            )
            rows = _daily_rows(ticker, frame) if frame is not None else []
            if rows:
                pg_store.upsert_observations(conn, rows)
            report.append(_depth(ticker, rows, str(market["inception_date"])))
    finally:
        conn.close()
    return report


def load_intraday(
    settings: Settings, *, region_pack: str, tickers: list[str] | None = None
) -> int:
    """Recent intraday prints → market_intraday.

    ~60 days of history exist on yfinance and that is a permanent ceiling, not
    a quota to work around: nothing in the transmission engine may require
    intraday for a historical event (build-spec section 5.3).
    """
    pack = packs.load(region_pack)
    conn = pg_store.connect(settings)
    wanted = set(tickers) if tickers else None
    written = 0
    try:
        for market in pack.markets:
            ticker = market["ticker"]
            if market["market_type"] in _FRED_TYPES:
                continue
            if wanted is not None and ticker not in wanted:
                continue
            frame = _first_history(_feed_symbols(market), "", None, "1h")
            if frame is None or len(getattr(frame, "index", ())) == 0:
                continue
            prints = [
                {"market_ticker": ticker, "ts": stamp, "price": float(bar["Close"])}
                for stamp, bar in frame.iterrows()
                if _is_number(bar.get("Close"))
            ]
            if prints:
                written += pg_store.upsert_intraday(conn, prints)
    finally:
        conn.close()
    return written


def load_yields(
    settings: Settings,
    *,
    region_pack: str = "mena",
    start: str | None = None,
    end: str | None = None,
) -> list[Depth]:
    """FRED sovereign yields → market_observations (daily).

    A yield carries `value`, not OHLC: there is no open or close for a rate,
    and the panel's nullable price columns exist precisely so this row shape
    does not need a second table.
    """
    if not settings.fred_api_key:
        raise IngestError(
            "FRED_API_KEY is unset, so Treasury yields cannot be loaded. Every "
            "other market still loads — this disables one series, not the panel."
        )
    try:
        from fredapi import Fred
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on extras
        raise IngestError('fredapi is not installed — pip install -e ".[ingest]"') from exc

    pack = packs.load(region_pack)
    fred = Fred(api_key=settings.fred_api_key)
    conn = pg_store.connect(settings)
    report: list[Depth] = []
    try:
        for market in pack.markets:
            if market["market_type"] not in _FRED_TYPES:
                continue
            ticker = market["ticker"]
            series = fred.get_series(
                ticker,
                observation_start=start or str(market["inception_date"]),
                observation_end=end,
            )
            rows = [
                {
                    "market_ticker": ticker,
                    "obs_date": stamp.date() if hasattr(stamp, "date") else stamp,
                    "frequency": "daily",
                    "open": None, "high": None, "low": None, "close": None,
                    # FRED marks non-trading days NaN. They are dropped, never
                    # forward-filled: a filled holiday is a fabricated
                    # observation that a market-model regression would weight.
                    "value": float(value),
                    "source_ref": SOURCE_FRED,
                }
                for stamp, value in series.items()
                if _is_number(value)
            ]
            if rows:
                pg_store.upsert_observations(conn, rows)
            report.append(_depth(ticker, rows, str(market["inception_date"])))
    finally:
        conn.close()
    return report
