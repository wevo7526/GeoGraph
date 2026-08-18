"""Score stored event → market cells. Read-only: writes nothing.

  python scripts/score_transmission.py            # every pack
  python scripts/score_transmission.py mena       # one pack

Streams `event_study_runs` ticker by ticker and walks them leave-one-out.
Does not rebuild a markets story, does not freeze a forecast, does not
re-run the event study. Stop the API first only if you need the graph lock
for `coding_for`; the panel itself is concurrent.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from core import packs
from core import settings as settings_module
from core.graph import kuzu_store
from core.panel import pg_store
from core.reasoning import transmission_skill


def run(pack_name: str) -> dict[str, Any]:
    settings = settings_module.load()
    conn = kuzu_store.connect(settings.kuzu_db_path, read_only=True)
    panel = pg_store.connect(settings)
    try:
        observations = list(
            transmission_skill.observations_from_panel(conn, panel, pack_name)
        )
    finally:
        kuzu_store.close(conn)
        panel.close()

    clean = transmission_skill.walk(observations, clean=True)
    dirty = transmission_skill.walk(observations, clean=False, p_gate=None)
    gate = transmission_skill.remeasure_justified(clean)
    return {
        "region": pack_name,
        "observations": len(observations),
        "clean": transmission_skill.compact_skill(clean),
        "all": transmission_skill.compact_skill(dirty),
        "remeasure": gate,
        "oracle_vs_game": {
            "oracle": (clean.get("matchers") or {}).get("quad_band"),
            "note": (
                "game_band needs persisted first-steps keyed by event; this "
                "script reports the oracle-class bound only"
            ),
        },
    }


def main() -> None:
    names = sys.argv[1:] or list(packs.available())
    for name in names:
        result = run(name)
        print(json.dumps(result, indent=2, default=str))
        skill = result["clean"]["kind"]
        print(
            f"{name}: clean kind n={skill['n']} coverage={skill['coverage']} "
            f"sign_hit={skill['sign_hit']} beats_naive={skill['beats_naive']} "
            f"remeasure={result['remeasure']['justified']}"
        )


if __name__ == "__main__":
    main()
