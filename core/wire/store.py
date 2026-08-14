"""Reading and writing the wire corpus in Postgres.

THE ONE DIRECTION OF FLOW IS UNCHANGED. Numbers still cross into the graph
only through `transmission.effects.write_effects`; this module writes the
corpus and reads it back for the model, the game and the forecaster. It never
writes an AFFECTED edge and never opens Kuzu.

The row contract of `dyad_event_rows` is deliberately IDENTICAL to the graph
query it replaces (`core.models.panel.dyad_event_rows`) — same keys, same
types, same ordering. That is what lets `fit_game.py` and the forecaster read
either store without knowing which one they got, and it is why the migration
is one function rather than six rewrites.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from core.classifier import escalation
from core.ontology import pg_schema
from core.settings import Settings


class WireUnavailable(RuntimeError):
    """The corpus cannot be reached, with the reason and the fix."""


def connect(settings: Settings) -> Any:
    """A psycopg connection to the corpus, or a diagnosis.

    Same database as the price panel — one Postgres service, two concerns —
    so this deliberately reuses `DATABASE_URL` rather than inventing a second
    connection string that could point somewhere else.
    """
    if not settings.database_url:
        raise WireUnavailable(
            "DATABASE_URL is not set — the wire corpus lives in Postgres. "
            "Set it to the Railway Postgres service's URL."
        )
    try:
        import psycopg
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        raise WireUnavailable(
            'psycopg is not installed — pip install -e ".[panel]"'
        ) from exc
    return psycopg.connect(settings.database_url)


# ── schema ────────────────────────────────────────────────────────────────

#: The EWMA baseline as a Postgres aggregate.
#:
#: Escalation is a RECURSIVE fold — each event is classified against the
#: baseline of everything before it — and that is why this is an aggregate
#: rather than arithmetic. The closed form of an EWMA is expressible with
#: window functions, but not USABLE here: it needs (1-alpha)^n, which
#: underflows to zero within a few hundred events and would silently return
#: garbage on dyads holding tens of thousands. A transition function keeps the
#: running state in the same order Python does and stays exact.
#:
#: Postgres computes a frame of `UNBOUNDED PRECEDING .. 1 PRECEDING`
#: incrementally, so this is one pass per dyad rather than O(n^2).
_EWMA_SQL = """
CREATE OR REPLACE FUNCTION wire_ewma_step(state double precision, value double precision)
RETURNS double precision AS $$
    SELECT CASE WHEN state IS NULL THEN value
                ELSE {alpha} * value + {one_minus} * state END
$$ LANGUAGE sql IMMUTABLE
"""

_EWMA_AGGREGATE = """
DO $$ BEGIN
    CREATE AGGREGATE wire_ewma(double precision) (
        SFUNC = wire_ewma_step, STYPE = double precision
    );
EXCEPTION WHEN duplicate_function THEN NULL;
END $$
"""


def apply_schema(conn: Any) -> None:
    """Stand up the corpus: tables, indexes and the EWMA aggregate.

    Idempotent, because it runs on every boot that touches the corpus. The
    alpha baked into the aggregate is read from `escalation.DEFAULT_ALPHA`
    rather than typed here — a second copy of that constant is a second
    definition of what escalation means.
    """
    alpha = float(escalation.DEFAULT_ALPHA)
    with conn.cursor() as cur:
        for statement in pg_schema.ddl():
            cur.execute(statement)
        cur.execute(_EWMA_SQL.format(alpha=alpha, one_minus=1.0 - alpha))
        cur.execute(_EWMA_AGGREGATE)
    conn.commit()


# ── writing ───────────────────────────────────────────────────────────────

def upsert_sources(conn: Any, rows: Iterable[dict[str, Any]]) -> int:
    """The mirrored Source rows — written BEFORE the events that cite them.

    The provenance ordering, enforced here by a real foreign key rather than
    by remembering to call a validator.
    """
    payload = [dict(r) for r in rows]
    if not payload:
        return 0
    columns = [c.name for c in pg_schema.source_spec().columns]
    names = ["source_id", *columns]
    placeholders = ", ".join(["%s"] * len(names))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
    sql = (
        f"INSERT INTO {pg_schema.SOURCE_TABLE} ({', '.join(names)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT (source_id) DO UPDATE SET {updates}"
    )
    with conn.cursor() as cur:
        for row in payload:
            key = row.get("source_id") or row.get("node_id")
            cur.execute(sql, [key, *[row.get(c) for c in columns]])
    conn.commit()
    return len(payload)


def copy_events(conn: Any, rows: Iterable[dict[str, Any]]) -> int:
    """COPY the corpus in. THE REASON THIS STORE EXISTS.

    Kuzu merged these at ~145 events/sec and slowed as it grew; COPY moves the
    same rows in one streaming write. Duplicate ids are dropped rather than
    updated: a wire event is immutable once coded, so a re-run of the same
    artifact is a no-op and a resumed load costs only what is left.

    Staged through a TEMP table because COPY itself has no ON CONFLICT.
    """
    names = pg_schema.columns()
    written = 0
    with conn.cursor() as cur:
        cur.execute(
            f"CREATE TEMP TABLE _wire_stage "
            f"(LIKE {pg_schema.WIRE_TABLE} INCLUDING DEFAULTS) ON COMMIT DROP"
        )
        with cur.copy(
            f"COPY _wire_stage ({', '.join(names)}) FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row([row.get(n) for n in names])
                written += 1
        if written:
            cur.execute(
                f"INSERT INTO {pg_schema.WIRE_TABLE} ({', '.join(names)}) "
                f"SELECT {', '.join(names)} FROM _wire_stage "
                f"ON CONFLICT (node_id) DO NOTHING"
            )
    conn.commit()
    return written


# ── the rescore, as one statement ─────────────────────────────────────────

def rescore(conn: Any, *, region_pack: str | None = None) -> int:
    """Fold Head B's escalation across the corpus.

    THE STEP THAT COST HOURS AND COULD NOT BE RESUMED. In the graph this was a
    Python pass over every event in time order, holding per-dyad state in a
    dict — un-resumable by construction, so an interrupted run left nothing
    behind and the next boot started over. Here it is one UPDATE that the
    database can restart, and the classification is IDENTICAL: each event is
    measured against the baseline of everything before it in its own dyad.

    The band and the direction rule are read off `escalation` for the same
    reason the alpha is — `classify()` is the definition, and this is the same
    definition executed where the data lives.
    """
    band = float(escalation.STABLE_BAND)
    where = "WHERE region_pack = %s" if region_pack else ""
    params = [region_pack] if region_pack else []
    sql = f"""
        UPDATE {pg_schema.WIRE_TABLE} AS e
           SET escalation_baseline  = b.base,
               escalation_magnitude = abs(e.goldstein - b.base),
               escalation_direction = CASE
                   WHEN abs(e.goldstein - b.base) < {band} THEN 'stable'
                   WHEN e.goldstein - b.base < 0          THEN 'escalating'
                   ELSE 'deescalating' END
          FROM (
              SELECT node_id,
                     -- No preceding row means this is the dyad's first event,
                     -- and a dyad's first event IS its own baseline: there is
                     -- no global prior, because a global normal is exactly
                     -- what relational escalation refuses to assume.
                     COALESCE(
                         wire_ewma(goldstein) OVER (
                             PARTITION BY dyad_id
                             ORDER BY event_time, node_id
                             ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                         ),
                         goldstein
                     ) AS base
                FROM {pg_schema.WIRE_TABLE}
                {where}
          ) AS b
         WHERE e.node_id = b.node_id
    """
    with conn.cursor() as cur:
        cur.execute(sql, params * 1)
        touched = cur.rowcount
    conn.commit()
    return int(touched)


def unscored(conn: Any) -> int:
    """How many events Head B has not reached — the rescore's own trigger."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {pg_schema.WIRE_TABLE} "
            f"WHERE escalation_magnitude IS NULL"
        )
        row = cur.fetchone()
    return int(row[0]) if row else 0


# ── reading ───────────────────────────────────────────────────────────────

#: The row contract, matching `core.models.panel.dyad_event_rows` exactly.
#: event_time is rendered back to an ISO STRING because every consumer slices
#: it (`date[:4]`, `date[5:7]` in `quarter_index`) rather than treating it as a
#: date. The column is DATE so the index is real; the boundary converts.
_ROWS_SQL = f"""
    SELECT dyad_id,
           coalesce(dyad_name, dyad_id)          AS dyad_name,
           to_char(event_time, 'YYYY-MM-DD')     AS event_time,
           escalation_direction                  AS direction,
           escalation_magnitude                  AS magnitude,
           goldstein,
           quad_class,
           region_pack
      FROM {pg_schema.WIRE_TABLE}
      {{where}}
     ORDER BY event_time, node_id
"""


def dyad_event_rows(
    conn: Any, *, region_pack: str | None = None
) -> list[dict[str, Any]]:
    """Every dyad-coded wire event with the fields the panel reads.

    Same keys and same order as the graph query it replaces, so
    `models.panel.build` cannot tell the difference.
    """
    where = "WHERE region_pack = %s" if region_pack else ""
    with conn.cursor() as cur:
        cur.execute(_ROWS_SQL.format(where=where), [region_pack] if region_pack else [])
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def iter_dyad_event_rows(
    conn: Any, *, region_pack: str | None = None, batch: int = 50_000
) -> Iterator[dict[str, Any]]:
    """The same rows, streamed.

    A million rows materialised as dicts is real memory, and the fitter reads
    them once in order. A server-side cursor keeps the peak flat for the jobs
    that do not need the whole list at once.
    """
    where = "WHERE region_pack = %s" if region_pack else ""
    with conn.cursor(name="wire_rows") as cur:
        cur.itersize = batch
        cur.execute(_ROWS_SQL.format(where=where), [region_pack] if region_pack else [])
        names = [d[0] for d in cur.description]
        for row in cur:
            yield dict(zip(names, row, strict=True))


def counts(conn: Any) -> dict[str, int]:
    """Events per pack — what a loader reports and a boot logs."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT region_pack, count(*) FROM {pg_schema.WIRE_TABLE} "
            f"GROUP BY region_pack ORDER BY region_pack"
        )
        return {str(pack): int(n) for pack, n in cur.fetchall()}
