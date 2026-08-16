"""Indirect inference: fit the game's payoffs to what the archive actually did.

THE JOIN BETWEEN THE TWO HALVES OF THIS REPO, and it is a named technique
rather than a gesture (Gouriéroux–Monfort–Renault). We can SIMULATE the game
easily and cannot write its likelihood, which is exactly the setting indirect
inference exists for: pick a statistic both the archive and a simulation can
produce, then choose the structural parameters that make them agree.

WHICH STATISTIC — and this changed, on evidence. docs/game-spec.md proposed
the shipped ridge's per-horizon decay, reasoning that reversion speed is a
function of patience and cost. Measured, it has NO LEVERAGE: sweeping the
whole plausible parameter space moves the simulated decay only between 46.4%
and 50.4%, because the transition kernel is measured and fixed and dominates
how intensity evolves. The observed decay is worse than useless besides — it
reads +22.6% on 1979–1995, +19.5% on 1990–2005, +3.4% pooled and −9.2% on
2015–2020, so it is mostly a statement about which window it was computed in.

What the payoffs DO control is which action gets played. P(escalate |
resolute) runs 0.001 under costly war to 0.470 under cheap war, and the
distance to the archive's own frequencies spreads sixfold across the same
sweep. So the binding moment is ACTION FREQUENCY BY INTENSITY BAND, counted
identically for the archive and the simulation.

WHAT THE FIT DOES AND DOES NOT ESTABLISH. It reaches 0.650 against 1.755 at
the defaults, which is real. But `discount` and `cost_resolute` land on their
clip bounds, and a boundary solution means the game still cannot fully
reproduce the archive's action mix — the parameter VALUES should not be
over-read, only the direction they point.

DETERMINISM. Simulation needs randomness; frozen forecasts need
reproducibility. The seed is explicit, carried in the result, and never read
from a clock — so a fit can be recomputed exactly from what it reports.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np

from core.games import family as family_module
from core.games import solve as solve_module
from core.games import state as state_module
from core.models import features as feature_module
from core.models import intensity as intensity_module

#: Synthetic dyads per simulation. Enough that the auxiliary fit is stable
#: between evaluations — a noisy objective sends a derivative-free optimiser
#: chasing its own sampling error — and small enough that a few hundred
#: evaluations stay affordable.
SIM_DYADS = 80
#: Quarters per synthetic dyad. Comparable to a real dyad's observed span, so
#: the expanding-window features have the same amount of history to work with
#: as they do on the archive.
SIM_QUARTERS = 120

#: The intensity a band represents, for turning a simulated band path back
#: into a panel the auxiliary model can read. Band 0 is genuinely quiet; the
#: rest take their lower edge scaled by a typical dyad yardstick.
_BAND_SCALE = 9.0


def _band_intensity(band: int) -> float:
    if band <= 0:
        return 0.0
    return float(state_module.INTENSITY_EDGES[band]) * _BAND_SCALE


def action_frequencies(
    bands: list[int], actions: list[tuple[int, int]]
) -> np.ndarray:
    """P(action | intensity band), pooled over both sides — THE binding moment.

    IT REPLACED THE DECAY, ON EVIDENCE. docs/game-spec.md proposed the ridge's
    per-horizon decay as the statistic to fit, on the reasoning that reversion
    speed is a function of patience and cost. Measured, it is not: sweeping the
    whole plausible parameter space moves the simulated decay only between
    46.4% and 50.4%, while the observed statistic swings from −9% to +23%
    depending on which window it is computed in. The decay has no leverage
    because the transition kernel is MEASURED and fixed — it dominates how
    intensity evolves, and the payoffs merely choose which actions feed it.

    What the payoffs DO control is which action gets played:
    P(escalate | resolute) runs 0.001 under costly war to 0.470 under cheap
    war. So the frequencies are the moment with the leverage, and they are
    observable on both sides — read off coded events for the archive, off the
    policy for the simulation.

    Returned flat, band-major, so the objective can difference it directly.
    """
    n_bands = len(state_module.INTENSITY_EDGES)
    n_actions = len(state_module.ACTIONS)
    counts = np.zeros((n_bands, n_actions))
    for band, (a, b) in zip(bands, actions, strict=True):
        counts[band, a] += 1
        counts[band, b] += 1
    totals = counts.sum(axis=1, keepdims=True)
    # A band nobody was ever observed in contributes a uniform row rather than
    # a zero row: absence of evidence must not read as "never escalates".
    shares = np.where(totals > 0, counts / np.maximum(totals, 1), 1.0 / n_actions)
    return np.asarray(shares.reshape(-1), dtype=float)


def observed_frequencies(
    panel_rows: list[dict[str, Any]],
    joint: dict[tuple[str, int], tuple[str, str]],
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> np.ndarray:
    """The archive's action frequencies by band, through the same counter —
    in the family's space (its own reading of the record, its own indices)."""
    bands: list[int] = []
    actions: list[tuple[int, int]] = []
    by_dyad: dict[str, list[dict[str, Any]]] = {}
    for row in panel_rows:
        by_dyad.setdefault(row["dyad_id"], []).append(row)
    for dyad, rows in by_dyad.items():
        rows = sorted(rows, key=lambda r: int(r["q"]))
        levels = state_module.classify(rows)
        for level, row in zip(levels, rows, strict=True):
            pair = joint.get((dyad, int(row["q"])))
            if pair is None:
                continue
            bands.append(level)
            actions.append((space.index(pair[0]), space.index(pair[1])))
    return action_frequencies(bands, actions)


def simulate(
    equilibrium: dict[str, Any],
    kernel: np.ndarray,
    payoffs: solve_module.Payoffs,
    *,
    seed: int,
    dyads: int = SIM_DYADS,
    quarters: int = SIM_QUARTERS,
) -> list[dict[str, Any]]:
    """Synthetic panel rows, shaped exactly like `core.models.panel.build`.

    Each synthetic dyad draws a private type per side from the prior, then
    plays the equilibrium: sample both sides' actions from the policy, sample
    the next intensity band from the measured kernel, repeat. Beliefs move
    along the path, so a side that escalates is judged more likely resolute
    and faces a differently-playing opponent next quarter.
    """
    rng = np.random.default_rng(seed)
    policy = equilibrium["policy"]
    horizon = equilibrium["horizon"]
    n_actions = len(state_module.ACTIONS)
    rows: list[dict[str, Any]] = []

    for index in range(dyads):
        # Capability is a property of the pair, fixed for its history.
        capability = int(rng.integers(0, len(state_module.CAPABILITY_EDGES)))
        type_a = int(rng.random() < 0.5)
        type_b = int(rng.random() < 0.5)
        band = int(rng.integers(0, len(state_module.INTENSITY_EDGES)))
        dyad_id = f"dyad:sim-{index:03d}"

        for step in range(quarters):
            # The policy table is indexed by period within the horizon; a
            # simulated history longer than one horizon replays it, which is
            # what a stationary Markov policy means.
            period = step % horizon
            mix_a = policy[period, band, capability, type_a]
            mix_b = policy[period, band, capability, type_b]
            action_a = int(rng.choice(n_actions, p=mix_a))
            action_b = int(rng.choice(n_actions, p=mix_b))
            band = int(rng.choice(len(state_module.INTENSITY_EDGES),
                                  p=kernel[band, action_a, action_b]))
            value = _band_intensity(band)
            rows.append({
                "band": band,
                "action_a": action_a,
                "action_b": action_b,
                "dyad_id": dyad_id,
                "dyad_name": dyad_id,
                "q": 1980 * 4 + step,
                "date": f"{1980 + step // 4}-{(step % 4) * 3 + 1:02d}-01",
                "intensity": value,
                # Volume and tone are not in the shipped feature set, but
                # features.build computes them, so they are supplied on the
                # same scale the real panel would show rather than left at
                # zero — a constant column would change what standardisation
                # does to the columns that DO matter.
                "events": int(1 + value),
                "conflict": int(value > 0),
                "tone": -value / 2.0,
            })
    return rows


def stable_era(
    panel_rows: list[dict[str, Any]], *, tolerance: float = 0.5
) -> tuple[int, int]:
    """The longest run of quarters whose event volume stays within `tolerance`
    of the run's own median — a stretch the archive watched at a roughly
    constant rate.

    THE AUXILIARY STATISTIC MUST BE COMPUTED INSIDE ONE COVERAGE REGIME, and
    this is the third place that has mattered. Measured on the MENA panel, the
    `level_now` coefficients are:

        whole panel   2.110 → 2.037   decay  3.4%
        1979–1995     0.759 → 0.587   decay 22.6%
        1990–2005     0.774 → 0.623   decay 19.5%
        2006+         1.288 → 1.221   decay  5.2%

    Pooling across regimes inflates the coefficient roughly threefold and
    flattens the decay to almost nothing, because the target and the feature
    share the corpus's growth. Fitting a bargaining game to that would be
    fitting the growth of GDELT, dressed as patience — the same failure that
    turned `base_level` into a clock and put a fake all-time high in the
    structural pressure series.
    """
    if not panel_rows:
        return (0, 0)
    volume: dict[int, int] = {}
    for row in panel_rows:
        volume[int(row["q"])] = volume.get(int(row["q"]), 0) + int(row["events"])
    quarters = sorted(volume)
    if not quarters:
        return (0, 0)

    # DENSE AND STABLE, not merely stable. Scoring runs by LENGTH alone picks
    # the deep past — seventy years of uniformly sparse curated events are
    # beautifully stable and carry almost no information, and the detector
    # duly returned 1908–1990 and an auxiliary decay of 1.4%. Scoring by TOTAL
    # EVENTS instead prefers the stretch that actually watched something,
    # which is the wire era, where the same statistic reads ~20%.
    floor = float(np.median([v for v in volume.values() if v > 0]) or 0)
    best: tuple[int, int] = (quarters[0], quarters[0])
    best_mass = -1.0
    start = quarters[0]
    run: list[int] = []
    for q in quarters:
        median = float(np.median(run)) if run else 0.0
        breaks = (
            volume[q] < floor
            or (median > 0 and abs(volume[q] - median) / median > tolerance)
        )
        if breaks:
            start, run = q, [volume[q]]
            if volume[q] < floor:
                start, run = q + 1, []
            continue
        run.append(volume[q])
        mass = float(sum(run))
        if mass > best_mass:
            best, best_mass = (start, q), mass
    return best


def auxiliary_coefficients(
    panel_rows: list[dict[str, Any]], *, target: str = "deviation"
) -> np.ndarray:
    """The auxiliary statistic: `level_now`'s coefficient at each horizon.

    Fitted through core.models, unmodified. This is the same vector the
    trainer prints for the real panel, which is the only reason comparing
    them means anything. Pass rows from ONE coverage era — see `stable_era`.
    """
    columns = feature_module.shipped_columns()
    position = list(feature_module.SHIPPED_FEATURES).index("level_now")
    feature_rows = feature_module.build(panel_rows)
    out = []
    for horizon in feature_module.HORIZONS:
        x, y, _q, _d = feature_module.matrices(
            feature_rows, horizon, target=target, columns=columns
        )
        if len(y) < 50:
            out.append(np.nan)
            continue
        mean, sd = intensity_module.scaler(x)
        weights = intensity_module.fit(intensity_module.standardize(x, mean, sd), y)
        out.append(float(weights[position]))
    return np.array(out, dtype=float)


def distance(simulated: np.ndarray, observed: np.ndarray) -> float:
    """Weighted squared distance between coefficient vectors.

    NaN in the simulated vector means that horizon produced too few usable
    rows to fit — it is penalised heavily rather than skipped, because a θ
    whose equilibrium cannot generate a fittable panel has not explained the
    data, it has failed to produce any.
    """
    if np.any(np.isnan(simulated)):
        return 1e6
    return float(np.sum((simulated - observed) ** 2))


def _theta_to_payoffs(theta: np.ndarray, base: solve_module.Payoffs) -> solve_module.Payoffs:  # noqa: E501
    """Vector → Payoffs, with the ordering constraint enforced by
    construction: the resolute type's cost of fighting is always the LOWER of
    the two. Without that the optimiser can relabel the types and report a
    perfect fit with the meanings swapped."""
    discount, cost_low, spread, stake, audience = theta
    return replace(
        base,
        discount=float(np.clip(discount, 0.5, 0.99)),
        cost_resolute=float(np.clip(cost_low, 0.05, 3.0)),
        cost_irresolute=float(np.clip(cost_low + abs(spread), 0.05, 6.0)),
        stake=float(np.clip(stake, 0.1, 3.0)),
        audience=float(np.clip(audience, 0.0, 2.0)),
    )


def simulated_frequencies(rows: list[dict[str, Any]]) -> np.ndarray:
    """Action frequencies from a simulated panel, through the same counter
    the archive goes through — the whole point of comparing them."""
    return action_frequencies(
        [int(r["band"]) for r in rows],
        [(int(r["action_a"]), int(r["action_b"])) for r in rows],
    )


def objective(
    theta: np.ndarray,
    *,
    kernel: np.ndarray,
    observed: np.ndarray,
    seed: int,
    horizon: int = 4,
    base: solve_module.Payoffs | None = None,
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> float:
    """One evaluation: solve → simulate → re-fit → distance.

    The seed is FIXED across evaluations on purpose. Re-drawing it every call
    would make the objective stochastic, and a derivative-free optimiser
    cannot tell that noise from a gradient — it would wander in proportion to
    the sampling error rather than to the fit.
    """
    payoffs = _theta_to_payoffs(theta, base or solve_module.defaults_for(space))
    equilibrium = solve_module.solve(kernel, payoffs, horizon=horizon, space=space)
    rows = simulate(equilibrium, kernel, payoffs, seed=seed)
    return distance(simulated_frequencies(rows), observed)


def fit(
    kernel: np.ndarray,
    observed: np.ndarray,
    *,
    seed: int = 20260813,
    horizon: int = 4,
    max_evaluations: int = 200,
    era: tuple[int, int] | None = None,
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> dict[str, Any]:
    """Fit θ by Nelder–Mead over the five structural parameters.

    Derivative-free because the objective runs a simulation — there is no
    gradient to take, and a finite-difference one would be measuring
    simulation noise. Reports the identification caveat alongside the fit
    rather than beneath it: patience and cost BOTH flatten the decay, so a
    good fit here does not mean both were recovered.
    """
    from scipy.optimize import minimize

    # The start is the family's own default: for the ally game Fearon's start
    # would begin from a rift and the optimiser would spend its evaluations
    # climbing out.
    base = solve_module.defaults_for(space)
    start = np.array([
        base.discount, base.cost_resolute,
        base.cost_irresolute - base.cost_resolute, base.stake, base.audience,
    ])

    def evaluate(theta: np.ndarray) -> float:
        return objective(
            theta, kernel=kernel, observed=observed, seed=seed, horizon=horizon,
            base=base, space=space,
        )

    result = minimize(
        evaluate,
        start,
        method="Nelder-Mead",
        options={"maxfev": max_evaluations, "xatol": 1e-3, "fatol": 1e-4},
    )
    payoffs = _theta_to_payoffs(result.x, base)
    equilibrium = solve_module.solve(kernel, payoffs, horizon=horizon, space=space)
    simulated = simulated_frequencies(
        simulate(equilibrium, kernel, payoffs, seed=seed)
    )
    return {
        "family": space.family,
        "actions": list(space.actions),
        "payoffs": {
            "discount": round(payoffs.discount, 4),
            "cost_resolute": round(payoffs.cost_resolute, 4),
            "cost_irresolute": round(payoffs.cost_irresolute, 4),
            "stake": round(payoffs.stake, 4),
            "audience": round(payoffs.audience, 4),
        },
        "observed_frequencies": [round(float(v), 4) for v in observed],
        "simulated_frequencies": [round(float(v), 4) for v in simulated],
        "distance": round(float(result.fun), 6),
        "evaluations": int(result.nfev),
        "converged": bool(result.success),
        "seed": seed,
        "era": list(era) if era else None,
        # Stated with the fit, never under it. Patience and cost both flatten
        # the decay, so matching it does not separate them; the market-implied
        # duration in docs/game-spec.md section 3.2 is the moment intended to
        # break the tie, and until the term structure is in the panel it is
        # unavailable.
        "identification": (
            "discount and cost both flatten the decay and are NOT separately "
            "identified from it alone; treat the pair as jointly fitted"
        ),
        "method": (
            "indirect inference on ACTION FREQUENCIES by intensity band, "
            "counted the same way for the archive and for a simulated panel; "
            "Nelder-Mead over five structural parameters at a fixed seed. The "
            "ridge decay originally proposed as the moment was measured to "
            "have no leverage (46.4-50.4% across the whole parameter space) "
            "and was replaced"
        ),
    }
