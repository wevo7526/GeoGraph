"""The dyad-quarter panel — the forecaster's unit of observation.

One row per dyad per quarter, from the dyad's first observed quarter to its
last, INCLUDING the quarters in which nothing happened. Those zeros are the
point: a panel built only from occupied quarters is a positive-only sample,
and a model trained on one learns that escalation is constant.

The quantity being modelled is INTENSITY — the largest departure from the
dyad's own EWMA baseline seen in the quarter, zero when the dyad was quiet.
Not a rare binary. A binary "did a significant escalation occur in the next
four quarters" is what the earlier exploration used, and it is
anti-correlated with its own best feature at both ends of a burst: at the end
the dyad is active and the next year is quiet, just before one it is quiet
and the next year is violent. Predicting the LEVEL has no such edges, carries
more signal per observation, and is already what the transmission engine
prices — the event study runs on escalation_magnitude.

Read-only. Pure functions over rows; the graph read is one query at the top.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from core.graph import kuzu_store

#: A dyad needs this many occupied quarters before it can be modelled. Below
#: it there is no within-dyad history to normalise against, and the median
#: dyad in the archive has three.
MIN_OCCUPIED_QUARTERS = 8


def quarter_index(date: str) -> int:
    """Quarters since year 0, so lags are arithmetic. Accepts any archive
    resolution — a deep-tier event that knows only its year lands in Q1,
    which is the same convention the near-term forecaster counts with."""
    year = int(date[:4])
    month = int(date[5:7]) if len(date) >= 7 else 1
    return year * 4 + (month - 1) // 3


def quarter_label(index: int) -> str:
    """The inverse, as an ISO date at the quarter's START — what the API and
    the charts show."""
    year, quarter = divmod(index, 4)
    return f"{year}-{quarter * 3 + 1:02d}-01"


def dyad_event_rows(conn: Any) -> list[dict[str, Any]]:
    """Every dyad-coded event with the fields the panel reads."""
    return kuzu_store.query(
        conn,
        "MATCH (e:Event)-[:OF_DYAD]->(d:Dyad) "
        "RETURN d.node_id AS dyad_id, d.name AS dyad_name, "
        "e.event_time AS event_time, e.escalation_direction AS direction, "
        "e.escalation_magnitude AS magnitude, e.goldstein AS goldstein, "
        "e.quad_class AS quad_class, e.region_pack AS region_pack "
        "ORDER BY e.event_time, e.node_id",
    )


def load_rows(db_path: Path) -> list[dict[str, Any]]:
    """One read-only connection, closed on the way out — a graph left open
    holds the single-writer lock and 8 TiB of address space."""
    conn = kuzu_store.connect(db_path, read_only=True)
    try:
        return dyad_event_rows(conn)
    finally:
        kuzu_store.close(conn)


def build(
    rows: list[dict[str, Any]],
    *,
    region_pack: str | None = None,
    cutoff: str | None = None,
    min_occupied: int = MIN_OCCUPIED_QUARTERS,
) -> list[dict[str, Any]]:
    """The panel, ascending by dyad then quarter.

    `cutoff` truncates to events at or before a date — the same as-of
    discipline the near-term forecaster uses, and the only reason a
    walk-forward evaluation of this model can be honest.

    `region_pack` filters to dyads the region has TOUCHED, not to events: a
    dyad's history outside the region is still that dyad's history, and
    dropping it would make the same dyad look different through two lenses.
    """
    if cutoff is not None:
        rows = [r for r in rows if str(r["event_time"]) <= cutoff]

    cells: dict[tuple[str, int], dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "intensity": 0.0, "goldstein": [], "conflict": 0}
    )
    names: dict[str, str] = {}
    regional: set[str] = set()
    for row in rows:
        dyad = str(row["dyad_id"])
        names.setdefault(dyad, str(row["dyad_name"] or dyad))
        if region_pack is None or row["region_pack"] == region_pack:
            regional.add(dyad)
        cell = cells[(dyad, quarter_index(str(row["event_time"])))]
        cell["n"] += 1
        if row["goldstein"] is not None:
            cell["goldstein"].append(float(row["goldstein"]))
        if row["quad_class"] == "material_conflict":
            cell["conflict"] += 1
        # Intensity is the quarter's LARGEST departure, not its mean: a
        # rupture inside a noisy quarter is the event being forecast, and an
        # average over the chatter around it hides exactly that.
        if row["direction"] == "escalating" and row["magnitude"] is not None:
            cell["intensity"] = max(cell["intensity"], float(row["magnitude"]))

    occupied: dict[str, set[int]] = defaultdict(set)
    for dyad, q in cells:
        occupied[dyad].add(q)

    panel: list[dict[str, Any]] = []
    for dyad in sorted(occupied):
        if dyad not in regional or len(occupied[dyad]) < min_occupied:
            continue
        quarters = sorted(occupied[dyad])
        for q in range(quarters[0], quarters[-1] + 1):
            filled = cells.get((dyad, q))
            panel.append({
                "dyad_id": dyad,
                "dyad_name": names[dyad],
                "q": q,
                "date": quarter_label(q),
                # An absent cell is a QUIET quarter, not a missing one.
                "intensity": float(filled["intensity"]) if filled else 0.0,
                "events": int(filled["n"]) if filled else 0,
                "conflict": int(filled["conflict"]) if filled else 0,
                "tone": (
                    sum(filled["goldstein"]) / len(filled["goldstein"])
                    if filled and filled["goldstein"] else 0.0
                ),
            })
    return panel


def series_for(panel: list[dict[str, Any]], dyad_id: str) -> list[dict[str, Any]]:
    """One dyad's rows, ascending — what the arc chart draws."""
    return [row for row in panel if row["dyad_id"] == dyad_id]


def dyad_summary(panel: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-dyad totals, ordered by how much the archive has actually watched
    them: the selector's list, and the evidence bar in one place."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in panel:
        grouped[row["dyad_id"]].append(row)
    out = []
    for dyad, rows_ in grouped.items():
        hot = [r for r in rows_ if r["intensity"] > 0.0]
        out.append({
            "dyad_id": dyad,
            "dyad_name": rows_[0]["dyad_name"],
            "quarters": len(rows_),
            "active_quarters": len(hot),
            "peak_intensity": max((r["intensity"] for r in rows_), default=0.0),
            "mean_intensity": sum(r["intensity"] for r in rows_) / len(rows_),
            "first": rows_[0]["date"],
            "last": rows_[-1]["date"],
        })
    out.sort(key=lambda d: (-d["active_quarters"], d["dyad_id"]))
    return out
