"""Fit the game's payoff parameters and write their artifact — OFFLINE.

  python scripts/fit_game.py                     # every installed region
  python scripts/fit_game.py --region mena       # one
  python scripts/fit_game.py --dry-run           # report, write nothing

SAME REASON THE MODEL TRAINS OFFLINE. Indirect inference solves the
equilibrium and simulates a panel once per objective evaluation — a hundred
and twenty of them per region, which is minutes. The boot's forecast step has
five minutes for EVERY region and every mode, and a wall-clock timeout there
does not lose the sequence forecast alone, it loses the two counted forecasts
with it. Structural payoffs are also not a thing that should change per
deploy: they are fitted to the archive's own history, so refitting them every
boot would make each container's numbers a slightly different model for no
gain.

So this writes models/game-<region>.json and the boot reads it, exactly as
models/intensity.json works. A region with no artifact simply gets no
sequence forecast, and says so.

Read-only against the graph; takes no write lock.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import packs  # noqa: E402
from core import settings as settings_module  # noqa: E402
from core.games import estimate, transition  # noqa: E402
from core.games import state as state_module  # noqa: E402
from core.graph import kuzu_store  # noqa: E402
from core.models import panel as panel_module  # noqa: E402
from core.models import registry  # noqa: E402
from core.wire import corpus  # noqa: E402

#: A kernel under this share of measured cells cannot carry an equilibrium
#: worth fitting — the same bar the freeze applies before it will speak.
MIN_KERNEL_COVERAGE = 0.5


def fit_region(db_path: Path, region: str, *, evaluations: int) -> dict[str, Any] | None:
    # THE CORPUS FIRST, THE GRAPH AS FALLBACK. Fitting is offline and its
    # output is committed, so it must be a pure function of the repository —
    # the artifacts are in git and the parser, the crosswalks and Head B are
    # all deterministic, which makes the fit reproducible by anyone holding
    # this commit. Reading the graph instead made it a function of whatever
    # happened to be loaded on the machine, and loading it first cost ~45
    # minutes per pack against 5 seconds here.
    #
    # The graph path is kept for a lens with no shipped artifact, where the
    # graph genuinely is the only source.
    if corpus.artifacts_for(region):
        panel_view, events = corpus.views(region)
        table = panel_module.build(panel_view, region_pack=region)
    else:
        conn = kuzu_store.connect(db_path, read_only=True)
        try:
            table = panel_module.build(
                panel_module.dyad_event_rows(conn), region_pack=region
            )
            events = transition.event_rows(conn)
        finally:
            kuzu_store.close(conn)

    if not table:
        print(f"{region}: no modelable dyad")
        return None

    joint = transition.joint_actions(events, quarter_of=panel_module.quarter_index)
    kernel, observed = transition.kernel(transition.count(table, joint))
    coverage = transition.coverage(observed)
    print(f"{region}: panel {len(table):,} rows, kernel "
          f"{coverage['share_measured']:.0%} measured "
          f"({coverage['observations']:,} transitions)")
    if coverage["share_measured"] < MIN_KERNEL_COVERAGE:
        print(f"{region}: kernel too sparse to fit — skipping")
        return None

    frequencies = estimate.observed_frequencies(table, joint)
    result = estimate.fit(kernel, frequencies, max_evaluations=evaluations)
    print(f"{region}: distance {result['distance']} "
          f"({result['evaluations']} evaluations, converged={result['converged']})")
    print(f"{region}: payoffs {json.dumps(result['payoffs'])}")

    # The BOUNDARY CAVEAT, computed rather than remembered. A parameter that
    # settles on its own clip bound means the optimiser wanted to go further
    # and the game cannot reproduce the archive's action mix — the value is a
    # direction, not an estimate, and anything reading this artifact should
    # be able to see that without re-deriving it.
    bounds = {
        "discount": (0.5, 0.99),
        "cost_resolute": (0.05, 3.0),
        "cost_irresolute": (0.05, 6.0),
        "stake": (0.1, 3.0),
        "audience": (0.0, 2.0),
    }
    at_bound = [
        name for name, (low, high) in bounds.items()
        if abs(result["payoffs"][name] - low) < 1e-6
        or abs(result["payoffs"][name] - high) < 1e-6
    ]
    if at_bound:
        print(f"{region}: AT BOUNDS {at_bound} — payoff magnitudes are a "
              "direction, not an estimate")

    return {
        "region": region,
        "payoffs": result["payoffs"],
        "distance": result["distance"],
        "converged": result["converged"],
        "evaluations": result["evaluations"],
        "seed": result["seed"],
        "at_bounds": at_bound,
        "kernel": coverage,
        "panel_rows": len(table),
        "bands": list(state_module.INTENSITY_EDGES),
        "identification": result["identification"],
        "method": result["method"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None)
    parser.add_argument("--region", default=None, help="default: every installed pack")
    parser.add_argument("--evaluations", type=int, default=150)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else settings_module.load().kuzu_db_path
    # A graph is only required for a lens with NO shipped corpus. Demanding one
    # unconditionally would put a 45-minute load in front of a fit that reads
    # the artifacts in five seconds — and would make a committed artifact
    # depend on the state of one machine's volume.
    if not corpus.installed() and not Path(db_path).exists():
        print(f"no corpus artifacts and no graph at {db_path}")
        return 1

    regions = [args.region] if args.region else packs.available()
    written = 0
    for region in regions:
        try:
            fitted = fit_region(Path(db_path), region, evaluations=args.evaluations)
        except Exception as exc:  # noqa: BLE001 - one region must not stop the rest
            print(f"{region}: fit failed — {exc}")
            continue
        if fitted is None or args.dry_run:
            continue
        target = registry.MODELS_DIR / f"game-{region}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        # Stamp the content hash so a hand edit or truncated write is caught
        # on load, matching the intensity model's integrity guarantee.
        fitted["hash"] = registry.content_hash(fitted)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(fitted, fh, indent=2)
            fh.write("\n")
        print(f"{region}: wrote {target}")
        written += 1
    if args.dry_run:
        print("\n--dry-run: nothing written")
    return 0 if (written or args.dry_run) else 1


if __name__ == "__main__":
    raise SystemExit(main())
