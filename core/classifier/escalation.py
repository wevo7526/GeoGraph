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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

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
        return cast(dict[str, Any], yaml.safe_load(fh))


def harmonize(source_scale: str, raw_value: float | str) -> float:
    """A COW hostility level or ICB severity → its Goldstein-equivalent.
    A goldstein-scale value passes through unchanged, floats included — a
    Goldstein score IS a float; the categorical scales arrive as ints or
    strings.

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

    def seed(self, dyad_id: str, baseline: float) -> None:
        """Open a dyad at a known EWMA — the snapshot's last fold.

        Without this, a live 15-minute file would hit Head B's first-event
        rule and every new pair would be `stable` at magnitude 0, which is
        exactly not a departure from history.
        """
        self._baselines[str(dyad_id)] = float(baseline)

    def observe(self, dyad_id: str, score: float) -> dict[str, Any]:
        """Classify against the dyad's baseline, then fold the event in."""
        baseline = self._baselines.get(dyad_id)
        result = classify(score, baseline)
        self._baselines[dyad_id] = update_baseline(baseline, score, self.alpha)
        return result

    def baseline(self, dyad_id: str) -> float | None:
        return self._baselines.get(dyad_id)


def _bare(actor_id: str) -> str:
    """`actor:cow-630` → `cow-630`, so a dyad id reads as the pair it is."""
    return actor_id.split(":", 1)[-1]


def dyad_id(actor_a: str, actor_b: str) -> str:
    """The canonical id for an actor pair — SORTED, and therefore UNORDERED.

    A rivalry is ONE relationship whichever side acted last. Iran striking
    Israel in April 2024 and Israel striking Iran in June 2025 are the same
    dyad escalating; keying the baseline by direction would split that history
    in half and hide the trajectory that makes the twelve-day war legible. The
    ontology's `actor_a_id`/`actor_b_id` hold the pair in sorted order so the
    id is STABLE — they do not encode a direction. Direction lives where it
    belongs, on INITIATED_BY and DIRECTED_AT.
    """
    a, b = sorted((actor_a, actor_b))
    return f"dyad:{_bare(a)}--{_bare(b)}"


@dataclass(frozen=True)
class Coding:
    """One Head B pass: escalation slots per event, EWMA state per dyad.

    `events` rows carry `node_id`, `dyad_id` and the three escalation slots,
    ready to merge onto Event nodes. `dyads` rows are Dyad nodes carrying the
    baseline as of the last event folded in.
    """

    events: list[dict[str, Any]]
    dyads: list[dict[str, Any]]


def code_events(
    events: Iterable[Mapping[str, Any]],
    *,
    names: Mapping[str, str] | None = None,
    alpha: float = DEFAULT_ALPHA,
) -> Coding:
    """Head B over a whole event stream. PURE — no graph, no I/O, no model.

    Each event needs `node_id`, `event_time`, `goldstein`, and its two actors
    as `actor_a`/`actor_b` (a one-sided or internal event passes the same actor
    twice — an internal rupture still has a relationship with itself, and the
    Iranian and Egyptian revolutions are exactly that).

    THE SORT IS THE ALGORITHM. A baseline is history, so events are folded in
    chronologically; ties break on node_id so a re-run of the same input
    produces the same baselines rather than a coin flip. ISO-8601 strings sort
    correctly at every resolution the archive holds, which is why dates are
    stored as strings.
    """
    names = names or {}
    tracker = DyadTracker(alpha=alpha)
    coded: list[dict[str, Any]] = []
    dyads: dict[str, dict[str, Any]] = {}

    ordered = sorted(events, key=lambda e: (str(e["event_time"]), str(e["node_id"])))
    for event in ordered:
        score = event.get("goldstein")
        if score is None:
            raise ValueError(
                f"{event['node_id']} has no goldstein score, so Head B has nothing to "
                "measure. Score it from its CAMEO code first "
                "(classifier.typing.goldstein_for) — escalation is never guessed."
            )
        actor_a = str(event["actor_a"])
        actor_b = str(event["actor_b"] or actor_a)
        did = dyad_id(actor_a, actor_b)
        result = tracker.observe(did, float(score))
        coded.append({"node_id": event["node_id"], "dyad_id": did, **result})

        pair = sorted((actor_a, actor_b))
        label = (
            f"{names.get(pair[0], _bare(pair[0]))} (internal)"
            if pair[0] == pair[1]
            else f"{names.get(pair[0], _bare(pair[0]))} – {names.get(pair[1], _bare(pair[1]))}"
        )
        dyads[did] = {
            "node_id": did,
            "name": label,
            "actor_a_id": pair[0],
            "actor_b_id": pair[1],
            # The baseline AFTER this event — the dyad node holds current state,
            # while each Event keeps the baseline it was measured against.
            "ewma_baseline": tracker.baseline(did),
            "ewma_as_of": str(event["event_time"]),
        }

    return Coding(events=coded, dyads=list(dyads.values()))
