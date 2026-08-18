"""The archive's temporal floor.

Cited deviation from build-spec §3 (locked 1905 → present, 120 years).
Opening at 1972 drops the pre-fiat deep tier (classical gold through
Bretton Woods) so the live graph, the slider and Head B all share the
first calendar year of the fiat-floating regime that every market in the
packs actually trades in.

GDELT itself begins in 1979, so this floor does not halve the modern wire
or the event study — it halves the CALENDAR and removes COW/CINC history
that predates the Nixon shock. Panel rows from before 1972 stay: they are
lookback for 1972 events, not events of their own.

Relationships that STRADDLE 1972 (NATO from 1949, US–Saudi from 1951) are
kept whole. Windows that END before 1972 are dropped.
"""

from __future__ import annotations

from typing import Any

START_YEAR = 1972
START = "1972-01-01"


def covers(when: object) -> bool:
    """Is this ISO-8601 stamp on or after the archive floor?

    Year-only and month-only dates compare correctly: ISO-8601 sorts
    lexically, which is why every date in the graph is a string.
    """
    text = str(when or "").strip()
    return bool(text) and text >= START


def clamp_start(start: str | None) -> str:
    """A range filter never opens before the archive does."""
    if not start or start < START:
        return START
    return start


def drop_events_before(conn: Any, start: str = START) -> dict[str, int]:
    """DETACH DELETE events (and stale estimates/metrics) from before `start`.

    AFFECTED edges hang off Event nodes, so they go with the event. RELATES_TO
    and Dyad nodes stay — those are the knowledge graph, and a 1949 alliance
    that is still in force in 1972 is a live fact. Idempotent.
    """
    from core.graph import kuzu_store

    def _ids(query: str) -> list[str]:
        return [str(row["id"]) for row in kuzu_store.query(conn, query, {"start": start})]

    events = _ids(
        "MATCH (e:Event) WHERE e.event_time < $start RETURN e.node_id AS id"
    )
    estimates = _ids(
        "MATCH (s:AttributeEstimate) WHERE s.as_of < $start RETURN s.node_id AS id"
    )
    metrics = _ids(
        "MATCH (m:NetworkMetric) WHERE m.window_end < $start RETURN m.node_id AS id"
    )
    return {
        "Event": kuzu_store.delete_nodes(conn, "Event", events),
        "AttributeEstimate": kuzu_store.delete_nodes(conn, "AttributeEstimate", estimates),
        "NetworkMetric": kuzu_store.delete_nodes(conn, "NetworkMetric", metrics),
    }


def drop_gdelt_events(conn: Any, *, limit: int = 500) -> int:
    """DETACH DELETE a slice of the graph's GDELT copy.

    The corpus is the wire (forecasts, games, the wire page). AFFECTED on
    those events is a 2 KB/edge duplicate of `event_study_runs`. The spine,
    dyads and RELATES_TO stay. Bound `limit` so this can run as a job tick.
    """
    from core.graph import kuzu_store

    rows = kuzu_store.query(
        conn,
        "MATCH (e:Event) WHERE starts_with(e.node_id, 'event:gdelt-') "
        "RETURN e.node_id AS id LIMIT $limit",
        {"limit": limit},
    )
    ids = [str(row["id"]) for row in rows]
    return kuzu_store.delete_nodes(conn, "Event", ids)
