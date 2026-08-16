"""Fit the per-region transition model and write its artifact — OFFLINE.

  python scripts/train_dynamics.py                 # every region, gate, write
  python scripts/train_dynamics.py mena            # one region
  python scripts/train_dynamics.py --dry-run       # report, write nothing
  python scripts/train_dynamics.py --ablate        # what each feature is worth

Reads the CORPUS, never a live store, so a committed artifact is reproducible
from the commit alone — the same rule `fit_game.py` and `train_forecaster.py`
follow, and the reason all three can be re-derived on any machine.

WHAT IS FITTED: a small residual over the counted kernel's log-probabilities
(core/models/dynamics.py). W = 0 is exactly today's behaviour, so the model
starts from the counts rather than competing with them.

THE GATE IS ENFORCED HERE, and it is the within-dyad one. A model that
improves pooled log-loss while ordering a pair's own quarters no better than
the pooled table has not done the job this model exists for — making the game
dyad-specific — and is written with `gate_passed: false` rather than silently
shipped.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.games import state as state_module  # noqa: E402
from core.games import transition  # noqa: E402
from core.models import dynamics, registry  # noqa: E402
from core.wire import corpus  # noqa: E402

#: Walk-forward cuts as SHARES of the ordered rows, not calendar years: the
#: three packs cover very different spans and a fixed year would put eurasia's
#: cut in the middle of its record and china's past the end of it.
DEFAULT_CUTS = (0.60, 0.70, 0.80)


def rows_for(region: str, space: Any = None) -> list[dict[str, Any]]:
    """One row per (dyad, quarter) transition: features, offset key, label —
    the joint action read in `space` (the adversary reading by default, the
    ally reading for `dynamics-ally-<region>`)."""
    from core.games import family as family_module
    from core.models import panel as panel_module

    space = space or family_module.ADVERSARY
    panel_rows, game_rows = corpus.views(region)
    table = panel_module.build(panel_rows, region_pack=region)
    joint = transition.joint_actions(
        game_rows, quarter_of=panel_module.quarter_index, space=space)

    by_dyad: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in table:
        by_dyad[row["dyad_id"]].append(row)

    out: list[dict[str, Any]] = []
    for dyad, unsorted in by_dyad.items():
        series = sorted(unsorted, key=lambda r: r["q"])
        scale = state_module.dyad_scale([float(r["intensity"]) for r in series])
        if not scale:
            continue
        levels = state_module.classify(series)
        for i in range(len(series) - 1):
            if series[i + 1]["q"] != series[i]["q"] + 1:
                continue
            actions = joint.get((dyad, series[i]["q"]))
            if actions is None:
                continue
            window = series[max(0, i - dynamics.WINDOW_QUARTERS + 1): i + 1]
            band = levels[i]
            out.append({
                "dyad": dyad,
                "q": int(series[i]["q"]),
                "date": str(series[i]["date"]),
                "band": band,
                "next": levels[i + 1],
                "a": space.index(actions[0]),
                "b": space.index(actions[1]),
                **dynamics.row_features(window, band, scale),
            })
    return sorted(out, key=lambda r: r["date"])


def counted_kernel(rows: list[dict[str, Any]]) -> np.ndarray:
    """The kernel that ships today, built from THESE rows only.

    Rebuilt per fold rather than once: a kernel counted over the test period
    would leak the answer into the offset, which is the same oos-spec mistake
    the as-of walk fixed on 2026-08-14.
    """
    bands = len(state_module.INTENSITY_EDGES)
    counts: dict[tuple[int, int, int], np.ndarray] = {}
    for row in rows:
        key = (row["band"], row["a"], row["b"])
        counts.setdefault(key, np.zeros(bands))[row["next"]] += 1.0
    kernel, _observed = transition.kernel(counts)
    return kernel


def evaluate(train: list[dict[str, Any]], test: list[dict[str, Any]],
             *, l2: float = dynamics.L2) -> dict[str, Any]:
    kernel = counted_kernel(train)
    model = dynamics.fit(train, kernel, l2=l2)
    p_model = dynamics.predict(model, test, kernel)
    p_counted = np.exp(dynamics.offsets(kernel, test))
    p_counted = p_counted / p_counted.sum(axis=1, keepdims=True)
    return {
        "train": len(train), "test": len(test),
        "log_loss": dynamics.log_loss(p_model, test),
        "log_loss_counted": dynamics.log_loss(p_counted, test),
        "rho": dynamics.within_dyad_rho(p_model, test),
        "rho_counted": dynamics.within_dyad_rho(p_counted, test),
    }


def walk(rows: list[dict[str, Any]],
         cuts: tuple[float, ...] = DEFAULT_CUTS) -> list[dict[str, Any]]:
    folds: list[dict[str, Any]] = []
    for share in cuts:
        cut = int(len(rows) * share)
        train, test = rows[:cut], rows[cut:]
        if len(train) < 500 or len(test) < 200:
            continue
        fold = evaluate(train, test)
        fold["cut_share"] = share
        fold["cut_date"] = rows[cut]["date"]
        folds.append(fold)
    return folds


def ablate(rows: list[dict[str, Any]]) -> None:
    """What each feature is worth, held out. Dropping one at a time."""
    cut = int(len(rows) * 0.75)
    train, test = rows[:cut], rows[cut:]
    base = evaluate(train, test)
    print(f"  {'all features':22s} log-loss {base['log_loss']:.4f}  "
          f"rho {base['rho']:+.4f}")
    original: tuple[str, ...] = dynamics.FEATURES
    for dropped in original:
        dynamics.FEATURES = tuple(f for f in original if f != dropped)  # type: ignore[assignment]
        try:
            fold = evaluate(train, test)
            print(f"  without {dropped:14s} log-loss {fold['log_loss']:.4f} "
                  f"({fold['log_loss'] - base['log_loss']:+.4f})  "
                  f"rho {fold['rho']:+.4f} "
                  f"({fold['rho'] - base['rho']:+.4f})")
        finally:
            dynamics.FEATURES = original  # type: ignore[assignment]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("regions", nargs="*", help="default: every pack")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ablate", action="store_true")
    parser.add_argument(
        "--family", default="adversary", choices=("adversary", "ally"),
        help="which reading of the record the kernel is conditioned on; 'ally' "
             "trains the per-pair residual over commit/affirm/withhold and writes "
             "models/dynamics-ally-<region>.json, which the ally game's kernel reads",
    )
    args = parser.parse_args()
    from core.games import family as family_module

    space = family_module.space_for(args.family)
    stem = "dynamics" if space.family != "ally" else "dynamics-ally"

    names = args.regions or corpus.installed()
    for region in names:
        print(f"\n=== {region} ({space.family} reading)")
        rows = rows_for(region, space)
        print(f"  {len(rows)} transitions, "
              f"{len({r['dyad'] for r in rows})} dyads, "
              f"{rows[0]['date']} → {rows[-1]['date']}")
        if args.ablate:
            ablate(rows)
            continue
        folds = walk(rows)
        for fold in folds:
            print(f"  cut {fold['cut_date']}  "
                  f"log-loss {fold['log_loss_counted']:.4f} → {fold['log_loss']:.4f}   "
                  f"within-dyad rho {fold['rho_counted']:+.4f} → {fold['rho']:+.4f}")
        passed, summary = dynamics.passes_gate(folds)
        print(f"  gate: {'PASS' if passed else 'FAIL'} — {summary}")

        # The shipped weights are fitted on EVERYTHING; the folds are what
        # decided whether to ship them.
        kernel = counted_kernel(rows)
        fitted = dynamics.fit(rows, kernel)
        payload: dict[str, Any] = {
            "name": f"{stem}-{region}",
            "version": registry.ARTIFACT_VERSION,
            "family": space.family,
            "actions": list(space.actions),
            "trained_on": {
                "region": region, "rows": len(rows),
                "dyads": len({r["dyad"] for r in rows}),
                "from": rows[0]["date"], "to": rows[-1]["date"],
            },
            "model": {**fitted.payload(), "region": region},
            "folds": folds,
            "gate_passed": passed,
            "gate_summary": summary,
            "features": list(dynamics.FEATURES),
            "interactions": list(dynamics.INTERACTIONS),
            "excluded": {
                "declared": ["ally", "rival", "bloc", "proxy", "cinc_ratio"],
                "structural": ["betweenness", "eigenvector", "degree",
                               "constraint", "community"],
                "why": (
                    "measured against production's 37,930 NetworkMetric nodes: "
                    "they move pooled log-loss by 0.004-0.025 and move "
                    "within-dyad ordering by nothing or down (china "
                    "+0.1740 → +0.1591). A feature that says which dyad this "
                    "is scores pooled and hurts within it."
                ),
            },
        }
        payload["hash"] = registry.content_hash(payload)
        if args.dry_run:
            print("  dry run — nothing written")
            continue
        path = registry.save(payload)
        print(f"  wrote {path} ({payload['hash']})")


if __name__ == "__main__":
    main()
