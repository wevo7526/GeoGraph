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

import math
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
        -- `window` is RESERVED in Postgres (since window functions), so a bare
        -- `window TEXT` is a syntax error, not a style question — the same trap
        -- `when` and `end` are in Kuzu. The graph's AFFECTED key slot is still
        -- called `window`; only this column carries the prefix.
        effect_window TEXT        NOT NULL,
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
        UNIQUE (event_node_id, market_ticker, effect_window)
    )
    """,
    # The walk-forward paper backtest's ledger: one row per traded quarter per
    # region. Recomputed whole on each run (the estimator or the pack's books
    # changing SHOULD change history — the upsert makes that visible, not
    # sneaky); positions ride as JSONB because their shape is the paper
    # module's, not the panel's.
    """
    CREATE TABLE IF NOT EXISTS paper_backtests (
        region_pack   TEXT        NOT NULL,
        quarter_end   DATE        NOT NULL,
        marked_through DATE       NOT NULL,
        escalation_likelihood DOUBLE PRECISION NOT NULL,
        episodes      INTEGER     NOT NULL,
        pnl_usd       DOUBLE PRECISION NOT NULL,
        quarter_return DOUBLE PRECISION NOT NULL,
        equity_usd    DOUBLE PRECISION NOT NULL,
        positions     JSONB       NOT NULL,
        method        TEXT        NOT NULL,
        computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (region_pack, quarter_end)
    )
    """,
    # What a walk-forward run knew about ITSELF and threw away at the API
    # boundary until 2026-08-15: the skipped quarters with their reasons and
    # the run's own summary. A region whose every quarter was a recorded skip
    # used to serve "no persisted backtest" — indistinguishable from never
    # having run. One row per region, replaced whole with the ledger.
    """
    CREATE TABLE IF NOT EXISTS paper_backtest_runs (
        region_pack   TEXT        NOT NULL PRIMARY KEY,
        quarters_traded INTEGER   NOT NULL,
        quarters_skipped INTEGER  NOT NULL,
        skipped       JSONB       NOT NULL,
        summary       JSONB       NOT NULL,
        method        TEXT        NOT NULL,
        computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # The solved games (core/games/scenarios.py): one row per (region,
    # scope, dyad). scope='region' holds the aggregate map (dyad_id = ''),
    # scope='dyad' the full per-dyad solution. Postgres rather than a Forecast
    # node because the API and the solver child need concurrent access and
    # nothing here is provenance-bearing graph structure; the frozen
    # `sequence` Forecast stays the scoreable record. Replaced whole per
    # region on every solve — a solution is a function of (archive, kernel,
    # payoffs, model artifact) and stale rows beside fresh ones would blend
    # two solves into one map.
    """
    CREATE TABLE IF NOT EXISTS game_solutions (
        region_pack   TEXT        NOT NULL,
        scope         TEXT        NOT NULL,
        dyad_id       TEXT        NOT NULL DEFAULT '',
        as_of         DATE        NOT NULL,
        solver        TEXT        NOT NULL,
        payload       JSONB       NOT NULL,
        computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (region_pack, scope, dyad_id)
    )
    """,
)


class PanelUnavailable(RuntimeError):
    """Postgres cannot be reached or is not configured. Names the fix."""


def connect(settings: Settings) -> Any:
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
    # executemany, not a per-row round trip: a full-history panel load is tens
    # to hundreds of thousands of daily bars across every market, and the load
    # runs on the boot path — one server round trip per bar is minutes of pure
    # latency. Same treatment record_runs already gets.
    full_rows = [
        {k: row.get(k) for k in
         ("market_ticker", "obs_date", "frequency", "open", "high",
          "low", "close", "value", "source_ref")}
        for row in rows
    ]
    with conn.cursor() as cur:
        if full_rows:
            cur.executemany(sql, full_rows)
    conn.commit()
    return len(rows)


def upsert_intraday(conn: Any, rows: list[dict[str, Any]]) -> int:
    """Recent intraday prints. Same idempotence rule as the daily panel."""
    sql = """
        INSERT INTO market_intraday (market_ticker, ts, price)
        VALUES (%(market_ticker)s, %(ts)s, %(price)s)
        ON CONFLICT (market_ticker, ts) DO UPDATE SET price = EXCLUDED.price
    """
    with conn.cursor() as cur:
        if rows:
            cur.executemany(sql, list(rows))
    conn.commit()
    return len(rows)


def record_runs(conn: Any, effects: list[Any], skips: list[Any]) -> int:
    """The event-study working set: every ATTEMPT, computed or skipped.

    A skip is a row, not an absence. "Tadawul has no 1973 reaction" and "we
    never looked" are different claims, and only a recorded skip can tell them
    apart — so coverage becomes something you query rather than infer.
    """
    sql = """
        INSERT INTO event_study_runs
            (event_node_id, market_ticker, effect_window, resolution, status,
             raw_return, expected_return, abnormal_return, t_stat, p_value, method)
        VALUES (%(event_node_id)s, %(market_ticker)s, %(effect_window)s, %(resolution)s,
                %(status)s, %(raw_return)s, %(expected_return)s, %(abnormal_return)s,
                %(t_stat)s, %(p_value)s, %(method)s)
        ON CONFLICT (event_node_id, market_ticker, effect_window) DO UPDATE SET
            resolution = EXCLUDED.resolution, status = EXCLUDED.status,
            raw_return = EXCLUDED.raw_return, expected_return = EXCLUDED.expected_return,
            abnormal_return = EXCLUDED.abnormal_return, t_stat = EXCLUDED.t_stat,
            p_value = EXCLUDED.p_value, method = EXCLUDED.method,
            computed_at = now()
    """
    rows: list[dict[str, Any]] = [
        {
            "event_node_id": e.event_node_id, "market_ticker": e.market_ticker,
            "effect_window": e.window, "resolution": e.resolution,
            "status": "overlapping" if e.overlapping else "computed",
            "raw_return": _finite(e.raw_return),
            "expected_return": _finite(e.expected_return),
            "abnormal_return": _finite(e.abnormal_return),
            "t_stat": _finite(e.t_stat), "p_value": _finite(e.p_value),
            "method": e.method,
        }
        for e in effects
    ] + [
        {
            "event_node_id": s.event_node_id, "market_ticker": s.market_ticker,
            "effect_window": s.window, "resolution": s.resolution,
            "status": s.status, "raw_return": None, "expected_return": None,
            "abnormal_return": None, "t_stat": None, "p_value": None,
            "method": s.reason,
        }
        for s in skips
    ]
    # executemany, not a Python loop of round trips: at GDELT scale the
    # working set is hundreds of thousands of attempts per full study.
    with conn.cursor() as cur:
        if rows:
            cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def measured_events(conn: Any, market_tickers: list[str] | None = None) -> set[str]:
    """Event ids already measured — the study's watermark.

    An attempt (computed OR skipped) means the engine already looked with the
    panel it had; determinism makes re-looking a no-op, so the watermark is what
    lets a hundred-thousand-event archive boot in seconds.

    PER-MARKET, when `market_tickers` is given. The watermark used to be a bare
    `DISTINCT event_node_id`, which let packs SHADOW each other: packs seed
    alphabetically, so china measured an event against China's markets and
    entered it into the watermark, and mena/eurasia then skipped it entirely —
    so a US–Russia event was never measured against Tadawul or MOEX, and those
    pack-unique markets served no effect for shadowed events. Now a pack asks
    "is this event measured against ALL of MY markets?" (an attempt, computed or
    skipped, for every ticker in its set); only then is it skipped. Each pack
    fills its own market coverage, and re-measuring a fully-covered event is the
    idempotent no-op it always was."""
    with conn.cursor() as cur:
        if not market_tickers:
            cur.execute("SELECT DISTINCT event_node_id FROM event_study_runs")
            return {row[0] for row in cur.fetchall()}
        wanted = sorted(set(market_tickers))
        cur.execute(
            "SELECT event_node_id FROM event_study_runs "
            "WHERE market_ticker = ANY(%s) "
            "GROUP BY event_node_id "
            "HAVING COUNT(DISTINCT market_ticker) >= %s",
            (wanted, len(wanted)),
        )
        return {row[0] for row in cur.fetchall()}


def record_backtest(conn: Any, region_pack: str, result: dict[str, Any]) -> int:
    """Persist a walk-forward run's ledger, replacing the region's previous
    one whole. A backtest is a FUNCTION of (archive, panel, books) — when any
    of those change, the honest move is to show the new history, and keeping
    stale quarters alongside fresh ones would blend two estimators into one
    curve."""
    import json

    ledger = result["ledger"]
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM paper_backtests WHERE region_pack = %s", (region_pack,)
        )
        if ledger:
            cur.executemany(
                """
                INSERT INTO paper_backtests
                    (region_pack, quarter_end, marked_through,
                     escalation_likelihood, episodes, pnl_usd, quarter_return,
                     equity_usd, positions, method)
                VALUES (%(region_pack)s, %(quarter_end)s, %(marked_through)s,
                        %(escalation_likelihood)s, %(episodes)s, %(pnl_usd)s,
                        %(quarter_return)s, %(equity_usd)s, %(positions)s,
                        %(method)s)
                """,
                [
                    {
                        "region_pack": region_pack,
                        "quarter_end": entry["quarter_end"],
                        "marked_through": entry["marked_through"],
                        "escalation_likelihood": entry["escalation_likelihood"],
                        "episodes": entry["episodes"],
                        "pnl_usd": entry["pnl_usd"],
                        "quarter_return": entry["quarter_return"],
                        "equity_usd": entry["equity_usd"],
                        "positions": json.dumps(entry["positions"]),
                        "method": result["method"],
                    }
                    for entry in ledger
                ],
            )
    conn.commit()
    return len(ledger)


def record_backtest_run(conn: Any, region_pack: str, result: dict[str, Any]) -> None:
    """Persist the run's skips and summary beside its ledger (see DDL)."""
    import json

    with conn.cursor() as cur:
        cur.execute("DELETE FROM paper_backtest_runs WHERE region_pack = %s", (region_pack,))
        cur.execute(
            """
            INSERT INTO paper_backtest_runs
                (region_pack, quarters_traded, quarters_skipped, skipped, summary, method)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                region_pack,
                int(result.get("quarters_traded", 0)),
                int(result.get("quarters_skipped", 0)),
                json.dumps(result.get("skipped", [])),
                json.dumps(result.get("summary") or {}),
                str(result.get("method", "")),
            ),
        )
    conn.commit()


def backtest_run(conn: Any, region_pack: str) -> dict[str, Any] | None:
    """The persisted run record for a region, or None."""
    import json

    with conn.cursor() as cur:
        cur.execute(
            "SELECT quarters_traded, quarters_skipped, skipped, summary, method, computed_at "
            "FROM paper_backtest_runs WHERE region_pack = %s",
            (region_pack,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    traded, skipped_n, skipped, summary, method, computed_at = row
    return {
        "quarters_traded": int(traded),
        "quarters_skipped": int(skipped_n),
        "skipped": skipped if isinstance(skipped, list) else json.loads(skipped),
        "summary": summary if isinstance(summary, dict) else json.loads(summary),
        "method": method,
        "computed_at": computed_at.isoformat(),
    }


def record_game_solutions(
    conn: Any, region_pack: str, solved: dict[str, Any], *, solver: str
) -> int:
    """Persist a region's scenario map: the aggregate row plus one row per
    dyad, replacing the region's previous solve whole."""
    import json

    aggregate = solved["region"]
    dyads = solved["dyads"]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM game_solutions WHERE region_pack = %s", (region_pack,))
        cur.execute(
            """
            INSERT INTO game_solutions (region_pack, scope, dyad_id, as_of, solver, payload)
            VALUES (%s, 'region', '', %s, %s, %s)
            """,
            (region_pack, aggregate["as_of"], solver, json.dumps(aggregate)),
        )
        if dyads:
            cur.executemany(
                """
                INSERT INTO game_solutions (region_pack, scope, dyad_id, as_of, solver, payload)
                VALUES (%(region)s, 'dyad', %(dyad)s, %(as_of)s, %(solver)s, %(payload)s)
                """,
                [
                    {
                        "region": region_pack, "dyad": d["dyad_id"], "as_of": d["as_of"],
                        "solver": solver, "payload": json.dumps(d),
                    }
                    for d in dyads
                ],
            )
    conn.commit()
    return 1 + len(dyads)


def game_solution(
    conn: Any, region_pack: str, *, scope: str, dyad_id: str = "", version: str | None = None
) -> dict[str, Any] | None:
    """One persisted solution (the region aggregate or a dyad), with its
    computed_at stamped in, or None.

    A stored payload whose `payload_version` is not `version` is a MISS, not a
    row: the solve is a function of code that has since changed shape, and the
    caller's fallback (solve live) is right where serving it is wrong. This is
    the 2026-08-15 NaN: rows written an hour before a field rename were served
    to a frontend reading the new names, and every probability on the region
    map rendered "NaN%". Pass None to read whatever is there.
    """
    import json

    with conn.cursor() as cur:
        cur.execute(
            "SELECT payload, computed_at, solver FROM game_solutions "
            "WHERE region_pack = %s AND scope = %s AND dyad_id = %s",
            (region_pack, scope, dyad_id),
        )
        row = cur.fetchone()
    if row is None:
        return None
    payload, computed_at, solver = row
    out = payload if isinstance(payload, dict) else json.loads(payload)
    if version is not None and str(out.get("payload_version", "")) != version:
        return None
    out["computed_at"] = computed_at.isoformat()
    out["persisted"] = True
    out["solver_persisted"] = solver
    return out


def game_solution_dyads(conn: Any, region_pack: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT dyad_id FROM game_solutions WHERE region_pack = %s AND scope = 'dyad' "
            "ORDER BY dyad_id",
            (region_pack,),
        )
        return [r[0] for r in cur.fetchall()]


def backtest_rows(conn: Any, region_pack: str) -> list[dict[str, Any]]:
    """The persisted ledger for one region, oldest quarter first."""
    import json

    sql = """
        SELECT quarter_end, marked_through, escalation_likelihood, episodes,
               pnl_usd, quarter_return, equity_usd, positions, method,
               computed_at
        FROM paper_backtests WHERE region_pack = %s ORDER BY quarter_end
    """
    with conn.cursor() as cur:
        cur.execute(sql, (region_pack,))
        rows = cur.fetchall()
    return [
        {
            "quarter_end": quarter_end.isoformat(),
            "marked_through": marked_through.isoformat(),
            "escalation_likelihood": float(likelihood),
            "episodes": int(episodes),
            "pnl_usd": float(pnl),
            "quarter_return": float(quarter_return),
            "equity_usd": float(equity),
            "positions": positions if isinstance(positions, list)
                         else json.loads(positions),
            "method": method,
            "computed_at": computed_at.isoformat(),
        }
        for (quarter_end, marked_through, likelihood, episodes, pnl,
             quarter_return, equity, positions, method, computed_at) in rows
    ]


def _finite(value: float | None) -> float | None:
    """NaN is not a measurement. Postgres would store it; nothing downstream
    could tell it from a real number, so it becomes NULL here."""
    if value is None:
        return None
    return None if math.isnan(value) or math.isinf(value) else float(value)


def coverage(conn: Any, ticker: str, *, frequency: str = "daily") -> dict[str, Any]:
    """What the panel actually holds for one ticker.

    THE PANEL IS THE RECORD OF WHAT DATA EXISTS. A market's inception_date is
    the pack's claim about when the exchange opened; this is the measured
    answer to "what can we compute with", and the two are not the same number
    (build-spec section 5.2 — verify depth on ingest).
    """
    sql = """
        SELECT count(*) AS rows, min(obs_date) AS first, max(obs_date) AS last
        FROM market_observations WHERE market_ticker = %s AND frequency = %s
    """
    with conn.cursor() as cur:
        cur.execute(sql, (ticker, frequency))
        count, first, last = cur.fetchone()
    return {
        "ticker": ticker,
        "frequency": frequency,
        "rows": int(count),
        "first": first.isoformat() if first else None,
        "last": last.isoformat() if last else None,
    }


def series(
    conn: Any,
    ticker: str,
    *,
    start: str,
    end: str,
    frequency: str = "daily",
) -> list[dict[str, Any]]:
    """One market's observations over an inclusive date range, in date order.

    Returns `price` as whichever column carries it — `close` for an index or a
    commodity, `value` for a yield — so callers do not each re-implement that
    choice. Rows with neither are not returned at all; a gap stays a gap.
    """
    sql = """
        SELECT obs_date, close, value FROM market_observations
        WHERE market_ticker = %s AND frequency = %s AND obs_date BETWEEN %s AND %s
        ORDER BY obs_date
    """
    with conn.cursor() as cur:
        cur.execute(sql, (ticker, frequency, start, end))
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for obs_date, close, value in rows:
        price = close if close is not None else value
        if price is None:
            continue
        out.append({"obs_date": obs_date.isoformat(), "price": float(price)})
    return out
