"""GDELT 2.0 overlay on a frozen snapshot.

The scored corpus, the counted kernel, the fitted payoffs and the measured
AFFECTED map are the engine's weights. This module scores the newest
15-minute export against those weights — Head B vs each pair's snapshot
EWMA — and NEVER writes a graph edge.

Consumers (the live wire, a game's opening state, the relationship series)
read the overlay. The kernel, payoffs, CINC and the transmission map do not.
"""

from __future__ import annotations

from typing import Any

from core.classifier import coercion, escalation
from core.games import opening as opening_module
from core.games import transition as transition_module
from core.ingestion import stream
from core.models import panel as panel_module

_BASELINES: dict[str, dict[str, float]] = {}
_PACK: dict[str, dict[str, Any]] = {}
_PUBLISHED: str | None = None

_DEESCALATING = frozenset({"de-escalating", "deescalating"})


def cached_published() -> str | None:
    """The last successfully polled export stamp, or None."""
    return _PUBLISHED


def clear() -> None:
    """Drop the overlay (tests). Does not touch the snapshot corpus."""
    global _PUBLISHED
    _BASELINES.clear()
    _PACK.clear()
    _PUBLISHED = None


def snapshot_baselines(pack: str) -> dict[str, float]:
    """Each dyad's EWMA AFTER the snapshot's last event — the seed for live.

    Walks the warmed slim view one row at a time. If the corpus is not yet
    warmed this returns empty and Head B's first-event rule applies, which is
    worse than a 20-second warm on a request thread.
    """
    cached = _BASELINES.get(pack)
    if cached is not None:
        return cached
    from core.wire import serving

    if not serving._WARMED:  # noqa: SLF001 - the public API would call warm()
        _BASELINES[pack] = {}
        return _BASELINES[pack]
    last: dict[str, dict[str, Any]] = {}
    for row in serving.iter_rows_of(pack):
        did = row.get("dyad_id")
        if did:
            last[str(did)] = row
    out: dict[str, float] = {}
    for did, row in last.items():
        gold = row.get("goldstein")
        if gold is None:
            continue
        base = row.get("escalation_baseline")
        out[did] = escalation.update_baseline(
            None if base is None else float(base), float(gold),
        )
    _BASELINES[pack] = out
    return out


def score(
    rows: list[dict[str, Any]],
    *,
    baselines: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Fold live rows through Head B, seeded at the snapshot's EWMA.

    Pure: no network, no graph. The first-event rule still applies to a dyad
    the snapshot never saw.
    """
    tracker = escalation.DyadTracker()
    for did, base in (baselines or {}).items():
        tracker.seed(did, base)
    ordered = sorted(
        rows,
        key=lambda r: (str(r.get("event_time") or ""), str(r.get("node_id") or "")),
    )
    out: list[dict[str, Any]] = []
    for row in ordered:
        scored = dict(row)
        did = row.get("dyad_id")
        gold = row.get("goldstein")
        if did and gold is not None:
            result = tracker.observe(str(did), float(gold))
            scored.update(result)
            scored["direction"] = result["escalation_direction"]
            scored["magnitude"] = result["escalation_magnitude"]
        scored["coercion"] = coercion.counts_as_coercion(scored, allied=False)
        out.append(scored)
    return out


def refresh_pack(pack: Any) -> dict[str, Any]:
    """Poll GDELT 2.0 for one roster, score against the snapshot, cache."""
    global _PUBLISHED
    roster = {
        a["iso3"]: {"node_id": a["id"], "name": a["name"]}
        for a in pack.actors if a.get("iso3")
    }
    polled = stream.poll(pack, roster)
    published = str(polled.get("published") or "")
    if published:
        _PUBLISHED = published
    rows = score(list(polled.get("rows") or []), baselines=snapshot_baselines(pack.name))
    payload = {**polled, "rows": rows}
    _PACK[pack.name] = payload
    return payload


def refresh_all() -> str | None:
    """Poll the shared 15-minute file once and score every installed pack."""
    from core import packs as packs_module

    for name in packs_module.available():
        try:
            refresh_pack(packs_module.load(name))
        except Exception:  # noqa: BLE001 - a live feed failing is not a job failure
            continue
    return _PUBLISHED


def rows_for(region: str, dyad_id: str | None = None) -> list[dict[str, Any]]:
    """Scored live rows from the cache. Empty if nothing has been polled."""
    payload = _PACK.get(region)
    if not payload:
        return []
    rows = list(payload.get("rows") or [])
    if dyad_id is None:
        return rows
    return [r for r in rows if str(r.get("dyad_id") or "") == dyad_id]


def meta_for(region: str) -> dict[str, Any]:
    payload = _PACK.get(region) or {}
    return {
        "published": payload.get("published"),
        "fetched_at": payload.get("fetched_at"),
        "url": payload.get("url"),
    }


def apply_to_own(
    own: list[dict[str, Any]], live_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Overlay live events onto one dyad's quarterly series (a COPY).

    Intensity stays the quarter's largest escalating departure — the panel's
    own rule — so a live rupture can move the opening band without rewriting
    the snapshot table.
    """
    if not own or not live_rows:
        return [dict(r) for r in own]
    dyad_id = str(own[0]["dyad_id"])
    relevant = [r for r in live_rows if str(r.get("dyad_id") or "") == dyad_id]
    if not relevant:
        return [dict(r) for r in own]
    out = [dict(r) for r in own]
    by_q = {int(r["q"]): r for r in out}
    last_q = max(by_q)
    name = own[0]["dyad_name"]
    for row in relevant:
        when = str(row.get("event_time") or "")
        if not when:
            continue
        q = panel_module.quarter_index(when)
        if q not in by_q:
            if q < min(by_q):
                continue
            for hole in range(last_q + 1, q + 1):
                quiet = {
                    "dyad_id": dyad_id,
                    "dyad_name": name,
                    "q": hole,
                    "date": panel_module.quarter_label(hole),
                    "intensity": 0.0,
                    "signed_intensity": 0.0,
                    "events": 0,
                    "conflict": 0,
                    "tone": 0.0,
                }
                out.append(quiet)
                by_q[hole] = quiet
            last_q = q
        cell = by_q[q]
        n = int(cell["events"])
        gold = row.get("goldstein")
        if gold is not None:
            prior = float(cell["tone"] or 0.0)
            cell["tone"] = (prior * n + float(gold)) / (n + 1) if n else float(gold)
        cell["events"] = n + 1
        direction = row.get("escalation_direction") or row.get("direction")
        mag = row.get("escalation_magnitude")
        if mag is None:
            mag = row.get("magnitude")
        if direction == "escalating" and mag is not None:
            cell["intensity"] = max(float(cell["intensity"]), float(mag))
        if mag is not None and direction in ("escalating", *_DEESCALATING):
            signed = float(mag) if direction == "escalating" else -float(mag)
            if abs(signed) > abs(float(cell["signed_intensity"])):
                cell["signed_intensity"] = signed
        coercive = row.get("coercion")
        if coercive is None:
            coercive = (
                row.get("quad_class") == "material_conflict"
                and not row.get("co_participation")
            )
        if coercive:
            cell["conflict"] = int(cell["conflict"]) + 1
    out.sort(key=lambda r: int(r["q"]))
    return out


def joints(live_rows: list[dict[str, Any]], space: Any) -> dict[tuple[str, int], tuple[str, str]]:
    """Current-quarter joint actions from live events — the same proxy the
    archive uses (`transition.action_from_quads`). Events are not decisions.
    """
    shaped: list[dict[str, Any]] = []
    for row in live_rows:
        did = row.get("dyad_id")
        initiator = row.get("initiator_id")
        if not did or not initiator:
            continue
        actor_a, actor_b = opening_module.dyad_actors(str(did))
        shaped.append({
            "dyad_id": did,
            "event_time": row.get("event_time"),
            "initiator": initiator,
            "actor_a": actor_a,
            "actor_b": actor_b,
            "quad_class": row.get("quad_class"),
            "co_participation": row.get("co_participation"),
        })
    if not shaped:
        return {}
    return transition_module.joint_actions(
        shaped, quarter_of=panel_module.quarter_index, space=space,
    )
