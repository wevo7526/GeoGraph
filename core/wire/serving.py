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

import threading
from typing import Any

from core.models import panel as panel_module
from core.wire import corpus

_LOCK = threading.Lock()
_TABLES: dict[str, list[dict[str, Any]]] = {}
_JOINT: dict[tuple[str, int], tuple[str, str]] = {}
_WARMED = False


def available() -> bool:
    """Whether the image ships a corpus at all."""
    return bool(corpus.installed())


def reset() -> None:
    """Drop the warmed state — FOR TESTS. Production never calls this: the
    corpus is immutable per process, so there is nothing to re-warm to. Tests
    repoint `GEOGRAPH_DERIVED_DIR` between cases, and warmed tables from the
    previous directory must not survive the switch."""
    global _WARMED
    with _LOCK:
        _TABLES.clear()
        _JOINT.clear()
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

        pooled_panel: list[dict[str, Any]] = []
        for name in corpus.installed():
            panel_rows, game_rows = corpus.views(name)
            pooled_panel.extend(panel_rows)
            _TABLES[name] = panel_module.build(panel_rows, region_pack=name)
            # One pooled map rather than one per region: (dyad, quarter) keys
            # are globally unique because a dyad belongs to one lens.
            _JOINT.update(
                transition.joint_actions(
                    game_rows, quarter_of=panel_module.quarter_index
                )
            )
        # The no-region view the dyad ledger serves. Built from the pooled
        # rows rather than by concatenating the per-region tables, so its
        # ordering is what `panel.build` defines and not an accident of pack
        # iteration order.
        _TABLES["*"] = panel_module.build(pooled_panel, region_pack=None)
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


def joint_actions() -> dict[tuple[str, int], tuple[str, str]] | None:
    """(dyad, quarter) → joint action, across every installed lens."""
    if not available():
        return None
    if not _WARMED:
        warm()
    return _JOINT
