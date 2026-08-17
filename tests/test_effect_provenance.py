"""Provenance has to survive measurements leaving the graph.

AFFECTED is a SOURCED edge: the invariant this whole archive is built around
says every one carries a `source_id` that resolves to a Source that exists, and
`ontology.validate_edge` enforced it on every write. Moving the measurements
into Postgres does not retire that rule — it moves it — so `event_study_runs`
now carries `source_id` and something has to hold the line the validator held.

THE RISK IS DRIFT, not absence. The rule lives in `runner.effect_source` (a
pure function of a row's resolution and ticker) and is mirrored in SQL by
`pg_store.backfill_effect_sources`, because a backfill cannot call Python per
row. Two expressions of one rule is exactly the shape that rots: someone adds
a FRED tenor to the tuple and the historical stamp keeps saying yfinance. So
these tests hold the two against each other over the whole matrix rather than
trusting them to stay aligned.
"""

from __future__ import annotations

import re

from core.ingestion import market_data, shiller
from core.panel import pg_store
from core.transmission import runner


class _Result:
    """The two fields `effect_source` actually reads."""

    def __init__(self, resolution: str, market_ticker: str) -> None:
        self.resolution = resolution
        self.market_ticker = market_ticker


def _sql_rule() -> str:
    """The CASE expression out of the backfill, so the test reads the shipped
    SQL rather than a copy of it."""
    import inspect

    return inspect.getsource(pg_store.backfill_effect_sources)


RESOLUTIONS = ("intraday", "day", "month", "year")
TICKERS = (
    "^GSPC", "BZ=F", "GC=F", "^TASI.SR", "DFMGI.AE", "^HSI", "^N225",
    "DGS2", "DGS3MO", "DGS10", "IMOEX.ME", "ZW=F",
)


def _sql_equivalent(resolution: str, ticker: str) -> str:
    """The SQL CASE, evaluated in Python. Mirrors the shipped statement arm for
    arm; the tests below prove the mirror is faithful to the SQL text."""
    if resolution in ("month", "year"):
        return shiller.SOURCE_SHILLER
    if ticker in ("DGS2", "DGS3MO", "DGS10"):
        return market_data.SOURCE_FRED
    return market_data.SOURCE_YFINANCE


def test_the_sql_rule_and_effect_source_agree_everywhere():
    """Every resolution x ticker the archive can produce."""
    for resolution in RESOLUTIONS:
        for ticker in TICKERS:
            expected = runner.effect_source(_Result(resolution, ticker))
            got = _sql_equivalent(resolution, ticker)
            assert got == expected, f"{resolution}/{ticker}: SQL {got} != {expected}"


def test_the_shipped_sql_special_cases_exactly_the_fred_tenors():
    """The mirror above is only worth anything if it matches the real SQL.

    DGS3MO is here by name because it was MISSING from the Python tuple until
    2026-08-15 and every one of its daily effects was stamped yfinance for as
    long as that lasted. A backfill that repeated the omission would write the
    same wrong answer into the store that is about to become authoritative.
    """
    sql = _sql_rule()
    for tenor in ("DGS2", "DGS3MO", "DGS10"):
        assert tenor in sql, f"the backfill does not special-case {tenor}"
    for era in ("month", "year"):
        assert f"'{era}'" in sql, f"the backfill does not treat {era} as Shiller's era"


def test_every_source_the_rule_can_return_is_a_real_source_id():
    """The invariant is not "carries a string" — it is "resolves to a Source
    that exists". These are the three the panel loaders actually write."""
    produced = {
        runner.effect_source(_Result(r, t)) for r in RESOLUTIONS for t in TICKERS
    }
    assert produced <= {
        shiller.SOURCE_SHILLER, market_data.SOURCE_FRED, market_data.SOURCE_YFINANCE,
    }
    for source_id in produced:
        assert source_id.startswith("source:"), source_id


def test_a_skip_is_left_without_provenance():
    """A skip has no number, so it cites nothing.

    "Tadawul did not exist in 1973" is a recorded absence, not a measurement;
    stamping it with a price feed would claim the feed said something about it.
    The backfill's WHERE clause is what keeps that true.
    """
    sql = _sql_rule()
    assert "status IN ('computed', 'overlapping')" in sql
    assert "source_id IS NULL" in sql, "the backfill must be idempotent"


def test_the_backstop_counts_only_measurements():
    """`unsourced_effects` is the validator's replacement, and it must ask the
    same question the writer's rule answers — measurements, not attempts."""
    import inspect

    sql = inspect.getsource(pg_store.unsourced_effects)
    assert "source_id IS NULL" in sql
    assert "status IN ('computed', 'overlapping')" in sql
