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
