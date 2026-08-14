"""Score the frozen forecast trail — calibration, mechanised (section 13).

  python scripts/score_forecasts.py            # every pack

Two modes, two evaluations, applied to every frozen Forecast node:

- NEAR-TERM calls whose horizon the archive has now OUTLIVED are
  Brier-scored: each further_escalation scenario resolves TRUE if its dyad
  had another escalating episode (a later quarter with an escalating event)
  inside the horizon — the same episode arithmetic the base rate was counted
  with — and reversion scenarios resolve as the complement. The score lands
  on the node's brier_score slot. A call whose horizon is still open is left
  unscored, visibly: an open question is not a zero.

- LONG-HORIZON calls are never Brier-scored (pressure over windows carries
  no dated point predictions). They carry a RETRODICTION instead: the
  structural method re-run as of ten years before the archive's edge, its
  flagged windows checked against the conflict intensity that actually
  followed — hit rate beside base rate, reported, never adjudicated.

Deterministic throughout; runs on boot after the freeze. Stop the API first
when running by hand: writing the scores needs the Kuzu write lock.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from core import packs
from core import settings as settings_module
from core.graph import kuzu_store
from core.reasoning import calibration, forecasting


def _episode_quarters(rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Per dyad, the sorted quarter indices holding an escalating event."""
    quarters: dict[str, set[int]] = {}
    for row in rows:
        if row["direction"] != "escalating":
            continue
        year, q = forecasting.quarter(str(row["event_time"]))
        quarters.setdefault(row["dyad_id"], set()).add(year * 4 + (q - 1))
    return {dyad: sorted(idx) for dyad, idx in quarters.items()}


def _near_term_outcomes(
    scenarios: list[dict[str, Any]],
    episodes: dict[str, list[int]],
    *,
    as_of: str,
    horizon_quarters: int,
) -> dict[str, bool]:
    """Resolve each scenario against what the archive then recorded."""
    year, q = forecasting.quarter(as_of)
    cutoff_index = year * 4 + (q - 1)
    outcomes: dict[str, bool] = {}
    for scenario in scenarios:
        name = str(scenario["scenario_name"])
        dyad_id = name.split(":", 1)[1] if ":" in name else ""
        escalated = any(
            0 < index - cutoff_index <= horizon_quarters
            for index in episodes.get(dyad_id, [])
        )
        if name.startswith("further_escalation"):
            outcomes[name] = escalated
        elif name.startswith("reversion_to_baseline"):
            outcomes[name] = not escalated
    return outcomes


def main() -> None:
    settings = settings_module.load()
    if not settings.kuzu_db_path.exists():
        sys.exit(f"no graph at {settings.kuzu_db_path} — seed first")

    # ── read phase: the archive's edge and every frozen call ─────────────────
    # Both stores, one read — the union the freeze reasoned from, so a
    # forecast is scored against the same archive that produced it.
    rows = forecasting.all_dyad_event_rows(settings.kuzu_db_path)
    conn = kuzu_store.connect(settings.kuzu_db_path, read_only=True)
    try:
        forecast_rows = kuzu_store.query(
            conn,
            "MATCH (f:Forecast) RETURN f.node_id AS node_id, f.mode AS mode, "
            "f.region_pack AS region_pack, f.question AS question, "
            "f.generated_at AS generated_at, f.horizon_end AS horizon_end, "
            "f.scenarios_json AS scenarios_json, "
            "f.frozen_inputs_json AS frozen_inputs_json, "
            "f.boundary_statement AS boundary_statement "
            "ORDER BY f.node_id",
        )
    finally:
        kuzu_store.close(conn)

    if not forecast_rows:
        print("no frozen forecasts — nothing to score")
        return

    latest = max(str(r["event_time"]) for r in rows) if rows else ""
    episodes = _episode_quarters(rows)

    # Retrodictions per region, ten years back from the archive's edge —
    # computed BEFORE the write lock is taken, because retrodict opens its
    # own read-only connections through the structural layer.
    retro_by_region: dict[str, str] = {}
    if latest:
        retro_as_of = f"{int(latest[:4]) - 10}-12-31"
        for name in packs.available():
            try:
                retro = calibration.retrodict(
                    settings.kuzu_db_path, as_of=retro_as_of, region_pack=name
                )
                retro_by_region[name] = json.dumps(retro)
            except Exception as exc:  # noqa: BLE001 - one region's failure is its own report
                print(f"{name}: retrodiction failed — {exc}")

    # ── score, then write FULL rows back (partial rows fail validation) ──────
    updates: list[dict[str, Any]] = []
    for row in forecast_rows:
        base = {k: (v if v is not None else "") for k, v in row.items()}
        scenarios = json.loads(str(row["scenarios_json"]) or "[]")
        frozen = json.loads(str(row["frozen_inputs_json"]) or "{}")
        if row["mode"] == "near_term":
            as_of = str(frozen.get("as_of") or "")
            horizon_end = str(row["horizon_end"] or "")
            if not as_of or not horizon_end or not latest or latest < horizon_end:
                print(
                    f"{row['node_id']}: horizon open through "
                    f"{horizon_end or '?'} — unscored"
                )
                continue
            horizon_quarters = max(int(horizon_end[:4]) - int(as_of[:4]), 1) * 4
            outcomes = _near_term_outcomes(
                scenarios, episodes, as_of=as_of, horizon_quarters=horizon_quarters
            )
            if not outcomes:
                continue
            base["brier_score"] = calibration.score_forecast(scenarios, outcomes)
            print(f"{row['node_id']}: brier {base['brier_score']:.4f}")
        else:
            attached = retro_by_region.get(str(row["region_pack"]))
            if attached is None:
                continue
            base["retrodiction_json"] = attached
            print(f"{row['node_id']}: retrodiction attached")
        updates.append(base)

    if not updates:
        print("nothing newly scoreable")
        return
    conn = kuzu_store.connect(settings.kuzu_db_path)
    try:
        kuzu_store.apply_schema(conn)
        kuzu_store.merge_nodes(conn, "Forecast", updates)
    finally:
        kuzu_store.close(conn)
    print(f"scored: {len(updates)} forecast nodes updated")


if __name__ == "__main__":
    main()
