"""Fit the intensity forecaster and write its artifact — OFFLINE.

  python scripts/train_forecaster.py                    # fit, gate, write
  python scripts/train_forecaster.py --dry-run          # report, write nothing
  python scripts/train_forecaster.py --ablate           # with/without dyad character

Training never happens in the container: Railway is CPU-only, the boot is
already long, and a model refitted at boot would make every deploy's numbers
a slightly different model. This writes models/intensity.json; the boot loads
it and does a forward pass in milliseconds.

THE GATE IS ENFORCED HERE. If the model does not order a dyad's own quarters
better than persistence, walk-forward, the artifact is still written — with
`gate_passed: false` — and the boot will refuse to freeze forecasts from it.
Recording a failure is the point: it is the difference between a model that
was checked and one that was merely trained.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import settings as settings_module  # noqa: E402
from core.models import features as feature_module  # noqa: E402
from core.models import intensity, panel, registry  # noqa: E402
from core.wire import corpus  # noqa: E402

#: Walk-forward cuts. Spread across the wire era so a single unusual stretch
#: cannot carry the verdict.
DEFAULT_CUTS = [1990, 1994, 1998, 2002]


def residual_spread(
    feature_rows: list[dict[str, Any]], folds: list[dict[str, Any]]
) -> dict[int, float]:
    """Per-horizon residual sd, taken from the HELD-OUT folds.

    The band on a forecast has to come from how wrong the model was on data
    it had not seen. Fitting the spread in-sample would draw a confident
    interval around exactly the predictions that were memorised.
    """
    columns = feature_module.shipped_columns()
    spread: dict[int, float] = {}
    for horizon in feature_module.HORIZONS:
        errors: list[float] = []
        for fold in folds:
            if fold["horizon"] != horizon:
                continue
            x, y, quarters, _ = feature_module.matrices(
                feature_rows, horizon, target="deviation", columns=columns
            )
            train = quarters < fold["cut"]
            test = (quarters >= fold["cut"]) & (quarters < fold["cut"] + 20)
            if train.sum() < 100 or test.sum() < 50:
                continue
            mean, sd = intensity.scaler(x[train])
            weights = intensity.fit(intensity.standardize(x[train], mean, sd), y[train])
            predicted = intensity.predict(
                intensity.standardize(x[test], mean, sd), weights, target="deviation"
            )
            errors.extend((predicted - y[test]).tolist())
        spread[horizon] = float(np.std(errors)) if errors else 0.0
    return spread


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="graph path (default: settings)")
    parser.add_argument("--name", default="intensity", help="artifact name")
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    parser.add_argument("--ablate", action="store_true", help="score without dyad character")
    parser.add_argument("--cuts", type=int, nargs="+", default=DEFAULT_CUTS)
    parser.add_argument(
        "--target", default="deviation", choices=feature_module.TARGETS,
        help=(
            "deviation = the coming window's level less the dyad's baseline (default, "
            "and the only one that passes the gate); level = absolute; point = quarter q+h"
        ),
    )
    args = parser.parse_args()

    # THE CORPUS FIRST, for the same reason the game fitter prefers it: this
    # writes a committed artifact, so the fit has to be a pure function of the
    # repository rather than of whatever happens to be loaded on this machine.
    # The artifacts are in git and every step from them to a feature row is
    # deterministic, so the hash in models/intensity.json is reproducible.
    if corpus.installed():
        rows = corpus.all_panel_rows()
    else:
        db_path = Path(args.db) if args.db else settings_module.load().kuzu_db_path
        if not Path(db_path).exists():
            print(f"no corpus artifacts and no graph at {db_path} — "
                  "seed and load one first")
            return 1
        rows = panel.load_rows(Path(db_path))
    table = panel.build(rows)
    if not table:
        print("the panel is empty — no dyad has enough occupied quarters")
        return 1
    feature_rows = feature_module.build(table)
    dyads = {r["dyad_id"] for r in feature_rows}
    span = (min(r["date"] for r in table), max(r["date"] for r in table))

    print(f"panel: {len(table):,} dyad-quarters over {len(dyads)} dyads  "
          f"({span[0][:7]} .. {span[1][:7]})")
    print(f"feature rows: {len(feature_rows):,}\n")

    # Both targets are scored, always. Which one to ship is a measurement, and
    # reporting only the winner would make it look like a choice made in
    # advance rather than one the folds decided.
    shipped = feature_module.shipped_columns()
    scored: dict[str, list[dict[str, Any]]] = {}
    for target in feature_module.TARGETS:
        folds_ = intensity.walk_forward(
            feature_rows, cut_years=args.cuts, target=target, columns=shipped
        )
        scored[target] = folds_
        ok, why = intensity.passes_gate(folds_)
        print(f"target={target:<10} {'PASS' if ok else 'FAIL'} — {why}")

    print()
    folds = scored[args.target]
    print(f"target = {args.target}")
    print(f"{'cut':>6} {'h':>2} {'n_train':>8} {'n_test':>7} {'dyads':>6} "
          f"{'WITHIN':>7} {'persist':>8} {'rmse':>7} {'rmse_p':>7}")
    for fold in folds:
        print(f"{fold['cut_year']:>6} {fold['horizon']:>2} {fold['n_train']:>8,} "
              f"{fold['n_test']:>7,} {fold['dyads_scored']:>6} "
              f"{str(fold['within_dyad']):>7} {str(fold['within_dyad_persistence']):>8} "
              f"{fold['rmse']:>7.3f} {fold['rmse_persistence']:>7.3f}")

    passed, reason = intensity.passes_gate(folds)
    print(f"\nGATE: {'PASS' if passed else 'FAIL'} — {reason}")

    if args.ablate:
        print(f"\nablations (walk-forward, target={args.target}):")
        variants = {
            "all nine features": None,
            "without dyad character": [
                i for i, n in enumerate(feature_module.FEATURE_NAMES)
                if n not in feature_module.BETWEEN_DYAD
            ],
            "persistence alone": [0, feature_module.FEATURE_NAMES.index("level_now")],
        }
        for label, columns in variants.items():
            _, why = intensity.passes_gate(intensity.walk_forward(
                feature_rows, cut_years=args.cuts, target=args.target, columns=columns
            ))
            print(f"  {label:<26} {why}")

    if args.dry_run:
        print("\n--dry-run: no artifact written")
        return 0 if passed else 2

    weights, mean, sd = intensity.fit_final(feature_rows, target=args.target)
    artifact = registry.build_artifact(
        name=args.name,
        weights=weights,
        scaler_mean=mean,
        scaler_sd=sd,
        target=args.target,
        folds=folds,
        gate=(passed, reason),
        train_span=span,
        residual_sd=residual_spread(feature_rows, folds),
        rows=len(feature_rows),
        dyads=len(dyads),
    )
    written = registry.save(artifact)
    print(f"\nwrote {written}  (hash {artifact['hash']})")
    for horizon, w in sorted(weights.items()):
        print(f"  h={horizon}  " + "  ".join(
            f"{n} {v:+.3f}"
            for n, v in zip(feature_module.SHIPPED_FEATURES, w, strict=True)
        ))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
