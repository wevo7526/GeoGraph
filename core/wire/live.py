"""GDELT 2.0 overlay on a frozen snapshot.

The scored corpus, the counted kernel, the fitted payoffs and the measured
AFFECTED map are the engine's weights. This module scores the newest
15-minute export against those weights — Head B vs each pair's snapshot
EWMA — and NEVER writes a graph edge or an `event_study_runs` row.

`measured` on a live row is THIS event's session move, computed in process
from the price panel. Historical `market_outlook` cells are analogy from
the frozen map. The two must not be mixed: writing live CARs into the
panel would change `markets` and `games.pricing`.

Consumers (the live wire, a game's opening state, the relationship series)
read the overlay. The kernel, payoffs, CINC and the transmission map do not.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

from core.classifier import coercion, escalation
from core.games import family as family_module
from core.games import opening as opening_module
from core.games import transition as transition_module
from core.ingestion import stream
from core.models import panel as panel_module

_BASELINES: dict[str, dict[str, float]] = {}
_PACK: dict[str, dict[str, Any]] = {}
_PACK_AT: dict[str, float] = {}
_PUBLISHED: str | None = None

#: Loudest events per pack to measure against the panel this tick. The whole
#: 15-minute file is scored; only these get this-event CARs.
LIVE_MEASURE_CAP = 12
LIVE_WINDOWS = ("car_0_1",)
#: How long a scored pack is reused before the next poll. GDELT publishes
#: every 15 minutes; the wire job polls every 60s, so a request rarely
#: fetches the file itself.
ENSURE_MAX_AGE_S = 90.0

_DEESCALATING = frozenset({"de-escalating", "deescalating"})


def cached_published() -> str | None:
    """The last successfully polled export stamp, or None."""
    return _PUBLISHED


def clear() -> None:
    """Drop the overlay (tests). Does not touch the snapshot corpus."""
    global _PUBLISHED
    _BASELINES.clear()
    _PACK.clear()
    _PACK_AT.clear()
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
    ally_windows: dict[str, list[tuple[int, int]]] | None = None,
) -> list[dict[str, Any]]:
    """Fold live rows through Head B, seeded at the snapshot's EWMA.

    Pure: no network, no graph. The first-event rule still applies to a dyad
    the snapshot never saw. `ally_windows` is the same map the corpus uses,
    so a US–UK force coding is not scored as interstate coercion live.
    """
    tracker = escalation.DyadTracker()
    for did, base in (baselines or {}).items():
        tracker.seed(did, base)
    windows = ally_windows or {}
    ordered = sorted(
        rows,
        key=lambda r: (str(r.get("event_time") or ""), str(r.get("node_id") or "")),
    )
    out: list[dict[str, Any]] = []
    for row in ordered:
        scored = dict(row)
        dyad = row.get("dyad_id")
        gold = row.get("goldstein")
        if dyad and gold is not None:
            result = tracker.observe(str(dyad), float(gold))
            scored.update(result)
            scored["direction"] = result["escalation_direction"]
            scored["magnitude"] = result["escalation_magnitude"]
        year = family_module._year_of(row.get("event_time"), 0)
        allied = family_module.allied_in(windows.get(str(dyad or "")), year)
        scored["allied"] = allied
        scored["co_participation"] = family_module.is_co_participation(scored, windows)
        scored["coercion"] = coercion.counts_as_coercion(scored, allied=allied)
        out.append(scored)
    return out


def refresh_pack(pack: Any) -> dict[str, Any]:
    """Poll GDELT 2.0 for one roster, score against the snapshot, cache."""
    global _PUBLISHED
    previous = {
        str(row.get("node_id") or ""): row
        for row in ((_PACK.get(pack.name) or {}).get("rows") or [])
        if row.get("node_id")
    }
    roster = {
        a["iso3"]: {"node_id": a["id"], "name": a["name"]}
        for a in pack.actors if a.get("iso3")
    }
    polled = stream.poll(pack, roster)
    published = str(polled.get("published") or "")
    if published:
        _PUBLISHED = published
    rows = score(
        list(polled.get("rows") or []),
        baselines=snapshot_baselines(pack.name),
        ally_windows=family_module.ally_windows(pack)[0],
    )
    for row in rows:
        old = previous.get(str(row.get("node_id") or ""))
        if old and old.get("measured"):
            row["measured"] = old["measured"]
    payload = {**polled, "rows": rows}
    _PACK[pack.name] = payload
    _PACK_AT[pack.name] = time.time()
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


def ensure_pack(pack: Any, *, max_age_s: float = ENSURE_MAX_AGE_S) -> dict[str, Any]:
    """Return a scored pack, polling only when the cache is empty or stale.

    The wire job is the poller. A request thread reuses what it already
    has so a refreshing dashboard does not re-download the export — or
    re-measure the same twelve events — on every hit.
    """
    name = pack.name
    age = time.time() - _PACK_AT.get(name, 0.0)
    if name not in _PACK or age > max_age_s:
        refresh_pack(pack)
        attach_measured(pack)
    return _PACK.get(name) or {}


def row_by_id(node_id: str) -> dict[str, Any] | None:
    """One overlay row by event id, or None. Case/impact graph-miss fallback."""
    needle = str(node_id)
    for payload in _PACK.values():
        for row in payload.get("rows") or []:
            if str(row.get("node_id") or "") == needle:
                return row
    return None


def attach_measured(pack: Any) -> int:
    """Stamp this-event CARs onto the loudest overlay rows.

    In-process event-study arithmetic only. The numbers stay on the
    overlay cache. The frozen transmission map does not move.
    """
    payload = _PACK.get(pack.name)
    if not payload:
        return 0
    rows = list(payload.get("rows") or [])
    need: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        if row.get("measured"):
            continue
        if not row.get("node_id") or not row.get("event_time"):
            continue
        mag = row.get("escalation_magnitude")
        need.append((abs(float(mag)) if mag is not None else 0.0, row))
    need.sort(key=lambda item: -item[0])
    chosen = [row for _, row in need[:LIVE_MEASURE_CAP]]
    if not chosen or not getattr(pack, "markets", None):
        return 0

    from core import settings as settings_module
    from core.panel import pg_store
    from core.transmission import event_study

    try:
        panel = pg_store.connect(settings_module.load())
    except pg_store.PanelUnavailable:
        return 0

    stamped = 0
    try:
        names = {
            str(m["ticker"]): str(m.get("name") or m["ticker"]) for m in pack.markets
        }
        first = min(_event_date(row) for row in chosen)
        last = max(_event_date(row) for row in chosen)
        start = (first - dt.timedelta(days=400)).isoformat()
        end = (last + dt.timedelta(days=60)).isoformat()
        preloaded: dict[str, list[dict[str, Any]]] = {}
        intra: dict[str, list[dict[str, Any]]] = {}
        today = dt.date.today()
        intra_start = (today - dt.timedelta(days=70)).isoformat()
        intra_end = (today + dt.timedelta(days=2)).isoformat()
        for market in pack.markets:
            ticker = str(market["ticker"])
            try:
                preloaded[ticker] = pg_store.series(
                    panel, ticker, start=start, end=end, frequency="daily",
                )
            except Exception:  # noqa: BLE001 - a missing series is no print
                preloaded[ticker] = []
            try:
                intra[ticker] = pg_store.series_intraday(
                    panel, ticker, start=intra_start, end=intra_end,
                )
            except Exception:  # noqa: BLE001 - missing table is no prints
                intra[ticker] = []

        for row in chosen:
            event_date = _event_date(row)
            event_start = (event_date - dt.timedelta(days=400)).isoformat()
            event_end = (event_date + dt.timedelta(days=60)).isoformat()
            prices = {
                ticker: [
                    obs for obs in series
                    if event_start <= str(obs.get("obs_date") or "") <= event_end
                ]
                for ticker, series in preloaded.items()
            }
            try:
                effects, _skips = event_study.compute_effects(
                    {"node_id": row["node_id"], "event_time": row["event_time"]},
                    pack.markets,
                    prices=prices,
                    windows=LIVE_WINDOWS,
                    intraday=intra or None,
                )
            except Exception:  # noqa: BLE001 - one event failing is not a job failure
                continue
            if not effects:
                continue
            row["measured"] = [
                {
                    "ticker": e.market_ticker,
                    "market": names.get(e.market_ticker, e.market_ticker),
                    "window": e.window,
                    "resolution": e.resolution,
                    "abnormal_return": e.abnormal_return,
                    "raw_return": e.raw_return,
                    "expected_return": e.expected_return,
                    "t_stat": e.t_stat,
                    "p_value": e.p_value,
                    "first_mover": e.first_mover,
                    "overlapping": e.overlapping,
                    "method": e.method,
                }
                for e in effects
            ]
            stamped += 1
    finally:
        panel.close()
    return stamped


def _event_date(row: dict[str, Any]) -> dt.date:
    text = str(row.get("event_time") or "")[:10]
    return dt.date.fromisoformat(text)


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
