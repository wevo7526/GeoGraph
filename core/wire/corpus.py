"""The wire corpus read straight from the artifacts in the image.

WHY THIS EXISTS ALONGSIDE `store.py`. The corpus has three readers and they do
not all have a database: the deployed API reads Postgres, but the OFFLINE
fitters (`fit_game.py`, `train_forecaster.py`) run on a developer's machine
and commit a hashed artifact. Making them depend on a populated Postgres would
put a database between a developer and a reproducible fit, and the whole point
of fitting offline is that the numbers in `models/*.json` can be re-derived by
anyone holding the repo.

So this module is the corpus as a PURE FUNCTION OF THE REPOSITORY: the
artifacts are in git, the parser is deterministic, the crosswalks are
deterministic, and Head B is deterministic. Same commit in, same rows out, no
services involved.

IT IS THE SAME CORPUS, not a parallel one. `store.copy_events` writes exactly
these rows, so Postgres holds what this produces rather than something derived
differently — which is what lets the fitters read either source and get the
same answer.
"""

from __future__ import annotations

import functools
import gzip
import os
from pathlib import Path
from typing import Any

from core import packs
from core.classifier import coercion as coercion_module
from core.classifier import escalation
from core.ingestion import gdelt

_DEFAULT_DERIVED = Path(__file__).resolve().parent.parent.parent / "data" / "derived"


def derived_dir() -> Path:
    """Where the artifacts live — env-overridable, READ PER CALL.

    `GEOGRAPH_DERIVED_DIR` exists for test isolation more than for ops: the
    corpus is corpus-FIRST in every consumer, so without an override a test
    that builds a three-event fixture graph gets 1.33M real events unioned
    into it and asserts against the archive instead of the fixture. The test
    conftest points this at an empty directory by default and `test_wire.py`
    opts back in. Read at call time, not import time, so a monkeypatched env
    var takes effect whenever the module was imported.
    """
    override = os.getenv("GEOGRAPH_DERIVED_DIR")
    return Path(override) if override else _DEFAULT_DERIVED

#: Matches `backfill_gdelt.py --min-mentions`'s default, so the graph path and
#: this one keep the same events.
MIN_MENTIONS = 10

#: The artifacts are latin-1. GDELT's raw exports carry bytes that are not
#: valid UTF-8 and the harvest wrote them through unchanged.
_ENCODING = "latin-1"


def artifacts_for(pack_name: str) -> list[Path]:
    """Every shipped artifact for one lens, oldest first."""
    return sorted(derived_dir().glob(f"gdelt-{pack_name}-*.tsv.gz"))


def _roster(pack: Any) -> dict[str, dict[str, Any]]:
    return {
        a["iso3"]: {"node_id": a["id"], "name": a["name"]}
        for a in pack.actors
        if a.get("iso3")
    }


def parse_artifact(pack: Any, artifact: Path) -> tuple[list[dict[str, Any]], Any]:
    """One artifact → unscored corpus rows, through the shared parser.

    The graph's INITIATED_BY / DIRECTED_AT / OF_DYAD edges become columns here,
    because every bulk reader groups by dyad. `escalation.dyad_id` computes the
    key — the same pure function the graph uses, so the stores cannot disagree
    about what a dyad is.
    """
    roster = _roster(pack)
    names = {entry["node_id"]: entry["name"] for entry in roster.values()}
    # The declared alliance windows this pack's pairs carry — the standing
    # half of the co-participation reading (see `family.is_co_participation`).
    from core.games import family as family_module

    windows, _sources = family_module.ally_windows(pack)
    with gzip.open(artifact, "rt", encoding=_ENCODING) as fh:
        events, edges, result = gdelt.parse_lines(
            fh,
            actors_by_iso3=roster,
            region_pack=pack.name,
            min_mentions=MIN_MENTIONS,
            external_powers=pack.external_powers,
        )

    # Indexed by event rather than zipped: `parse_lines` appends three edges per
    # event today, and a positional assumption would break silently the day it
    # appends a fourth.
    initiator: dict[str, str] = {}
    target: dict[str, str] = {}
    for edge in edges:
        if edge["kind"] == "INITIATED_BY":
            initiator[edge["src"]] = edge["dst"]
        elif edge["kind"] == "DIRECTED_AT":
            target[edge["src"]] = edge["dst"]

    rows: list[dict[str, Any]] = []
    for event in events:
        node_id = event["node_id"]
        actor_a, actor_b = initiator.get(node_id), target.get(node_id)
        if not actor_a or not actor_b:
            # Never infer an end to tidy a parse failure — drop and count.
            result.drop("event without both actor ends")
            continue
        first, second = sorted((actor_a, actor_b))
        row = {
            **event,
            "dyad_id": escalation.dyad_id(actor_a, actor_b),
            "dyad_name": f"{names.get(first, first)}–{names.get(second, second)}",
            "initiator_id": actor_a,
            "target_id": actor_b,
            "source_id": gdelt.SOURCE_GDELT,
        }
        # ALLIES CODED IN MATERIAL CONFLICT ON THIRD-COUNTRY SOIL are read as
        # co-participants (fighting together there), not adversaries. The raw
        # code stays; the flag rides beside it for the readers that count.
        row["co_participation"] = family_module.is_co_participation(row, windows)
        # AND WHETHER IT IS COERCION BETWEEN THE TWO STATES AT ALL. The quad
        # class is a coding of a sentence; this is the claim about states, and
        # every counter reads it rather than re-deriving one of its own.
        row["coercion"] = coercion_module.counts_as_coercion(
            row,
            allied=family_module.allied_in(
                windows.get(str(row.get("dyad_id"))),
                family_module._year_of(row.get("event_time"), 0),
            ),
        )
        rows.append(row)
    return rows, result


def score(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold Head B's escalation across the corpus, IN PLACE and IN TIME ORDER.

    The ordering is not a detail — the baseline is history, and history has an
    order. Sorting by (event_time, node_id) matches what the graph query does,
    so a row scored here and the same row scored on the graph path get the same
    baseline rather than merely a similar one.

    A dyad's first event IS its own baseline: there is no global prior, because
    a global normal is exactly what relational escalation refuses to assume.
    """
    rows.sort(key=lambda r: (r["event_time"], r["node_id"]))
    tracker = escalation.DyadTracker()
    for row in rows:
        row.update(tracker.observe(row["dyad_id"], float(row["goldstein"])))
    return rows


def forecast_rows(pack_names: list[str] | None = None) -> list[dict[str, Any]]:
    """The corpus in the shape `reasoning.forecasting.dyad_event_rows` reads.

    `baseline` is PER EVENT and AS OF that event: the dyad's standing EWMA
    immediately after folding the event in, recovered from the slots Head B
    already left on the scored row (`escalation_baseline` is what the event
    was measured against; one `update_baseline` step folds it). A dyad's
    latest row at or before any cutoff therefore carries its standing
    baseline AT that cutoff — which is what keeps the walk-forward backtest
    honest — and the final row's value is exactly the number the graph
    snapshots on the Dyad node. Stamping every row with the tracker's FINAL
    state (the old shape) leaked the archive's end into every historical
    cutoff.
    """
    # DEDUPED ACROSS LENSES: rosters overlap (mena and eurasia share USA, RUS
    # and TUR), so a RUS–TUR event ships in BOTH packs' artifacts — 627 shared
    # ids in 2022 alone. Concatenating would count those dyads' every event
    # twice. First lens wins, in installed() order — the same rule the
    # Postgres wire's ON CONFLICT DO NOTHING produces, so the stores agree.
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for name in pack_names if pack_names is not None else installed():
        for row in load(name):
            if row["node_id"] not in seen_ids:
                seen_ids.add(row["node_id"])
                rows.append(row)
    rows.sort(key=lambda r: (r["event_time"], r["node_id"]))
    return [
        {
            "dyad_id": row["dyad_id"],
            "dyad_name": row["dyad_name"],
            "baseline": escalation.update_baseline(
                row["escalation_baseline"], float(row["goldstein"])
            ),
            "event_id": row["node_id"],
            "event_time": row["event_time"],
            "direction": row["escalation_direction"],
            "magnitude": row["escalation_magnitude"],
            "region_pack": row["region_pack"],
        }
        for row in rows
    ]


#: ONE ENTRY, not eight. The parsed rows are ~450 MB a pack as dicts, and
#: `evict()` below exists precisely because holding them all is most of an
#: 8 GB container. But eviction only helps until the next reader repopulates:
#: `reasoning/structural` and `reasoning/forecasting` both call `load()` per
#: pack inside background jobs, so with eight slots all three packs came back
#: and STAYED — the corpus was evicted at warm and fully resident again an
#: hour later. The API's own read path never comes here (it reads
#: `wire/serving`'s slim tables), and the jobs that do walk packs serially, so
#: one slot costs a ~5s re-parse per pack per job and bounds this cache at one
#: lens instead of three.
@functools.lru_cache(maxsize=1)
def _loaded(pack_name: str, scored: bool, derived: str) -> tuple[dict[str, Any], ...]:
    # `derived` is in the key so a test that repoints the directory cannot be
    # served rows parsed from the previous one.
    pack = packs.load(pack_name)
    rows: list[dict[str, Any]] = []
    for artifact in artifacts_for(pack_name):
        parsed, _result = parse_artifact(pack, artifact)
        rows.extend(parsed)
    if scored:
        score(rows)
    return tuple(rows)


def load(pack_name: str, *, scored: bool = True) -> list[dict[str, Any]]:
    """The whole corpus for one lens, parsed and (by default) scored.

    Scoring spans EVERY artifact rather than each one, because a dyad's
    baseline does not restart at a file boundary. That is the same reason the
    graph path passes `--skip-rescore` per artifact and folds once at the end.

    CACHED FOR THE PROCESS LIFETIME, which is correct for the same reason the
    serving cache is: the artifacts are baked into the image and cannot change
    under a running process, so a re-parse returns the same rows for ~5s a
    pack. The returned LIST is fresh per call — callers sort and extend it —
    but the row dicts are shared: read them, never mutate them.
    """
    return list(_loaded(pack_name, scored, str(derived_dir())))


def _as_panel_row(row: dict[str, Any]) -> dict[str, Any]:
    """One corpus row in the shape `models.panel.build` reads."""
    return {
        "dyad_id": row["dyad_id"],
        "dyad_name": row["dyad_name"],
        "event_time": row["event_time"],
        "direction": row["escalation_direction"],
        "magnitude": row["escalation_magnitude"],
        "goldstein": row["goldstein"],
        "quad_class": row["quad_class"],
        "region_pack": row["region_pack"],
        "co_participation": bool(row.get("co_participation", False)),
        "coercion": bool(row.get("coercion", False)),
    }


def _as_game_row(row: dict[str, Any]) -> dict[str, Any]:
    """One corpus row in the shape `games.transition.event_rows` reads.

    The dyad's two sides come back SORTED, matching `Dyad.actor_a_id` /
    `actor_b_id` in the graph — those hold the pair in sorted order so the id
    is stable and do not encode a direction. Direction is `initiator`.
    """
    side_a, side_b = sorted((row["initiator_id"], row["target_id"]))
    return {
        "dyad_id": row["dyad_id"],
        "actor_a": side_a,
        "actor_b": side_b,
        "initiator": row["initiator_id"],
        "event_time": row["event_time"],
        "quad_class": row["quad_class"],
        "region_pack": row["region_pack"],
        "co_participation": bool(row.get("co_participation", False)),
        "coercion": bool(row.get("coercion", False)),
    }


def panel_rows(pack_name: str) -> list[dict[str, Any]]:
    """The corpus in the shape `models.panel.build` reads.

    Same keys as `models.panel.dyad_event_rows` and `wire.store`, so the three
    sources are interchangeable to every consumer downstream.
    """
    return [_as_panel_row(row) for row in load(pack_name)]


def installed() -> list[str]:
    """Packs that actually ship a corpus, in a stable order."""
    return [name for name in packs.available() if artifacts_for(name)]


def evict() -> None:
    """Drop the parsed-row cache. `serving.warm()` calls this once its derived
    tables are built: the API process never reads the raw rows again, and the
    cached tuples for three lenses hold ~1.4 GB — most of an 8 GB container —
    for nothing. Offline fitters and the boot's forecast children run in their
    own processes, where the cache simply repopulates on first use."""
    _loaded.cache_clear()


def all_panel_rows() -> list[dict[str, Any]]:
    """Every lens's corpus, pooled — what the intensity model trains on.

    SCORED PER PACK, POOLED AFTER. Escalation baselines are per dyad; a dyad
    shared by two lenses (overlapping rosters) carries the same event set in
    both, so folding each pack separately still gives the same answer as
    folding a pooled stream while keeping the sort O(n log n) per pack. The
    pooled ORDER still matters to the reader, so the result is sorted again.

    Pooling is right here and wrong in the gate: the model is trained across
    lenses but scored WITHIN dyad, because 70% of the label's variance is
    within dyad and a pooled score mostly measures which dyad it is looking at.

    DEDUPED BY EVENT: shared-roster dyads (RUS–TUR, TUR–USA) ship in more than
    one lens's artifacts, and a pooled read must count each event once. First
    lens wins, in installed() order.
    """
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for name in installed():
        for row in load(name):
            if row["node_id"] not in seen_ids:
                seen_ids.add(row["node_id"])
                rows.append(_as_panel_row(row))
    rows.sort(key=lambda r: (r["event_time"], r["dyad_id"]))
    return rows


def views(pack_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Both views from ONE parse — `(panel_rows, game_rows)`.

    The fitter needs both, and they are two projections of the same rows.
    Parsing twice would double the only expensive step for no benefit, and
    worse, it would let the two views drift if the parse were ever made
    non-deterministic.
    """
    rows = load(pack_name)
    return [_as_panel_row(r) for r in rows], [_as_game_row(r) for r in rows]
