"""Reproduce every number in docs/ml-spec.md section 2.

  python scripts/explore_panel.py                  # the whole report
  python scripts/explore_panel.py --cut 1998       # one walk-forward cut

EXPLORATION, NOT A PIPELINE. Nothing here writes to the graph, freezes a
Forecast, or ships a number to the surface — it exists so the claims in the ML
spec can be rechecked against the archive instead of trusted, which is the
same standard every counted likelihood in this repo is held to.

It builds the dyad-quarter panel the spec proposes (active dyads, explicit
zeros between each dyad's first and last observation, label = a significant
escalation inside the next four quarters), then measures the four things that
decide whether a learned model is worth building:

  1. what the trivial baselines already capture,
  2. whether a fitted model's skill is BETWEEN dyads or WITHIN them,
  3. how much memory and sequence structure the process carries,
  4. how badly the base rate moves across decades.

The headline it exists to produce: a pooled AUC of 0.92 and a within-dyad AUC
of 0.35 are the same model. Read-only, deterministic, no dependencies beyond
the [analysis] extra.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import settings as settings_module  # noqa: E402
from core.graph import kuzu_store  # noqa: E402
from core.reasoning import regimes  # noqa: E402

#: Label horizon, quarters. Matches the near-term forecaster's 3-year framing
#: closely enough to be comparable, short enough to be a timing question.
HORIZON_Q = 4
#: The same significance definition core/reasoning/forecasting.py counts with,
#: so the panel's episodes and the frozen forecast's episodes are one concept.
SIG_PCTL = 0.90
#: A dyad needs this many occupied quarters before it can be modelled at all.
MIN_OCCUPIED = 8


def quarter_index(date: str) -> int:
    """Quarters since year 0 — a single integer so lags are arithmetic."""
    year = int(date[:4])
    month = int(date[5:7]) if len(date) >= 7 else 1
    return year * 4 + (month - 1) // 3


def auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Rank-based AUC with tie averaging. 0.5 is a coin flip; BELOW 0.5 is a
    model ranking backwards, which is a finding rather than a bug."""
    pos, neg = scores[labels == 1], scores[labels == 0]
    if not len(pos) or not len(neg):
        return float("nan")
    values = np.concatenate([pos, neg])
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(order), dtype=float)
    ranks[order] = np.arange(1, len(order) + 1)
    for value in np.unique(values):
        tied = values == value
        if tied.sum() > 1:
            ranks[tied] = ranks[tied].mean()
    return float(
        (ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))
    )


def brier(scores: np.ndarray, labels: np.ndarray) -> float:
    return float(np.mean((scores - labels) ** 2))


def logistic_irls(
    x: np.ndarray, y: np.ndarray, *, penalty: float = 1.0, iterations: int = 25
) -> np.ndarray:
    """L2-penalised logistic regression by iteratively reweighted least
    squares. No learning rate, no early stopping, no seed — a baseline whose
    whole value is that it has nothing to tune, and so cannot be tuned into
    looking good."""
    weights = np.zeros(x.shape[1])
    ridge = penalty * np.eye(x.shape[1])
    ridge[0, 0] = 0.0  # the intercept is never penalised
    for _ in range(iterations):
        p = 1.0 / (1.0 + np.exp(-np.clip(x @ weights, -30, 30)))
        variance = np.clip(p * (1 - p), 1e-6, None)
        hessian = x.T @ (x * variance[:, None]) + ridge
        gradient = x.T @ (y - p) - ridge @ weights
        try:
            weights = weights + np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            break
    return weights


def predict(x: np.ndarray, weights: np.ndarray) -> np.ndarray:
    scores: np.ndarray = 1.0 / (1.0 + np.exp(-np.clip(x @ weights, -30, 30)))
    return scores


def rule(title: str) -> None:
    print(f"\n{'=' * 76}\n{title}\n{'=' * 76}")


def load_events(db_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = kuzu_store.connect(db_path, read_only=True)
    try:
        events = kuzu_store.query(
            conn,
            "MATCH (e:Event)-[:OF_DYAD]->(d:Dyad) RETURN d.node_id AS dyad, "
            "e.event_time AS event_time, e.escalation_direction AS direction, "
            "e.escalation_magnitude AS magnitude, e.goldstein AS goldstein, "
            "e.quad_class AS quad_class",
        )
        relations = kuzu_store.query(
            conn,
            "MATCH (:Actor)-[r:RELATES_TO]->(:Actor) RETURN r.relation_type AS kind",
        )
    finally:
        kuzu_store.close(conn)
    return events, relations


def build_panel(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    """The dyad-quarter panel with explicit zeros, and the significance
    threshold it was built at."""
    anchor = max(str(e["event_time"]) for e in events)
    magnitudes = sorted(
        float(e["magnitude"])
        for e in events
        if e["direction"] == "escalating"
        and e["magnitude"] is not None
        and regimes.comparable(anchor, str(e["event_time"]))
    )
    threshold = magnitudes[int(len(magnitudes) * SIG_PCTL)] if magnitudes else 0.0

    cells: dict[tuple[str, int], dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "goldstein": [], "significant": 0, "conflict": 0}
    )
    for event in events:
        cell = cells[(event["dyad"], quarter_index(str(event["event_time"])))]
        cell["n"] += 1
        if event["goldstein"] is not None:
            cell["goldstein"].append(float(event["goldstein"]))
        if event["quad_class"] == "material_conflict":
            cell["conflict"] += 1
        if (
            event["direction"] == "escalating"
            and event["magnitude"] is not None
            and float(event["magnitude"]) >= threshold
        ):
            cell["significant"] += 1

    occupied: dict[str, set[int]] = defaultdict(set)
    for dyad, q in cells:
        occupied[dyad].add(q)

    rows: list[dict[str, Any]] = []
    for dyad, quarters in occupied.items():
        if len(quarters) < MIN_OCCUPIED:
            continue
        ordered = sorted(quarters)
        for q in range(ordered[0], ordered[-1] + 1):
            # Absent cell = a quarter with no events: an explicit NEGATIVE, not
            # a missing row. Training on occupied quarters alone is how a panel
            # like this silently becomes positive-only.
            filled = cells.get((dyad, q))
            rows.append({
                "dyad": dyad,
                "q": q,
                "n": filled["n"] if filled else 0,
                "significant": 1.0 if (filled and filled["significant"] > 0) else 0.0,
                "tone": (
                    float(np.mean(filled["goldstein"]))
                    if filled and filled["goldstein"] else 0.0
                ),
                "conflict": filled["conflict"] if filled else 0,
            })
    return rows, threshold


FEATURE_NAMES = (
    "intercept", "sig_now", "sig_1y", "sig_1_3y", "freq_5y",
    "log_volume", "log_conflict", "tone", "prior_rate",
)
#: Features that describe the DYAD rather than the moment. Isolating these is
#: how section 2.2 separates "which dyad is this" from "is it about to move".
DYAD_LEVEL = ("freq_5y", "prior_rate")


def design_matrix(
    rows: list[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(X, y, quarter, dyad) using ONLY information available at each row's
    own quarter."""
    sig_at = {(r["dyad"], r["q"]): r["significant"] for r in rows}
    n_at = {(r["dyad"], r["q"]): r["n"] for r in rows}
    tone_at = {(r["dyad"], r["q"]): r["tone"] for r in rows}
    conflict_at = {(r["dyad"], r["q"]): r["conflict"] for r in rows}
    first_q: dict[str, int] = {}
    for r in rows:
        first_q[r["dyad"]] = min(first_q.get(r["dyad"], r["q"]), r["q"])

    features, labels, quarters, dyads = [], [], [], []
    for r in rows:
        dyad, q = r["dyad"], r["q"]
        if any((dyad, q + k) not in sig_at for k in range(1, HORIZON_Q + 1)):
            continue
        history = [sig_at.get((dyad, q - k), 0.0) for k in range(20)]
        volume = [n_at.get((dyad, q - k), 0) for k in range(8)]
        conflict = [conflict_at.get((dyad, q - k), 0) for k in range(8)]
        tone = [tone_at.get((dyad, q - k), 0.0) for k in range(4)]
        prior = [sig_at[(dyad, x)] for x in range(first_q[dyad], q) if (dyad, x) in sig_at]
        features.append([
            1.0,
            history[0],
            float(any(history[:4])),
            float(any(history[4:12])),
            float(np.mean(history)),
            float(np.log1p(sum(volume))),
            float(np.log1p(sum(conflict))),
            float(np.mean(tone)),
            float(np.mean(prior)) if prior else 0.0,
        ])
        labels.append(float(any(sig_at[(dyad, q + k)] for k in range(1, HORIZON_Q + 1))))
        quarters.append(q)
        dyads.append(dyad)
    return (
        np.array(features), np.array(labels), np.array(quarters), np.array(dyads)
    )


def within_dyad_auc(
    scores: np.ndarray, labels: np.ndarray, dyads: np.ndarray
) -> tuple[float, int]:
    """Mean AUC computed INSIDE each dyad, over the dyads whose window holds
    both outcomes. A dyad that never escalates has no ordering to get right,
    and scoring it as 0.5 would launder ignorance into skill."""
    per_dyad = []
    for dyad in set(dyads.tolist()):
        mask = dyads == dyad
        if 0 < labels[mask].sum() < mask.sum():
            per_dyad.append(auc(scores[mask], labels[mask]))
    return (float(np.mean(per_dyad)) if per_dyad else float("nan")), len(per_dyad)


def entropy(counts: Counter[str]) -> float:
    total = sum(counts.values())
    return -sum((v / total) * math.log2(v / total) for v in counts.values() if v)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="graph path (default: settings)")
    parser.add_argument(
        "--cut", type=int, default=None, help="a single walk-forward cut year"
    )
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else settings_module.load().kuzu_db_path
    if not Path(db_path).exists():
        print(f"no graph at {db_path} — seed and load one first")
        return 1

    events, relations = load_events(Path(db_path))
    print(f"events {len(events):,}   durable relations {len(relations):,}")
    rows, threshold = build_panel(events)
    x, y, quarters, dyads = design_matrix(rows)

    rule("1 · THE PANEL")
    print(f"significance threshold (p{SIG_PCTL:.0%} of in-regime departures): {threshold:.2f}")
    print(f"modelable dyads (>= {MIN_OCCUPIED} occupied quarters): {len(set(dyads.tolist()))}")
    print(f"panel rows with a fully observable {HORIZON_Q}-quarter horizon: {len(y):,}")
    print(f"positive rate: {y.mean():.4f}   <- the number to beat")
    scored = {(d, q) for d, q in zip(dyads.tolist(), quarters.tolist(), strict=True)}
    counts = [r["n"] for r in rows if (r["dyad"], r["q"]) in scored]
    print(f"quarters holding no event at all: {np.mean([c == 0 for c in counts]):.3f}")
    print(f"quarters holding a significant one: {x[:, FEATURE_NAMES.index('sig_now')].mean():.3f}")

    rule("2 · WHAT THE TRIVIAL BASELINES ALREADY CAPTURE")
    persistence = 0.1 + 0.8 * x[:, FEATURE_NAMES.index("sig_now")]
    recent = 0.1 + 0.8 * x[:, FEATURE_NAMES.index("sig_1y")]
    for name, p in (
        ("base rate", np.full(len(y), y.mean())),
        ("persistence (significant this quarter)", persistence),
        ("significant in the last 4 quarters", recent),
    ):
        print(f"  {name:<40} Brier {brier(p, y):.4f}   pooled AUC {auc(p, y):.4f}")

    rule("3 · WALK-FORWARD: TRAIN ON THE PAST, TEST THE NEXT FIVE YEARS")
    cuts = [args.cut] if args.cut else [1990, 1994, 1998, 2002]
    print(f"{'cut':>6} {'n_train':>8} {'n_test':>7} {'pos_tr':>7} {'pos_te':>7} "
          f"{'B_base':>7} {'B_persist':>10} {'B_logit':>8} {'AUC_p':>6} {'AUC_l':>6}")
    for cut_year in cuts:
        cut, end = cut_year * 4, (cut_year + 5) * 4
        train = quarters < cut
        test = (quarters >= cut) & (quarters < end)
        if train.sum() < 200 or test.sum() < 100:
            continue
        weights = logistic_irls(x[train], y[train])
        p_logit = predict(x[test], weights)
        p_base = np.full(int(test.sum()), y[train].mean())
        p_persist = 0.1 + 0.8 * x[test][:, FEATURE_NAMES.index("sig_now")]
        print(f"{cut_year:>6} {train.sum():>8,} {test.sum():>7,} {y[train].mean():>7.3f} "
              f"{y[test].mean():>7.3f} {brier(p_base, y[test]):>7.4f} "
              f"{brier(p_persist, y[test]):>10.4f} {brier(p_logit, y[test]):>8.4f} "
              f"{auc(p_persist, y[test]):>6.3f} {auc(p_logit, y[test]):>6.3f}")

    rule("4 · IS THE SKILL BETWEEN DYADS OR WITHIN THEM?")
    grouped: dict[str, list[float]] = defaultdict(list)
    for dyad, label in zip(dyads.tolist(), y.tolist(), strict=True):
        grouped[dyad].append(label)
    means = np.array([np.mean(v) for v in grouped.values()])
    sizes = np.array([len(v) for v in grouped.values()])
    between = float(np.sum(sizes * (means - y.mean()) ** 2) / len(y))
    total = float(np.var(y))
    print(f"  label variance {total:.5f} = between-dyad {between:.5f} "
          f"({between / total:.1%}) + within-dyad {total - between:.5f} "
          f"({1 - between / total:.1%})")
    print("  Only the within share is what a forecast is asked for.\n")

    cut_year = args.cut or 1998
    cut, end = cut_year * 4, (cut_year + 5) * 4
    train = quarters < cut
    test = (quarters >= cut) & (quarters < end)
    subsets = {
        "all features": list(range(x.shape[1])),
        "dynamics only (no dyad averages)": [
            i for i, n in enumerate(FEATURE_NAMES) if n not in DYAD_LEVEL
        ],
        "dyad averages only": [
            i for i, n in enumerate(FEATURE_NAMES)
            if n in DYAD_LEVEL or n == "intercept"
        ],
    }
    print(f"  cut {cut_year}, test {cut_year}-{cut_year + 5}")
    print(f"  {'feature set':<34} {'Brier':>7} {'POOLED':>8} {'WITHIN':>8} {'dyads':>6}")
    for label, columns in subsets.items():
        weights = logistic_irls(x[train][:, columns], y[train])
        p = predict(x[test][:, columns], weights)
        within, n_dyads = within_dyad_auc(p, y[test], dyads[test])
        print(f"  {label:<34} {brier(p, y[test]):>7.4f} {auc(p, y[test]):>8.3f} "
              f"{within:>8.3f} {n_dyads:>6}")
    p_persist = 0.1 + 0.8 * x[test][:, FEATURE_NAMES.index("sig_now")]
    within, n_dyads = within_dyad_auc(p_persist, y[test], dyads[test])
    print(f"  {'persistence':<34} {'—':>7} {auc(p_persist, y[test]):>8.3f} "
          f"{within:>8.3f} {n_dyads:>6}")

    rule("5 · MEMORY AND SEQUENCE STRUCTURE")
    sig_at = {(r["dyad"], r["q"]): r["significant"] for r in rows}
    for lag in (1, 2, 4, 8, 12, 20):
        a, b = [], []
        for r in rows:
            other = sig_at.get((r["dyad"], r["q"] + lag))
            if other is not None:
                a.append(r["significant"])
                b.append(other)
        if len(a) > 30 and np.std(a) > 0 and np.std(b) > 0:
            print(f"  autocorrelation, lag {lag:>2}q: r = {np.corrcoef(a, b)[0, 1]: .4f}"
                  f"   (n={len(a):,})")

    sequences: dict[str, list[str]] = defaultdict(list)
    for event in sorted(events, key=lambda e: str(e["event_time"])):
        if event["quad_class"]:
            sequences[event["dyad"]].append(str(event["quad_class"]))
    transitions: Counter[tuple[str, str]] = Counter()
    marginal: Counter[str] = Counter()
    for sequence in sequences.values():
        for a_, b_ in zip(sequence, sequence[1:], strict=False):
            transitions[(a_, b_)] += 1
        marginal.update(sequence)
    total_transitions = sum(transitions.values())
    conditional = 0.0
    for state in marginal:
        following = Counter({b_: n for (a_, b_), n in transitions.items() if a_ == state})
        if following:
            conditional += (sum(following.values()) / total_transitions) * entropy(following)
    h_marginal = entropy(marginal)
    print(f"\n  H(quad) = {h_marginal:.4f} bits    H(quad | previous) = {conditional:.4f} bits")
    print(f"  mutual information = {h_marginal - conditional:.4f} bits "
          f"({(h_marginal - conditional) / h_marginal:.1%} of the marginal)")

    rule("6 · NON-STATIONARITY, AND HOW MUCH OF IT IS COVERAGE")
    for decade in range(1970, 2030, 10):
        selected = (quarters // 4 >= decade) & (quarters // 4 < decade + 10)
        if selected.sum() > 50:
            p = 0.1 + 0.8 * x[selected][:, FEATURE_NAMES.index("sig_now")]
            print(f"  {decade}s  n={selected.sum():>6,}  positive rate "
                  f"{y[selected].mean():.3f}   persistence AUC {auc(p, y[selected]):.3f}")
    kinds = Counter(str(r["kind"]) for r in relations)
    print(f"\n  durable relation types: {dict(kinds.most_common(6))}")
    print("  Membership dominates and is near-constant across dyads; the")
    print("  discriminative relational tier is alliance and rivalry.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
