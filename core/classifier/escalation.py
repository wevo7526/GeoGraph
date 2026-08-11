"""Classifier Head B: escalation. DETERMINISTIC — build-spec section 10.

Escalation is ALWAYS RELATIONAL, never an absolute label. The base score is
the Goldstein value (modern tier) or the harmonized Goldstein-equivalent
(deep tier, via crosswalks/escalation_scale_map.yaml — see `harmonize`). Each
dyad keeps an EWMA baseline of its own history, and an event is escalatory
only relative to THAT baseline: a US–Iran threat and a US–UK threat are
different events even with identical Goldstein scores.

No LLM anywhere in this file, by design.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

_CROSSWALK = (
    Path(__file__).resolve().parent.parent
    / "ontology" / "crosswalks" / "escalation_scale_map.yaml"
)

#: EWMA smoothing weight for the per-dyad baseline. 0.25 keeps roughly the
#: last half-dozen events in memory — long enough that one conciliatory
#: statement does not reset a rivalry, short enough that a détente registers.
DEFAULT_ALPHA = 0.25

#: Below this |score − baseline| the direction is `stable` — Goldstein deltas
#: under half a point are coding noise, not signal.
STABLE_BAND = 0.5


@functools.lru_cache(maxsize=1)
def _scale_map() -> dict[str, Any]:
    with open(_CROSSWALK, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def harmonize(source_scale: str, raw_value: int | str) -> float:
    """A COW hostility level or ICB severity → its Goldstein-equivalent.

    Raises KeyError-with-a-message on an unmapped value rather than guessing:
    an unmappable deep-tier record is dropped and counted, never smoothed.
    """
    if source_scale == "goldstein":
        return float(raw_value)
    table = _scale_map().get(source_scale)
    if table is None:
        raise KeyError(
            f"source_scale {source_scale!r} has no crosswalk in {_CROSSWALK.name}. "
            "Valid: goldstein, cow_hostility, icb_severity."
        )
    entry = table.get(str(raw_value))
    if entry is None:
        raise KeyError(
            f"{source_scale} value {raw_value!r} is not in {_CROSSWALK.name} — "
            f"mapped values: {sorted(table)}."
        )
    return float(entry["goldstein_equivalent"])


def update_baseline(baseline: float | None, score: float, alpha: float = DEFAULT_ALPHA) -> float:
    """One EWMA step. A dyad's first event IS its baseline — there is no
    global prior to fall back to, because a global normal is exactly what
    relational escalation refuses to assume."""
    if baseline is None:
        return score
    return alpha * score + (1.0 - alpha) * baseline


def classify(score: float, baseline: float | None) -> dict[str, Any]:
    """The Head B output for one event against one dyad baseline.

    Escalation is movement toward conflict: a score DROP below baseline
    (more negative Goldstein) escalates, a rise de-escalates.
    """
    base = score if baseline is None else baseline
    delta = score - base
    magnitude = abs(delta)
    if magnitude < STABLE_BAND:
        direction = "stable"
    elif delta < 0:
        direction = "escalating"
    else:
        direction = "deescalating"
    return {
        "escalation_baseline": base,
        "escalation_direction": direction,
        "escalation_magnitude": magnitude,
    }


class DyadTracker:
    """Per-dyad EWMA state for a chronological pass over events.

    Feed events IN TIME ORDER — the baseline is history, and history has an
    order. The pipeline sorts by event_time before calling this; ISO-8601
    strings sort correctly at every resolution the archive holds.
    """

    def __init__(self, alpha: float = DEFAULT_ALPHA) -> None:
        self.alpha = alpha
        self._baselines: dict[str, float] = {}

    def observe(self, dyad_id: str, score: float) -> dict[str, Any]:
        """Classify against the dyad's baseline, then fold the event in."""
        baseline = self._baselines.get(dyad_id)
        result = classify(score, baseline)
        self._baselines[dyad_id] = update_baseline(baseline, score, self.alpha)
        return result

    def baseline(self, dyad_id: str) -> float | None:
        return self._baselines.get(dyad_id)
