"""Regime segmentation — the shared conditioning layer (build-spec section 13).

Loads crosswalks/regimes.yaml and answers "which monetary order and polarity
epoch was date X in?". Every event and window gets tagged; analogy and
forecasting condition on the answer and NEVER match across regime boundaries.
Non-stationarity over 120 years is the central risk (section 19) and this
module is the guard rail.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

_REGIMES_PATH = (
    Path(__file__).resolve().parent.parent / "ontology" / "crosswalks" / "regimes.yaml"
)


@functools.lru_cache(maxsize=1)
def segmentation() -> dict[str, list[dict[str, Any]]]:
    """The full segmentation, keyed by RegimeKind value, ordered by start."""
    with open(_REGIMES_PATH, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return {kind: sorted(entries, key=lambda e: e["start"]) for kind, entries in raw.items()}


def regime_at(date: str, kind: str) -> dict[str, Any] | None:
    """The regime of `kind` covering an ISO-8601 date, or None before the
    archive starts. String comparison is correct here — ISO-8601 sorts
    lexically, which is the same reason the graph stores dates as strings."""
    entries = segmentation().get(kind)
    if entries is None:
        raise KeyError(
            f"kind {kind!r} is not a RegimeKind. Valid: {sorted(segmentation())}"
        )
    for entry in entries:
        if entry["start"] <= date and (entry["end"] is None or date < entry["end"]):
            return entry
    return None


def regimes_at(date: str) -> dict[str, dict[str, Any] | None]:
    """Both kinds at once — the tag every event and window carries."""
    return {kind: regime_at(date, kind) for kind in segmentation()}


def comparable(date_a: str, date_b: str, kind: str = "monetary_order") -> bool:
    """Whether two dates sit in the SAME regime of `kind` — the analogy
    engine's admissibility test, not a similarity score."""
    a, b = regime_at(date_a, kind), regime_at(date_b, kind)
    return a is not None and b is not None and a["id"] == b["id"]


def as_nodes() -> list[dict[str, Any]]:
    """Regime rows shaped for kuzu_store.merge_nodes — how regimes.yaml
    becomes Regime nodes at seed time."""
    rows: list[dict[str, Any]] = []
    for kind, entries in segmentation().items():
        for entry in entries:
            rows.append(
                {
                    "node_id": f"regime:{entry['id']}",
                    "name": entry["name"],
                    "kind": kind,
                    # start_date/end_date, not start/end: `end` is a Kuzu
                    # reserved word — see the ontology's Regime class.
                    "start_date": entry["start"],
                    "end_date": entry["end"] or "",
                }
            )
    return rows
