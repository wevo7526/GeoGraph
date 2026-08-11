"""The tabular numeric store: Postgres on Railway — build-spec section 8.3.

WHY POSTGRES WHEN THE GRAPH IS AN EMBEDDED FILE: the API and the compute jobs
need CONCURRENT access to the price panel, and Kuzu is single-writer. The
graph holds structure and provenance; this store holds the multi-frequency
price panel and the event-study working set. The transmission engine reads
series from here, computes in pandas/statsmodels, and writes effects back to
Kuzu as AFFECTED edge properties — numbers cross from panel to graph in
exactly one direction, through `core.transmission`.

THE FIDELITY GRADIENT LIVES IN THE ROWS: `frequency` is per-observation, not
per-market, because one market's native frequency changes across eras (Shiller
monthly → yfinance daily for US equities). Deep-past series may carry a single
`value` rather than full OHLC — the columns are nullable on purpose.
"""

from __future__ import annotations

from typing import Any

from core.settings import Settings

#: Executed in order by `apply_schema`. Plain DDL strings, parameterised
#: nowhere — there is no input in them to inject.
DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS market_observations (
        market_ticker TEXT        NOT NULL,
        obs_date      DATE        NOT NULL,
        frequency     TEXT        NOT NULL CHECK (frequency IN ('annual', 'monthly', 'daily')),
        open          DOUBLE PRECISION,
        high          DOUBLE PRECISION,
        low           DOUBLE PRECISION,
        close         DOUBLE PRECISION,
        value         DOUBLE PRECISION,
        source_ref    TEXT        NOT NULL,
        PRIMARY KEY (market_ticker, obs_date, frequency)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_intraday (
        market_ticker TEXT        NOT NULL,
        ts            TIMESTAMPTZ NOT NULL,
        price         DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (market_ticker, ts)
    )
    """,
    # The event-study working set: one row per (event, market, window) attempt,
    # including skips — a market that did not exist at event time is recorded
    # as skipped, not silently absent, so coverage is data (the MarketGraph
    # lesson applied to the panel).
    """
    CREATE TABLE IF NOT EXISTS event_study_runs (
        run_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        event_node_id TEXT        NOT NULL,
        market_ticker TEXT        NOT NULL,
        window        TEXT        NOT NULL,
        resolution    TEXT        NOT NULL,
        status        TEXT        NOT NULL CHECK (status IN
            ('computed', 'skipped_no_market', 'skipped_no_data', 'overlapping')),
        raw_return       DOUBLE PRECISION,
        expected_return  DOUBLE PRECISION,
        abnormal_return  DOUBLE PRECISION,
        t_stat           DOUBLE PRECISION,
        p_value          DOUBLE PRECISION,
        method        TEXT        NOT NULL,
        computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (event_node_id, market_ticker, window)
    )
    """,
)


class PanelUnavailable(RuntimeError):
    """Postgres cannot be reached or is not configured. Names the fix."""


def connect(settings: Settings):
    """A psycopg connection to the panel, or a diagnosis.

    Lazy import so the core installs without the `panel` extra — reading the
    GRAPH needs no Postgres at all.
    """
    if not settings.database_url:
        raise PanelUnavailable(
            "DATABASE_URL is unset. The panel is Postgres (build-spec section "
            "8.3); locally run one via Docker, on Railway reference the "
            "Postgres service's DATABASE_URL."
        )
    try:
        import psycopg
    except ImportError as exc:
        raise PanelUnavailable(
            'psycopg is not installed — pip install -e ".[panel]"'
        ) from exc
    try:
        return psycopg.connect(settings.database_url)
    except Exception as exc:
        raise PanelUnavailable(f"Cannot reach Postgres: {exc}") from exc


def apply_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        for statement in DDL:
            cur.execute(statement)
    conn.commit()


def upsert_observations(conn: Any, rows: list[dict[str, Any]]) -> int:
    """Idempotent load of panel rows; re-running an ingest is a no-op, which
    is what makes backfills safe to resume."""
    sql = """
        INSERT INTO market_observations
            (market_ticker, obs_date, frequency, open, high, low, close, value, source_ref)
        VALUES (%(market_ticker)s, %(obs_date)s, %(frequency)s, %(open)s, %(high)s,
                %(low)s, %(close)s, %(value)s, %(source_ref)s)
        ON CONFLICT (market_ticker, obs_date, frequency) DO UPDATE SET
            open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
            close = EXCLUDED.close, value = EXCLUDED.value, source_ref = EXCLUDED.source_ref
    """
    with conn.cursor() as cur:
        for row in rows:
            full = {k: row.get(k) for k in
                    ("market_ticker", "obs_date", "frequency", "open", "high",
                     "low", "close", "value", "source_ref")}
            cur.execute(sql, full)
    conn.commit()
    return len(rows)
