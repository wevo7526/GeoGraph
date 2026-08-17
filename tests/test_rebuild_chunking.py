"""The refill's READ must be bounded, and its chunks must not cut an event.

`panel_effect_rows` was written for a repair CHILD, where materialising a
million rows was a fine trade. It became a JOB inside the API process, where
the peak is not spare: on 2026-08-17 the wire finished, the refill ran for the
first time with real work, read every remaining row in one statement and took
the container to 7.1 GB of a 7.45 GB limit. No memory guard can catch that —
the allocation happens inside a single call, between two checks.

The second property matters as much as the first. `rebuild.refill` groups rows
by event and the resume marker advances by event id, so a chunk that ended
half way through an event would mark it done and silently drop its remaining
markets — a measurement lost with nothing to show it.
"""

from __future__ import annotations

from typing import Any

from core.transmission import rebuild


class _Cursor:
    """A cursor that honours LIMIT over a fixed row set."""

    description = [
        (n,) for n in (
            "event_node_id", "market_ticker", "effect_window", "resolution",
            "status", "raw_return", "expected_return", "abnormal_return",
            "t_stat", "p_value", "method",
        )
    ]

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._all = rows
        self._out: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        after = params[1]
        rows = [r for r in self._all if r[0] > after]
        if " LIMIT " in sql:
            rows = rows[: int(params[2])]
        self._out = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._out

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _Panel:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def cursor(self) -> _Cursor:
        return _Cursor(self._rows)


def _rows(events: int, per_event: int) -> list[tuple[Any, ...]]:
    out = []
    for e in range(events):
        for m in range(per_event):
            out.append((f"event:e{e:04d}", f"T{m}", "CAR-0", "day",
                        "computed", 0.1, 0.1, 0.1, 1.0, 0.5, "market_model"))
    return out


def test_the_read_is_bounded_by_limit():
    panel = _Panel(_rows(events=100, per_event=10))
    got = rebuild.panel_effect_rows(panel, limit=55)
    assert len(got) <= 55


def test_a_chunk_never_ends_mid_event():
    """55 rows over 10-market events cuts event 5 in half; the partial event
    is left for the next tick so the marker cannot skip its other markets."""
    panel = _Panel(_rows(events=100, per_event=10))
    got = rebuild.panel_effect_rows(panel, limit=55)
    per_event: dict[str, int] = {}
    for row in got:
        per_event[row["event_node_id"]] = per_event.get(row["event_node_id"], 0) + 1
    assert set(per_event.values()) == {10}, per_event


def test_a_single_oversized_event_still_makes_progress():
    """If one event has more rows than the limit, trimming it would return
    nothing and the refill would never advance past it."""
    panel = _Panel(_rows(events=1, per_event=40))
    got = rebuild.panel_effect_rows(panel, limit=10)
    assert got, "a chunk that is entirely one event must not be trimmed away"


def test_without_a_limit_everything_comes_back():
    panel = _Panel(_rows(events=5, per_event=3))
    assert len(rebuild.panel_effect_rows(panel)) == 15


def test_after_resumes_past_the_marker():
    panel = _Panel(_rows(events=5, per_event=2))
    got = rebuild.panel_effect_rows(panel, after="event:e0002")
    assert {r["event_node_id"] for r in got} == {"event:e0003", "event:e0004"}
