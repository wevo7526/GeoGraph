"""The corpus, shaped for the API and cached for the process lifetime.

WHY A PROCESS-LIFETIME CACHE IS CORRECT HERE, when caching is usually where
staleness bugs live: the corpus is IMMUTABLE for the life of a process. Its
source is the artifacts baked into the image, and they change only when a new
image deploys — which is a new process. There is no writer to race and no
invalidation to get wrong; the cache is just the corpus's derived tables
computed once instead of per request.

WHAT IS KEPT IS THE SMALL DERIVED SHAPE, NOT THE ROWS. Parsing 1.33M events
yields hundreds of megabytes of dicts; the API serves dyad-QUARTER tables
(tens of thousands of rows) and a joint-action map. `warm()` parses each pack
once, derives those, and lets the raw rows go — so the steady-state cost is
megabytes, and the parse cost is paid once at startup instead of on a user's
first click.

The routers treat this as the FIRST source and keep their graph reads as the
fallback, so a checkout without artifacts (or a lens that never shipped one)
serves exactly as before.
"""

from __future__ import annotations

import bisect
import threading
from collections.abc import Iterator
from typing import Any

from core.models import panel as panel_module
from core.wire import corpus

_LOCK = threading.Lock()
_TABLES: dict[str, list[dict[str, Any]]] = {}
_JOINT: dict[tuple[str, int], tuple[str, str]] = {}
#: The per-side quad-class counts behind `_JOINT` — a few integers per
#: dyad-quarter — kept so a FAMILY'S reading (the ally space reads the same
#: quarter as commit / affirm / withhold) can be derived after the raw rows
#: are gone, without re-parsing the wire. `_JOINT_BY_SPACE` memoises those.
_QUADS: dict[tuple[str, int], dict[str, dict[str, int]]] = {}
_JOINT_BY_SPACE: dict[str, dict[tuple[str, int], tuple[str, str]]] = {}
# The EXPLORER VIEW: per pack, a time-sorted list of tab-joined slim rows
# (event_time first, so lexical bisect IS the window query), plus per-pack
# year counts for the coverage strip. Kept compact — one str per event
# instead of one dict — so holding the whole wire for the explorer costs
# ~300 MB where the raw rows cost ~1.4 GB. Built at warm() from the same
# parse the tables come from, BEFORE the raw rows are evicted.
_EVENTS: dict[str, list[str]] = {}
_COVERAGE: dict[str, dict[str, int]] = {}
_WARMED = False

#: Field order inside a slim row. event_time leads for bisect; the rest is
#: exactly the /api/events row contract (cameo_code is the API's name for
#: action_cameo_code). Tab-separated: the fields come off a TSV parse, so an
#: embedded tab is impossible.
_SLIM_FIELDS = (
    "event_time", "node_id", "name", "cameo_code", "quad_class", "goldstein",
    "escalation_direction", "escalation_magnitude", "escalation_baseline",
    "fidelity_tier", "temporal_resolution", "source_scale", "region_pack",
    "initiator_id", "target_id", "dyad_id", "source_id",
    # THE COERCION VERDICT RIDES ALONG, because a model served features
    # rebuilt from a different source than it was trained on is a model
    # measuring something else. `models.hostility` is fitted on corpus rows;
    # the region context serves it from this view, and the first wiring of it
    # rebuilt the features from the quarterly panel instead — where the
    # severity and fight shares do not exist. US-Russia scored 0.41 served
    # against 0.82 trained, which is the whole difference between "rival" and
    # "adversary" (2026-08-17).
    "coercion",
    # DISPLAY COLUMNS the Intel headline needs. Dropping them made every
    # corpus fight look like an A–B war: "Russia fought United Kingdom" with
    # no place, because ActionGeo never reached `wire_headline`. They are
    # corpus-only (not Event properties) and cheap as short tokens.
    "action_geo", "initiator_iso3", "target_iso3",
    "num_sources", "actor1_type", "actor2_type", "co_participation",
)
_FLOAT_FIELDS = {"goldstein", "escalation_magnitude", "escalation_baseline"}
_BOOL_FIELDS = {"coercion", "co_participation"}
_INT_FIELDS = {"num_sources"}


def _slim(row: dict[str, Any]) -> str:
    parts = []
    for field in _SLIM_FIELDS:
        value = row.get("action_cameo_code" if field == "cameo_code" else field)
        if field in _BOOL_FIELDS:
            value = "1" if value else ""
        parts.append("" if value is None else str(value))
    return "\t".join(parts)


def _unslim(joined: str) -> dict[str, Any]:
    values = joined.split("\t")
    row: dict[str, Any] = {}
    for field, value in zip(_SLIM_FIELDS, values, strict=True):
        if field in _FLOAT_FIELDS:
            row[field] = float(value) if value else None
        elif field in _INT_FIELDS:
            row[field] = int(value) if value else None
        elif field in _BOOL_FIELDS:
            row[field] = bool(value)
        else:
            row[field] = value or None
    return row


def available() -> bool:
    """Whether the image ships a corpus at all."""
    return bool(corpus.installed())


def reset() -> None:
    """Drop the warmed state.

    Written for tests, which repoint `GEOGRAPH_DERIVED_DIR` between cases and
    must not see the previous directory's tables. Production called it never,
    on the reasoning that the corpus is immutable for the life of a process —
    true for as long as the artifacts only ever shipped inside the image.

    THE `harvest` JOB ENDED THAT (2026-08-17). It appends new days to the
    volume's overlay, so the corpus a process started with is no longer the
    corpus on disk, and `work.harvest` calls this followed immediately by
    `warm()` when a day actually landed. Immediately, because `table()`
    rebuilds lazily: invalidating without refilling hands a ~20s re-parse to
    the next user request instead of to the job that caused it.
    """
    global _WARMED
    with _LOCK:
        _TABLES.clear()
        _JOINT.clear()
        _QUADS.clear()
        _JOINT_BY_SPACE.clear()
        _EVENTS.clear()
        _COVERAGE.clear()
        _WARMED = False


def warm() -> dict[str, Any]:
    """Parse once, derive every region's tables, discard the rows.

    Idempotent and thread-safe: the app calls it at startup, but a request
    that arrives first (or a startup that skipped it) triggers the same work
    through `table()` and waits on the same lock rather than duplicating it.
    """
    global _WARMED
    with _LOCK:
        if _WARMED:
            return {"warmed": False, "regions": sorted(_TABLES)}
        if not available():
            _WARMED = True
            return {"warmed": False, "regions": []}

        from core.games import transition  # local: the API can serve without games

        for name in corpus.installed():
            panel_rows, game_rows = corpus.views(name)
            _TABLES[name] = panel_module.build(panel_rows, region_pack=name)
            # The explorer view, from the same cached parse: slim rows sorted
            # by (event_time, node_id) — the joined string starts with those
            # two fields, so sorting the strings sorts the events.
            slim = sorted(_slim(row) for row in corpus.load(name))
            _EVENTS[name] = slim
            years: dict[str, int] = {}
            for joined in slim:
                year = joined[:4]
                years[year] = years.get(year, 0) + 1
            _COVERAGE[name] = years
            # One pooled map rather than one per region. Shared-roster dyads
            # (RUS–TUR, TUR–USA) appear in more than one lens, so a later
            # lens's update overwrites the earlier one's keys — harmlessly,
            # because both lenses carry the same underlying events for a
            # shared dyad and derive the same joint actions.
            counts = transition.quad_counts(game_rows, quarter_of=panel_module.quarter_index)
            _QUADS.update(counts)
            _JOINT.update(transition.joint_from_counts(counts))
        # Pooled coverage, deduped by event id (shared-roster events ship in
        # more than one lens): computed once here — the transient id set is
        # freed when warm returns — so the no-pack ask never rescans 1.3M rows.
        pooled_years: dict[str, int] = {}
        pooled_seen: set[str] = set()
        for name in sorted(_EVENTS):
            for line in _EVENTS[name]:
                event_id = line.split("\t", 2)[1]
                if event_id in pooled_seen:
                    continue
                pooled_seen.add(event_id)
                pooled_years[line[:4]] = pooled_years.get(line[:4], 0) + 1
        _COVERAGE["*"] = pooled_years
        # The no-region view the dyad ledger serves — through the DEDUPED
        # pooled reader, so a shared dyad's events count once, and with
        # `panel.build` defining the ordering rather than pack iteration
        # order. load() is cached, so this re-projects without re-parsing.
        _TABLES["*"] = panel_module.build(corpus.all_panel_rows(), region_pack=None)
        # The derived tables are all the API ever serves; the parsed raw rows
        # behind them are ~1.4 GB across three lenses and would otherwise sit
        # in the row cache for the process lifetime.
        corpus.evict()
        _WARMED = True
        return {"warmed": True, "regions": sorted(k for k in _TABLES if k != "*")}


def table(region: str | None) -> list[dict[str, Any]] | None:
    """The dyad-quarter table for one region (None = every lens), or None if
    no corpus backs it — the caller falls back to the graph."""
    if not available():
        return None
    if not _WARMED:
        warm()
    return _TABLES.get(region or "*")


def joint_actions(
    space: Any = None,
) -> dict[tuple[str, int], tuple[str, str]] | None:
    """(dyad, quarter) → joint action, across every installed lens — in a
    family's space when one is given (derived once from the retained quad
    counts and memoised), the adversary's otherwise."""
    if not available():
        return None
    if not _WARMED:
        warm()
    if space is None or space.family == "adversary":
        return _JOINT
    cached = _JOINT_BY_SPACE.get(space.family)
    if cached is None:
        from core.games import transition

        with _LOCK:
            cached = _JOINT_BY_SPACE.get(space.family)
            if cached is None:
                cached = transition.joint_from_counts(_QUADS, space)
                _JOINT_BY_SPACE[space.family] = cached
    return cached


def iter_rows_of(pack: str) -> Iterator[dict[str, Any]]:
    """Every scored row of one lens, ONE AT A TIME, from the retained slim view
    — the wire's events with Head B's coding, in (event_time, node_id) order.
    `dyad_id`, `initiator_id`, `target_id`, `escalation_*` and `goldstein` all
    ride on it, which is everything the study and the wire page need
    (the graph no longer holds a GDELT copy — see `core.api.work.wire`).

    AN ITERATOR, BECAUSE THE LIST WAS A THREE-GIGABYTE SPIKE. This module's own
    docstring is the promise it broke: "what is kept is the small derived
    shape, not the rows … parsing 1.33M events yields hundreds of megabytes of
    dicts". `rows_of` handed exactly those dicts back — every row of a lens
    materialised at once, and its one caller then built a filtered list beside
    it. On 2026-08-17 that took the container from ~4 GB to the 8 GB ceiling
    within four seconds of "job: wire starting", four container lives in a
    row, with the deploy CRASHED at the end of it.

    The slim strings stay; only the caller's window of them becomes objects.
    """
    if not available():
        return
    if not _WARMED:
        warm()
    for line in _EVENTS.get(pack, []):
        yield _unslim(line)


def events_for_dyad(dyad_id: str, *, limit: int = 400) -> list[dict[str, Any]]:
    """Wire events on one dyad, from the slim corpus view.

    The graph no longer holds a GDELT copy, so a case built on a roster
    pair cannot walk INITIATED_BY to find membership. This scan is the
    membership test: tab-contains then unslim, capped, deduped by id.
    Empty means the corpus is dark or this pair has no coded wire, never
    that nothing happened.
    """
    if not available() or not dyad_id:
        return []
    if not _WARMED:
        warm()
    needle = f"\t{dyad_id}\t"
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for slim in _EVENTS.values():
        for line in slim:
            if needle not in line:
                continue
            row = _unslim(line)
            if str(row.get("dyad_id") or "") != dyad_id:
                continue
            node_id = str(row.get("node_id") or "")
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            out.append(row)
            if len(out) >= limit:
                return out
    return out


# ── the explorer view: window queries over the wire ─────────────────────────


def _window_of(pack: str, start: str | None, end: str | None) -> list[str]:
    slim = _EVENTS.get(pack, [])
    lo = bisect.bisect_left(slim, start) if start else 0
    # end is INCLUSIVE and dates sort lexically, so the first row past the
    # window is the first one whose event_time exceeds `end` at any length —
    # '\x7f' sorts after every character a date or id contains.
    hi = bisect.bisect_right(slim, end + "\x7f") if end else len(slim)
    return slim[lo:hi]


def events_window(
    pack: str | None,
    start: str | None,
    end: str | None,
    limit: int,
    *,
    newest_first: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """Wire events inside [start, end] — `(rows, truncated)`. No pack = every
    lens, deduped by event id (shared-roster events ship in more than one
    lens's artifacts). `newest_first` takes the most RECENT `limit` events,
    which is what a dense window wants; otherwise the oldest."""
    if not available():
        return [], False
    if not _WARMED:
        warm()
    packs_to_read = [pack] if pack else sorted(_EVENTS)
    joined: list[str] = []
    for name in packs_to_read:
        joined.extend(_window_of(name, start, end))
    joined.sort(reverse=newest_first)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    truncated = False
    for line in joined:
        node_id = line.split("\t", 2)[1]
        if node_id in seen:
            continue
        seen.add(node_id)
        if len(rows) >= limit:
            truncated = True
            break
        rows.append(_unslim(line))
    return rows, truncated


def coverage(pack: str | None) -> dict[str, int] | None:
    """Wire events per year for the slider's coverage strip, or None when no
    corpus backs the ask. No pack = every lens, deduped by event id."""
    if not available():
        return None
    if not _WARMED:
        warm()
    return dict(_COVERAGE.get(pack if pack is not None else "*", {}))


def event(node_id: str) -> dict[str, Any] | None:
    """One wire event by id, or None. A linear scan — this backs the detail
    panel's graph-miss fallback, one user click at a time, and a per-id index
    would cost ~150 MB to save ~100 ms."""
    if not available():
        return None
    if not _WARMED:
        warm()
    needle = f"\t{node_id}\t"
    for slim in _EVENTS.values():
        for line in slim:
            if needle in line and line.split("\t", 2)[1] == node_id:
                return _unslim(line)
    return None
