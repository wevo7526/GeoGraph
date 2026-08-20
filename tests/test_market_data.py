"""Market-data ingestion. No network and no Postgres: the feed and the panel
are both faked, because what these tests pin is the archive's rules — gaps stay
gaps, depth is reported against the pack's claim, and which loader a market
uses is derived from its type rather than hardcoded.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from core import packs
from core.ingestion import market_data
from core.panel import pg_store


class _Bar(dict[str, Any]):
    """One row of a price frame — dict already has the .get() the loader uses."""


class _Frame:
    """The two lines of the pandas API the loader touches."""

    def __init__(self, rows: list[tuple[Any, dict[str, Any]]]) -> None:
        self._rows = rows

    @property
    def empty(self) -> bool:
        return not self._rows

    @property
    def index(self) -> list[Any]:
        return [stamp for stamp, _ in self._rows]

    def iterrows(self):
        return iter((stamp, _Bar(bar)) for stamp, bar in self._rows)


def _bar(close: float | None, *, open_: float | None = None) -> dict[str, Any]:
    return {"Open": open_ if open_ is not None else close, "High": close,
            "Low": close, "Close": close}


class _FakePanel:
    """Records what would have been written."""

    def __init__(self) -> None:
        self.observations: list[dict[str, Any]] = []
        self.intraday: list[dict[str, Any]] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def panel(monkeypatch):
    # market_data does `from core.panel import pg_store`, so it holds this same
    # module object — patching here is what the loader sees.
    fake = _FakePanel()

    def _observations(conn: _FakePanel, rows: list[dict[str, Any]]) -> int:
        conn.observations.extend(rows)
        return len(rows)

    def _intraday(conn: _FakePanel, rows: list[dict[str, Any]]) -> int:
        conn.intraday.extend(rows)
        return len(rows)

    monkeypatch.setattr(pg_store, "connect", lambda settings: fake)
    monkeypatch.setattr(pg_store, "upsert_observations", _observations)
    monkeypatch.setattr(pg_store, "upsert_intraday", _intraday)
    return fake


@pytest.fixture()
def settings():
    from core import settings as settings_module

    return settings_module.load()


# ── depth reporting ──────────────────────────────────────────────────────────


def test_depth_names_the_gap_between_claim_and_reality():
    # The Tadawul case: the pack says the exchange opened in 1985, the feed
    # starts far later, and a study that assumes 1985 is quietly wrong.
    depth = market_data.Depth(
        ticker="^TASI.SR", rows=100, first="2007-01-02", last="2025-06-30",
        inception_date="1985-01-01",
    )
    assert depth.years_missing == pytest.approx(22.0, abs=0.5)
    assert "later than the pack's 1985-01-01" in depth.report()


def test_depth_is_quiet_when_the_feed_matches_the_claim():
    # Two days late is the first trading session of the year, not a gap — the
    # report only speaks up at a year, where the shortfall changes a study.
    depth = market_data.Depth(
        ticker="^GSPC", rows=10, first="2000-01-03", last="2000-01-14",
        inception_date="2000-01-01",
    )
    assert depth.years_missing < 1.0
    assert "later than" not in depth.report()


def test_an_empty_ticker_is_reported_not_hidden():
    depth = market_data.Depth(
        ticker="DFMGI.AE", rows=0, first=None, last=None, inception_date="2000-03-26"
    )
    assert depth.empty
    assert "NO DATA" in depth.report()


# ── gaps stay gaps ───────────────────────────────────────────────────────────


def test_bars_without_a_close_are_dropped_not_filled():
    frame = _Frame([
        (dt.date(2025, 6, 12), _bar(100.0)),
        (dt.date(2025, 6, 13), _bar(None)),        # holiday / missing print
        (dt.date(2025, 6, 16), _bar(float("nan"))),
        (dt.date(2025, 6, 17), _bar(104.0)),
    ])
    rows = market_data._daily_rows("^GSPC", frame)
    assert [r["obs_date"] for r in rows] == [dt.date(2025, 6, 12), dt.date(2025, 6, 17)]
    assert all(r["source_ref"] == market_data.SOURCE_YFINANCE for r in rows)
    assert all(r["frequency"] == "daily" for r in rows)


def test_a_partial_bar_keeps_its_close_and_nulls_the_rest():
    frame = _Frame([(dt.date(2025, 6, 12), {"Close": 100.0})])
    row = market_data._daily_rows("BZ=F", frame)[0]
    assert row["close"] == 100.0
    assert row["open"] is None and row["high"] is None and row["low"] is None


def test_nan_is_not_a_number():
    assert market_data._is_number(1.5)
    assert market_data._is_number(0)
    assert not market_data._is_number(None)
    assert not market_data._is_number(float("nan"))
    assert not market_data._is_number("not a price")


# ── routing and windows ──────────────────────────────────────────────────────


def test_yields_are_not_fetched_from_the_price_feed(panel, settings, monkeypatch):
    asked: list[str] = []

    def _history(ticker, start, end, interval):
        asked.append(ticker)
        return _Frame([(dt.date(2025, 6, 12), _bar(1.0))])

    monkeypatch.setattr(market_data, "_yfinance_history", _history)
    report = market_data.load_daily(settings, region_pack="mena")

    pack = packs.load("mena")
    yields = {m["ticker"] for m in pack.markets if m["market_type"] == "sovereign_yield"}
    prices = {m["ticker"] for m in pack.markets if m["market_type"] != "sovereign_yield"}
    assert yields, "the pack should have yield markets for this test to mean anything"
    assert set(asked) == prices
    assert not yields & set(asked)
    assert {d.ticker for d in report} == prices


def test_each_market_is_asked_for_history_from_its_own_inception(panel, settings, monkeypatch):
    starts: dict[str, str] = {}

    def _history(ticker, start, end, interval):
        starts[ticker] = start
        return _Frame([])

    monkeypatch.setattr(market_data, "_yfinance_history", _history)
    market_data.load_daily(settings, region_pack="mena")

    markets = {m["ticker"]: m for m in packs.load("mena").markets}
    assert starts["^TASI.SR"] == str(markets["^TASI.SR"]["inception_date"])
    assert starts["^GSPC"] == str(markets["^GSPC"]["inception_date"])


def test_an_explicit_window_overrides_every_inception(panel, settings, monkeypatch):
    starts: list[str] = []

    def _history(ticker: str, start: str, end: str | None, interval: str) -> _Frame:
        starts.append(start)
        return _Frame([])

    monkeypatch.setattr(market_data, "_yfinance_history", _history)
    market_data.load_daily(settings, region_pack="mena", start="2025-06-01", end="2025-07-01")
    assert set(starts) == {"2025-06-01"}


def test_a_ticker_subset_limits_the_fetch(panel, settings, monkeypatch):
    asked: list[str] = []

    def _history(ticker: str, start: str, end: str | None, interval: str) -> _Frame:
        asked.append(ticker)
        return _Frame([])

    monkeypatch.setattr(market_data, "_yfinance_history", _history)
    market_data.load_daily(settings, region_pack="mena", tickers=["^GSPC"])
    assert asked == ["^GSPC"]


def test_rows_reach_the_panel_and_the_connection_is_closed(panel, settings, monkeypatch):
    monkeypatch.setattr(
        market_data,
        "_yfinance_history",
        lambda ticker, start, end, interval: _Frame([(dt.date(2025, 6, 12), _bar(42.0))]),
    )
    market_data.load_daily(settings, region_pack="mena", tickers=["^GSPC"])
    assert [r["close"] for r in panel.observations] == [42.0]
    assert panel.closed


def test_yields_need_a_key_and_say_so(settings, monkeypatch):
    from dataclasses import replace

    with pytest.raises(market_data.IngestError, match="FRED_API_KEY"):
        market_data.load_yields(replace(settings, fred_api_key=None))


def test_feed_symbols_try_aliases_before_the_pack_ticker():
    market = {"ticker": "IMOEX.ME", "feed_tickers": ["IMOEX", "IMOEX.ME"]}
    assert market_data._feed_symbols(market) == ["IMOEX", "IMOEX.ME"]
    # The panel key stays the pack ticker even when an alias quotes.
    bare = {"ticker": "^GSPC"}
    assert market_data._feed_symbols(bare) == ["^GSPC"]


def test_first_history_skips_empty_frames(monkeypatch):
    calls: list[str] = []

    def fake(symbol: str, start: str, end: str | None, interval: str) -> Any:
        calls.append(symbol)
        if symbol == "IMOEX.ME":
            return _Frame([(dt.date(2024, 1, 2), _bar(1.0))])
        return _Frame([])

    monkeypatch.setattr(market_data, "_yfinance_history", fake)
    frame = market_data._first_history(
        ["IMOEX", "IMOEX.ME"], "2020-01-01", None, "1d",
    )
    assert calls == ["IMOEX", "IMOEX.ME"]
    assert not frame.empty
    assert list(stamp for stamp, _ in frame._rows) == [dt.date(2024, 1, 2)]
