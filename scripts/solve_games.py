"""Solve every region's scenario map and persist it (core/games/scenarios.py).

    python scripts/solve_games.py            # every installed pack
    python scripts/solve_games.py mena       # one region
    python scripts/solve_games.py --dyads 12 # fewer dyads per region

Reads the corpus (the same table the payoffs were fitted on), the graph
READ-ONLY (CINC estimates for the opening capability, the frozen model mode
for the kernel tilt, AFFECTED for the market map) and the committed game
artifact; writes Postgres `game_solutions`. It never takes the graph's write
lock, so it can run beside the API — the boot runs it as a child
(GEOGRAPH_GAMES_ON_BOOT) because that is where the graph is open.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import packs  # noqa: E402
from core import settings as settings_module  # noqa: E402
from core.games import context as context_module  # noqa: E402
from core.games import scenarios  # noqa: E402
from core.games import solve as solve_module  # noqa: E402
from core.graph import kuzu_store  # noqa: E402
from core.panel import pg_store  # noqa: E402


def solve_region(region: str, *, conn: Any, dyads: int) -> dict[str, Any]:
    context = context_module.build(conn, region)
    payoffs = solve_module.Payoffs(**context_module.fitted_payoffs(region))
    dyad_ids = context_module.active_dyads(context, dyads)
    return scenarios.region_map(
        context, region=region, payoffs=payoffs, graph_conn=conn, dyad_ids=dyad_ids,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("regions", nargs="*", help="pack names; default = all installed")
    parser.add_argument("--dyads", type=int, default=scenarios.REGION_DYADS)
    parser.add_argument("--dry-run", action="store_true", help="solve, print, write nothing")
    args = parser.parse_args()

    settings = settings_module.load()
    names = args.regions or packs.available()

    conn = None
    if settings.kuzu_db_path.exists():
        try:
            conn = kuzu_store.connect(settings.kuzu_db_path, read_only=True)
        except Exception as exc:  # noqa: BLE001 - solve without the graph, say so
            print(f"graph not opened read-only ({exc}); solving without CINC/tilt/effects")
    else:
        print(f"no graph at {settings.kuzu_db_path}; solving without CINC/tilt/effects")

    panel = None
    if not args.dry_run:
        panel = pg_store.connect(settings)
        pg_store.apply_schema(panel)

    try:
        for region in names:
            started = time.monotonic()
            try:
                solved = solve_region(region, conn=conn, dyads=args.dyads)
            except (context_module.GraphNeeded, context_module.NothingToSolve) as exc:
                print(f"{region}: not solved — {exc}")
                continue
            aggregate = solved["region"]
            lead = aggregate["ranking"][0] if aggregate["ranking"] else None
            print(
                f"{region}: {aggregate['dyads_solved']} dyads solved in "
                f"{time.monotonic() - started:.1f}s; kernel "
                f"{aggregate['kernel']['share_measured']:.0%} measured; "
                f"nash_gap mean {aggregate['nash_gap']['mean']}; "
                + (
                    f"lead {lead['dyad_name']} P(esc)={lead['escalation_probability']:.2f}"
                    if lead else "no ranking"
                )
            )
            if panel is not None:
                written = pg_store.record_game_solutions(
                    panel, region, solved, solver=aggregate["primary_solver"]
                )
                print(f"{region}: {written} rows persisted")
    finally:
        if panel is not None:
            panel.close()
        if conn is not None:
            kuzu_store.close(conn)


if __name__ == "__main__":
    main()
