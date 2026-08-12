"""The panel against a REAL Postgres. Skipped unless GEOGRAPH_TEST_DATABASE_URL
is set, so `pytest` still needs no database.

WHY A SEPARATE ENV VAR and not DATABASE_URL: these tests write. Keying them on
DATABASE_URL would mean that running the suite on Railway, or locally with a
production URL exported, quietly inserts into the real panel. They need an
opt-in that cannot happen by accident:

  docker run -d --rm --name geograph-pg -e POSTGRES_PASSWORD=geograph \\
      -e POSTGRES_DB=geograph -p 55432:5432 postgres:16-alpine
  GEOGRAPH_TEST_DATABASE_URL=postgresql://postgres:geograph@127.0.0.1:55432/geograph

These are the tests that catch DDL that only fails against a real server — the
reserved-word class of bug (`window` is reserved in Postgres, so a bare
`window TEXT` column is a syntax error, not a style choice).
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import replace

import pytest

from core import settings as settings_module
from core.panel import pg_store

_URL = os.getenv("GEOGRAPH_TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not _URL, reason="set GEOGRAPH_TEST_DATABASE_URL to run the panel tests"
)

#: Every row these tests write carries this ticker, so cleanup is exact and no
#: real market's data can be touched.
_TICKER = "TEST.PANEL"


@pytest.fixture()
def panel():
    settings = replace(settings_module.load(), database_url=_URL)
    conn = pg_store.connect(settings)
    pg_store.apply_schema(conn)
    _purge(conn)
    yield conn
    _purge(conn)
    conn.close()


def _purge(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM market_observations WHERE market_ticker = %s", (_TICKER,))
        cur.execute("DELETE FROM market_intraday WHERE market_ticker = %s", (_TICKER,))
        cur.execute("DELETE FROM event_study_runs WHERE market_ticker = %s", (_TICKER,))
    conn.commit()


def _obs(date: str, close: float | None = None, value: float | None = None):
    return {
        "market_ticker": _TICKER, "obs_date": date, "frequency": "daily",
        "open": None, "high": None, "low": None, "close": close, "value": value,
        "source_ref": "source:test",
    }


def test_the_schema_applies_to_a_real_server(panel):
    # The whole reason this file exists: DDL that parses in a Python string can
    # still be a syntax error on the server.
    with panel.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        tables = {row[0] for row in cur.fetchall()}
    assert {"market_observations", "market_intraday", "event_study_runs"} <= tables


def test_the_event_study_table_has_an_unreserved_window_column(panel):
    with panel.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'event_study_runs'"
        )
        columns = {row[0] for row in cur.fetchall()}
    assert "effect_window" in columns
    assert "window" not in columns, "a bare `window` column cannot exist — it is reserved"


def test_applying_the_schema_twice_is_harmless(panel):
    pg_store.apply_schema(panel)
    pg_store.apply_schema(panel)


def test_observations_upsert_rather_than_duplicate(panel):
    pg_store.upsert_observations(panel, [_obs("2025-06-12", close=100.0)])
    pg_store.upsert_observations(panel, [_obs("2025-06-12", close=101.5)])
    assert pg_store.coverage(panel, _TICKER)["rows"] == 1
    assert pg_store.series(panel, _TICKER, start="2025-06-01", end="2025-06-30")[0][
        "price"
    ] == 101.5


def test_coverage_reports_what_is_actually_held(panel):
    pg_store.upsert_observations(panel, [
        _obs("2025-06-12", close=100.0), _obs("2025-06-13", close=101.0),
    ])
    row = pg_store.coverage(panel, _TICKER)
    assert row == {
        "ticker": _TICKER, "frequency": "daily", "rows": 2,
        "first": "2025-06-12", "last": "2025-06-13",
    }


def test_coverage_of_an_unloaded_market_is_empty_not_an_error(panel):
    assert pg_store.coverage(panel, "NOT.LOADED")["rows"] == 0


def test_a_yield_row_carries_value_and_the_reader_finds_it(panel):
    # A rate has no open or close; the nullable price columns exist for this.
    pg_store.upsert_observations(panel, [_obs("2025-06-12", value=4.37)])
    rows = pg_store.series(panel, _TICKER, start="2025-06-01", end="2025-06-30")
    assert rows == [{"obs_date": "2025-06-12", "price": 4.37}]


def test_a_row_with_neither_price_is_not_returned(panel):
    pg_store.upsert_observations(panel, [_obs("2025-06-12")])
    assert pg_store.series(panel, _TICKER, start="2025-06-01", end="2025-06-30") == []


def test_series_is_ordered_and_inclusive_of_its_bounds(panel):
    pg_store.upsert_observations(panel, [
        _obs("2025-06-17", close=3.0), _obs("2025-06-12", close=1.0),
        _obs("2025-06-13", close=2.0), _obs("2025-06-30", close=4.0),
    ])
    rows = pg_store.series(panel, _TICKER, start="2025-06-12", end="2025-06-17")
    assert [r["obs_date"] for r in rows] == ["2025-06-12", "2025-06-13", "2025-06-17"]


def test_the_frequency_check_constraint_is_enforced(panel):
    with pytest.raises(Exception, match="frequency|check"):
        pg_store.upsert_observations(panel, [
            {**_obs("2025-06-12", close=1.0), "frequency": "hourly"},
        ])
    panel.rollback()


def test_intraday_upserts_by_timestamp(panel):
    stamp = dt.datetime(2025, 6, 12, 14, 30, tzinfo=dt.UTC)
    pg_store.upsert_intraday(panel, [{"market_ticker": _TICKER, "ts": stamp, "price": 1.0}])
    pg_store.upsert_intraday(panel, [{"market_ticker": _TICKER, "ts": stamp, "price": 2.0}])
    with panel.cursor() as cur:
        cur.execute("SELECT price FROM market_intraday WHERE market_ticker = %s", (_TICKER,))
        assert [row[0] for row in cur.fetchall()] == [2.0]
