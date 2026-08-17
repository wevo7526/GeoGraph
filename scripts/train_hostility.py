"""Fit the relationship classifier and freeze it — OFFLINE, from the corpus.

    python scripts/train_hostility.py            # fit, gate, report
    python scripts/train_hostility.py --write    # …and write models/hostility.json

The fit reads the committed artifacts in data/derived and COW's MIDB in
data/raw, so it is reproducible from the commit alone (the rule every offline
fit in this repo holds — see scripts/fit_game.py). It never touches the graph
or Postgres.

THE GATE. Fitted on dyad-years to 2004, scored on 2005-2014, and it must beat
three baselines that cost nothing:

  * `shipped_rule` — the thresholds this replaces: coercive share >= 0.25, or
    coercive count >= 300.
  * `declared_rivalry` — the sourced standing on its own. If a curated
    relation predicts militarised disputes as well as the wire does, the wire
    is not adding anything and the model should not ship.
  * `coercion_count` — the count alone. The simplest thing that could work.

A model that fails any of them is written with `gate_passed: false`, and
`models.hostility.load` then refuses it: `games.family` keeps its rules and
the payload says which read it used.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import packs  # noqa: E402
from core.classifier import escalation  # noqa: E402
from core.models import hostility  # noqa: E402
from core.wire import corpus  # noqa: E402


def _roster_ccodes() -> dict[int, str]:
    out: dict[int, str] = {}
    for name in packs.available():
        for actor in packs.load(name).actors:
            code = actor.get("cow_ccode")
            if code:
                try:
                    out[int(code)] = str(actor["id"])
                except (TypeError, ValueError):
                    continue
    return out


def _declared_rivalries() -> dict[str, int]:
    """dyad_id → the year a declared rivalry opens, from the packs."""
    out: dict[str, int] = {}
    for name in packs.available():
        for relation in packs.load(name).relations:
            if relation.get("relation_type") != "rivalry":
                continue
            dyad = escalation.dyad_id(str(relation["a"]), str(relation["b"]))
            stamp = str(relation.get("valid_from") or "")[:4]
            year = int(stamp) if stamp.isdigit() else 1905
            out[dyad] = min(out.get(dyad, year), year)
    return out


def build() -> tuple[
    np.ndarray, np.ndarray, np.ndarray, list[tuple[str, int]], dict[str, Any]
]:
    ccodes = _roster_ccodes()
    positives = hostility.mid_dyad_years(ccodes)
    rivalries = _declared_rivalries()

    cells: dict[tuple[str, int], dict[str, float]] = {}
    for name in packs.available():
        rows = corpus.load(name)
        if isinstance(rows, tuple):
            rows = rows[0]
        for key, cell in hostility.dyad_year_rows(rows).items():
            # A dyad can appear in two packs' lenses; the wider record wins
            # rather than the alphabetically later one.
            prior = cells.get(key)
            if prior is None or cell["events"] > prior["events"]:
                cells[key] = cell

    keys: list[tuple[str, int]] = []
    design: list[list[float]] = []
    labels: list[int] = []
    years: list[int] = []
    for (dyad, year), cell in sorted(cells.items()):
        if year > hostility.LABEL_LAST_YEAR or year < 1979:
            continue
        opened = rivalries.get(dyad)
        keys.append((dyad, year))
        design.append(hostility.design_row(
            cell,
            declared_rivalry=opened is not None and year >= opened,
            previous=cells.get((dyad, year - 1)),
        ))
        labels.append(1 if (dyad, year) in positives else 0)
        years.append(year)
    meta = {
        "packs": packs.available(),
        "dyad_years": len(keys),
        "positives": int(sum(labels)),
        "positive_rate": round(float(np.mean(labels)), 4) if labels else 0.0,
        "distinct_pairs": len({d for d, _ in keys}),
        "label_span": [1979, hostility.LABEL_LAST_YEAR],
    }
    return (np.asarray(design, dtype=float), np.asarray(labels, dtype=float),
            np.asarray(years, dtype=int), keys, meta)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write models/hostility.json")
    parser.add_argument("--l2", type=float, default=1.0)
    args = parser.parse_args()

    design, labels, years, keys, meta = build()
    if not len(design):
        print("no dyad-years — is the corpus installed?")
        return 1
    print(f"dyad-years {meta['dyad_years']:,}  positives {meta['positives']:,} "
          f"({meta['positive_rate']:.1%})  pairs {meta['distinct_pairs']:,}")

    train = years <= hostility.FIT_LAST_YEAR
    test = ~train
    print(f"fit on {int(train.sum()):,} rows to {hostility.FIT_LAST_YEAR}; "
          f"score on {int(test.sum()):,} rows after "
          f"({int(labels[test].sum()):,} positive)")

    scaled, mean, scale = hostility._standardise(design[train])
    theta = hostility.fit_logistic(scaled, labels[train], l2=args.l2)
    held = (design[test] - mean) / scale
    model_auc = hostility.auc(hostility.predict_proba(theta, held), labels[test])

    # The baselines, on the same held-out rows.
    share = design[test][:, hostility.FEATURES.index("coercion_share")]
    count = np.expm1(design[test][:, hostility.FEATURES.index("log_coercion")])
    rivalry = design[test][:, hostility.FEATURES.index("declared_rivalry")]
    baselines = {
        "shipped_rule": hostility.auc(
            ((share >= 0.25) | (count >= 300)).astype(float), labels[test]),
        "declared_rivalry": hostility.auc(rivalry, labels[test]),
        "coercion_count": hostility.auc(count, labels[test]),
    }
    beaten = {k: round(model_auc - v, 4) for k, v in baselines.items()}
    passed = all(model_auc > v + 0.01 for v in baselines.values())

    print(f"\nheld-out AUC   model {model_auc:.4f}")
    for name, value in baselines.items():
        print(f"               {name:18} {value:.4f}   (+{beaten[name]:.4f})")
    print(f"\ngate {'PASSED' if passed else 'FAILED'}")
    print("\nweights (standardised):")
    for name, weight in zip(hostility.FEATURES, theta[1:], strict=True):
        print(f"  {name:18} {weight:+.4f}")

    payload = {
        "name": "hostility",
        "trained_at_label_span": meta["label_span"],
        "fit_last_year": hostility.FIT_LAST_YEAR,
        "features": list(hostility.FEATURES),
        "theta": [float(x) for x in theta],
        "standardise": {"mean": [float(x) for x in mean], "scale": [float(x) for x in scale]},
        "held_out_auc": round(float(model_auc), 4),
        "baselines": {k: round(float(v), 4) for k, v in baselines.items()},
        "beats_baselines_by": beaten,
        "gate": (
            "fitted on dyad-years to 2004 and scored on 2005-2014; must beat the "
            "shipped threshold rule, the declared standing alone and the raw "
            "coercion count by at least 0.01 AUC"
        ),
        "gate_passed": bool(passed),
        "label": (
            "COW MIDB 5.0: a dyad-year is positive when both states were in the "
            "same militarised dispute on OPPOSITE sides"
        ),
        "caveat": (
            "MID 5.0 ends in 2014 and the wire runs to 2026, so the recent era is "
            "an extrapolation. The label is dispute PARTICIPATION, a floor on "
            "adversariality rather than a full ordering of it."
        ),
        **meta,
    }
    if args.write:
        hostility.ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
        hostility.ARTIFACT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {hostility.ARTIFACT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
