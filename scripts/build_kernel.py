"""Build the game's transition kernel from the archive and report its coverage.

  python scripts/build_kernel.py --region mena
  python scripts/build_kernel.py --region mena --solve

The kernel is the one part of docs/game-spec.md that is MEASURED rather than
solved: P(next intensity | intensity, action A, action B), counted from
dyad-quarters. Everything else in core/games is arithmetic over parameters,
and this is the empirical fact those parameters have to explain.

WHAT THIS PRINTS IS MOSTLY THE COVERAGE, on purpose. A kernel is 54 cells of
nine joint actions over six intensity bands, and a game solved over one that
is four-fifths fallback is a game about the fallback. The share measured is
the number that decides whether the equilibrium above it means anything, so
it is reported before any of the equilibrium is.

Read-only. Takes no write lock and can run beside a live API.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import settings as settings_module  # noqa: E402
from core.games import paths as paths_module  # noqa: E402
from core.games import solve as solve_module  # noqa: E402
from core.games import state as state_module  # noqa: E402
from core.games import transition  # noqa: E402
from core.graph import kuzu_store  # noqa: E402
from core.models import panel as panel_module  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None)
    parser.add_argument("--region", default="mena")
    parser.add_argument("--solve", action="store_true", help="also solve and show a path")
    parser.add_argument("--horizon", type=int, default=4)
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else settings_module.load().kuzu_db_path
    if not Path(db_path).exists():
        print(f"no graph at {db_path}")
        return 1

    conn = kuzu_store.connect(Path(db_path), read_only=True)
    try:
        panel_rows = panel_module.build(
            panel_module.dyad_event_rows(conn), region_pack=args.region
        )
        event_rows = transition.event_rows(conn)
    finally:
        kuzu_store.close(conn)

    if not panel_rows:
        print(f"no panel for {args.region} — nothing to count")
        return 1

    actions = transition.joint_actions(
        event_rows, quarter_of=panel_module.quarter_index
    )
    counts = transition.count(panel_rows, actions)
    kernel, observed = transition.kernel(counts)
    report = transition.coverage(observed)

    dyads = {r["dyad_id"] for r in panel_rows}
    print(f"panel: {len(panel_rows):,} dyad-quarters over {len(dyads)} dyads")
    print(f"joint actions read: {len(actions):,} dyad-quarters\n")
    print(f"kernel coverage: {report['measured']}/{report['cells']} cells measured "
          f"({report['share_measured']:.1%}), {report['observations']:,} transitions")
    if report["share_measured"] < 0.5:
        print("  NOTE: most cells are pooled fallback — the equilibrium below is "
              "largely about the fallback, not about measured play")

    print("\nobservations per (intensity band, action A, action B):")
    print(f"{'band':>5} " + "".join(f"{a[:4]:>10}" for a in state_module.ACTIONS))
    for x in range(len(state_module.INTENSITY_EDGES)):
        for a1, name in enumerate(state_module.ACTIONS):
            row = "".join(f"{int(observed[x, a1, a2]):>10,}"
                          for a2 in range(len(state_module.ACTIONS)))
            print(f"{x if a1 == 0 else '':>5} {name[:4]:<5}{row}")

    print("\nwhere escalation LEADS (measured cells only):")
    for x in range(len(state_module.INTENSITY_EDGES)):
        esc = state_module.ACTIONS.index("escalate")
        if observed[x, esc, esc] >= transition.MIN_CELL_OBSERVATIONS:
            row = kernel[x, esc, esc]
            print(f"  from band {x}: expected next band "
                  f"{float(row @ np.arange(len(row))):.2f}  "
                  f"(modal {int(np.argmax(row))})")

    if args.solve:
        payoffs = solve_module.Payoffs()
        equilibrium = solve_module.solve(kernel, payoffs, horizon=args.horizon)
        print(f"\n{equilibrium['concept']}")
        escalate = state_module.ACTIONS.index("escalate")
        print("P(escalate) by intensity band, resolute vs irresolute:")
        for x in range(len(state_module.INTENSITY_EDGES)):
            irr = equilibrium["policy"][0, x, 1, 0][escalate]
            res = equilibrium["policy"][0, x, 1, 1][escalate]
            print(f"  band {x}: irresolute {irr:.3f}   resolute {res:.3f}")

        result = paths_module.enumerate_paths(
            equilibrium, kernel, intensity=2, capability=1,
            belief_a=0.5, belief_b=0.5, payoffs=payoffs,
        )
        print(f"\npaths from band 2: {len(result['paths'])} kept of "
              f"{result['paths_enumerated']}, retaining "
              f"{result['retained_probability']:.1%} of the mass")
        for path in result["paths"][:4]:
            steps = " -> ".join(
                f"{s['action_a'][:4]}/{s['action_b'][:4]}@b{s['intensity_band']}"
                for s in path["steps"]
            )
            print(f"  p={path['probability']:.3f}  {steps}")
        print("\nmarginal intensity by period:")
        for period in paths_module.marginal_intensity(result, args.horizon):
            print(f"  +{period['period']}q  modal band {period['modal_band']}  "
                  f"expected {period['expected_band']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
