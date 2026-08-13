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
from typing import Any

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


_SEQUENCE_QUESTION = "Which sequences does the equilibrium put weight on, and what did they price?"

#: Dyads the sequence forecast covers, by how much the archive has watched
#: them. A path for a dyad with eight quarters of history is a number the
#: reader cannot weigh.
_SEQUENCE_DYADS = 6


def _load_game_artifact(region_pack: str) -> dict[str, Any] | None:
    """The region's fitted payoffs, or None with the reason on stdout.

    Absent is the NORMAL state for a region whose kernel is too sparse to fit
    — scripts/fit_game.py refuses those — so this is a skip, not an error.
    """
    from core.models import registry

    target = registry.MODELS_DIR / f"game-{region_pack}.json"
    if not target.exists():
        print(f"{region_pack}: sequence not frozen — no {target.name} "
              "(run scripts/fit_game.py)")
        return None
    with open(target, encoding="utf-8") as fh:
        artifact: dict[str, Any] = json.load(fh)
    return artifact


def _sequence_forecast(
    db_path: Path, *, region_pack: str, generated_at: str
) -> dict[str, str] | None:
    """The solved mode — or None, loudly, when it should not speak.

    Returns None when the kernel is too sparsely measured to carry an
    equilibrium. A game solved over mostly-fallback transitions is a game
    about the fallback, and shipping it beside three grounded forecasts in
    the same typography would be the overclaim docs/game-spec.md section 7
    exists to prevent.
    """
    from core.games import duration, paths, pricing, solve, state, transition
    from core.models import panel as panel_module

    conn = kuzu_store.connect(db_path, read_only=True)
    try:
        table = panel_module.build(
            panel_module.dyad_event_rows(conn), region_pack=region_pack
        )
        events = transition.event_rows(conn)
        effects = pricing.measured_effects(conn, region_pack=region_pack)
    finally:
        kuzu_store.close(conn)

    if not table:
        print(f"{region_pack}: sequence not frozen — no modelable dyad")
        return None

    joint = transition.joint_actions(events, quarter_of=panel_module.quarter_index)
    kernel, observed = transition.kernel(transition.count(table, joint))
    coverage = transition.coverage(observed)
    if coverage["share_measured"] < 0.5:
        print(f"{region_pack}: sequence not frozen — kernel only "
              f"{coverage['share_measured']:.0%} measured")
        return None

    # PAYOFFS ARE READ, NOT FITTED HERE. Indirect inference is a hundred and
    # twenty solve-and-simulate cycles per region; this step has five minutes
    # for every region and every mode, and a wall-clock timeout would lose the
    # two COUNTED forecasts along with this one. Same split as the learned
    # model: fit offline (scripts/fit_game.py), freeze from the artifact.
    fit = _load_game_artifact(region_pack)
    if fit is None:
        return None
    payoffs = solve.Payoffs(
        discount=fit["payoffs"]["discount"],
        cost_resolute=fit["payoffs"]["cost_resolute"],
        cost_irresolute=fit["payoffs"]["cost_irresolute"],
        stake=fit["payoffs"]["stake"],
        audience=fit["payoffs"]["audience"],
    )
    equilibrium = solve.solve(kernel, payoffs, horizon=4)

    as_of = max(row["date"] for row in table)
    summaries = panel_module.dyad_summary(table)[:_SEQUENCE_DYADS]
    frozen = []
    for summary in summaries:
        own = [r for r in table if r["dyad_id"] == summary["dyad_id"]]
        if not own:
            continue
        scale = state.dyad_scale([float(r["intensity"]) for r in own])
        latest = max(own, key=lambda r: r["q"])
        band = state.intensity_band(float(latest["intensity"]), scale)
        result = paths.enumerate_paths(
            equilibrium, kernel, intensity=band, capability=1,
            belief_a=0.5, belief_b=0.5, payoffs=payoffs,
        )
        priced = pricing.price_paths(result, effects, as_of=as_of, scale=scale or 1.0)
        frozen.append({
            "dyad_id": summary["dyad_id"],
            "dyad_name": summary["dyad_name"],
            "active_quarters": summary["active_quarters"],
            "opening_band": band,
            "marginal": paths.marginal_intensity(result, 4),
            **priced,
        })

    if not frozen:
        print(f"{region_pack}: sequence not frozen — no dyad produced a path")
        return None

    return {
        "node_id": f"forecast:sequence:{region_pack}:{as_of}",
        "region_pack": region_pack,
        "mode": "sequence",
        "question": _SEQUENCE_QUESTION,
        "generated_at": generated_at,
        "horizon_end": as_of[:4] + "-12-31",
        "scenarios_json": json.dumps([]),
        "frozen_inputs_json": json.dumps({
            "as_of": as_of,
            "dyads": frozen,
            "equilibrium": {
                "concept": equilibrium["concept"],
                "payoffs": fit["payoffs"],
                "distance": fit["distance"],
                "converged": fit["converged"],
                "seed": fit["seed"],
                "identification": fit["identification"],
                "at_bounds": fit.get("at_bounds", []),
                "fitted_on": fit.get("panel_rows"),
                "method": fit["method"],
            },
            # The kernel's coverage travels with every path built on it.
            "kernel": coverage,
            # What the yield curve says about how LONG these dyads' crises
            # last — the second moment §2.2 wants for identification. Reports
            # its own absence until FRED yields reach the panel, and carries
            # the uncalibrated-mapping caveat either way.
            "duration": duration.report(
                effects,
                {
                    str(e["event_id"]): str(e.get("dyad_id", ""))
                    for e in effects if e.get("event_id")
                },
            ),
            "bands": list(state.INTENSITY_EDGES),
        }),
        "boundary_statement": (
            "A SOLVED forecast, not a counted or fitted one: a distribution "
            "over sequences from the equilibrium of a bargaining game whose "
            "payoffs were fitted to observed action frequencies. Read the "
            "paths as a distribution — the retained probability says how much "
            "of it is shown — and every price beside them is a measured "
            "abnormal return from comparable past events, never a model's."
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

    # The solved mode, likewise optional: three grounded forecasts do not
    # depend on a game existing, converging, or having a measured kernel.
    try:
        sequence_row = _sequence_forecast(
            db_path, region_pack=region_pack, generated_at=generated_at
        )
    except Exception as exc:  # noqa: BLE001 - a failed solve must not lose the rest
        print(f"{region_pack}: sequence not frozen — {exc}")
        sequence_row = None
    if sequence_row is not None:
        rows.append(sequence_row)

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
