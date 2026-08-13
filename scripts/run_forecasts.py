"""Freeze the current forecasts into the graph (build-spec sections 13, 17).

  python scripts/run_forecasts.py            # both modes, region one

Computes the DETERMINISTIC payloads first (each opens its own read-only
connection), then takes the write lock once and merges Forecast nodes.
Forecasts are FROZEN AT GENERATION: the payloads carry the archive's own
data cutoff (`as_of`), generated_at is stamped here — the only clock in the
pipeline — and the node_id embeds mode+region+cutoff, so re-freezing the
same archive state merges onto itself while new data makes a NEW node. That
history is what lets calibration score or retrodict a past call honestly.

Stop the API first: writing Forecast nodes needs the Kuzu write lock.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from core import settings as settings_module
from core.graph import kuzu_store
from core.reasoning import forecasting, structural

_NEAR_QUESTION = "Which focal dyads escalate again within the horizon?"
_LONG_QUESTION = "Where does systemic pressure run over the coming decades?"
_MODEL_QUESTION = "How hot does each dyad run over the next four quarters?"

#: How many dyads the model forecast covers, by how much the archive has
#: watched them. A trajectory for a dyad with eight quarters of history is a
#: number the reader cannot weigh.
_MODEL_DYADS = 12


def _model_forecast(
    db_path: Path, *, region_pack: str, generated_at: str
) -> dict[str, str] | None:
    """The learned trajectory, frozen as a THIRD mode — or None.

    Returns None, loudly on stdout, whenever the model should not speak: no
    artifact, an artifact whose feature contract no longer matches the code,
    or one whose walk-forward gate FAILED. A model that could not beat
    persistence must not appear beside two counted forecasts wearing the same
    typography (build-spec section 17).
    """
    from core.models import features as feature_module
    from core.models import intensity, panel, registry

    try:
        artifact = registry.load("intensity")
    except registry.ArtifactError as exc:
        print(f"{region_pack}: model not frozen — {exc}")
        return None
    if not artifact.get("gate_passed"):
        print(f"{region_pack}: model not frozen — gate failed: {artifact.get('gate_reason')}")
        return None

    table = panel.build(panel.load_rows(db_path), region_pack=region_pack)
    if not table:
        print(f"{region_pack}: model not frozen — no dyad has enough occupied quarters")
        return None
    feature_rows = feature_module.build(table)
    weights = registry.weights_of(artifact)
    mean, sd = registry.scaler_of(artifact)
    residual_sd = registry.residual_sd_of(artifact)

    trajectories = []
    for summary in panel.dyad_summary(table)[:_MODEL_DYADS]:
        path = intensity.forecast_trajectory(
            feature_rows, weights, dyad_id=summary["dyad_id"],
            mean=mean, sd=sd, residual_sd=residual_sd,
        )
        if path:
            trajectories.append({
                "dyad_id": summary["dyad_id"],
                "dyad_name": summary["dyad_name"],
                "active_quarters": summary["active_quarters"],
                "last_observed": summary["last"],
                "path": path,
            })
    if not trajectories:
        print(f"{region_pack}: model not frozen — no dyad produced a trajectory")
        return None

    as_of = max(row["date"] for row in table)
    return {
        "node_id": f"forecast:model:{region_pack}:{as_of}",
        "region_pack": region_pack,
        "mode": "model",
        "question": _MODEL_QUESTION,
        "generated_at": generated_at,
        "horizon_end": trajectories[0]["path"][-1]["date"],
        "scenarios_json": json.dumps([]),
        "frozen_inputs_json": json.dumps({
            "as_of": as_of,
            "trajectories": trajectories,
            # The artifact's identity travels with every call it made, so a
            # prediction can be traced to the exact weights that produced it.
            "model": {
                "name": artifact["name"],
                "hash": artifact["hash"],
                "target": artifact["target"],
                "features": artifact["features"],
                "train_span": artifact["train_span"],
                "gate_reason": artifact["gate_reason"],
            },
            "walk_forward": artifact["walk_forward"],
            "method": (
                "ridge on within-dyad deviations (intercept, current deviation, "
                "running baseline), fitted offline and frozen here; ordering is "
                "persistence's, magnitude is the fitted reversion; band is the "
                "held-out residual spread at each horizon"
            ),
        }),
        "boundary_statement": (
            "A learned forecast. It orders a dyad's quarters no better than "
            "persistence does — that ordering is the signal — and its "
            "contribution is the magnitude of the reversion toward baseline. "
            "Every number beside it carries the walk-forward score it earned."
        ),
    }


def freeze(db_path: Path, *, region_pack: str) -> list[dict[str, str]]:
    """Compute both modes, then persist. Returns the written node summaries."""
    near = forecasting.forecast(db_path, _NEAR_QUESTION, region_pack=region_pack)
    long_horizon = structural.structural_forecast(db_path, region_pack=region_pack)

    cutoff = near["as_of"]
    generated_at = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    cutoff_year = int(cutoff[:4])

    rows = [
        {
            "node_id": f"forecast:near-term:{region_pack}:{cutoff}",
            "region_pack": region_pack,
            "mode": "near_term",
            "question": _NEAR_QUESTION,
            "generated_at": generated_at,
            "horizon_end": f"{cutoff_year + near['horizon_years']}-12-31",
            "scenarios_json": json.dumps(near["scenarios"]),
            "frozen_inputs_json": json.dumps(
                {**near["frozen_inputs"], "method": near["method"]}
            ),
            "boundary_statement": "",
        },
        {
            "node_id": f"forecast:long-horizon:{region_pack}:{cutoff}",
            "region_pack": region_pack,
            "mode": "long_horizon",
            "question": _LONG_QUESTION,
            "generated_at": generated_at,
            "horizon_end": f"{cutoff_year + long_horizon['horizon_years']}-12-31",
            "scenarios_json": json.dumps(long_horizon["scenarios"]),
            # The trajectory IS part of the frozen inputs: pressure by year,
            # the flagged windows and components — everything a reader (or a
            # later retrodiction) needs to recompute the call.
            "frozen_inputs_json": json.dumps({
                "as_of": cutoff,
                "pressure": long_horizon["pressure"],
                "windows": long_horizon["windows"],
                "components": long_horizon["components"],
                "method": long_horizon["method"],
            }),
            "boundary_statement": long_horizon["boundary_statement"],
        },
    ]

    # The learned mode is optional by design: the two counted forecasts above
    # do not depend on a model existing, passing, or being retrained.
    model_row = _model_forecast(
        db_path, region_pack=region_pack, generated_at=generated_at
    )
    if model_row is not None:
        rows.append(model_row)

    conn = kuzu_store.connect(db_path)
    try:
        kuzu_store.merge_nodes(conn, "Forecast", rows)
    finally:
        kuzu_store.close(conn)
    return [{"node_id": r["node_id"], "mode": r["mode"]} for r in rows]


def main() -> None:
    settings = settings_module.load()
    if not settings.kuzu_db_path.exists():
        sys.exit(f"no graph at {settings.kuzu_db_path} — seed first")
    from core import packs

    for name in packs.available():
        try:
            for row in freeze(settings.kuzu_db_path, region_pack=name):
                print(f"{row['mode']}: {row['node_id']}")
        except ValueError as exc:
            print(f"{name}: not frozen — {exc}")


if __name__ == "__main__":
    main()
