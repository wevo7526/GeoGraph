"""Classifier Head A: event typing — build-spec section 10.

Three paths into one CAMEO vocabulary, ONE of which involves a model:

1. GDELT firehose: trusted directly — it arrives CAMEO-coded and
   Goldstein-scored. No classification happens here at all.
2. Deep tier (COW, ICB): DETERMINISTIC crosswalk through
   crosswalks/cow_to_cameo.yaml — `map_deep_event` below. Never the LLM: a
   structured 1911 dispute record does not need a language model to become a
   CAMEO code, and determinism means the whole deep tier re-derives
   identically on every run.
3. Modern non-GDELT text (marquee spine narratives, live episodes): Claude
   with the CAMEO codebook → {cameo_code, actor1, actor2, quad_class,
   confidence}. The LLM types the event; it NEVER originates a number that
   lands in an effect or a metric (build-spec section 17).
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

_CROSSWALK = (
    Path(__file__).resolve().parent.parent / "ontology" / "crosswalks" / "cow_to_cameo.yaml"
)


@functools.lru_cache(maxsize=1)
def _crosswalk() -> dict[str, Any]:
    with open(_CROSSWALK, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def map_deep_event(dataset: str, category: int | str) -> dict[str, Any] | None:
    """Path 2: a COW/ICB category → CAMEO-equivalent, deterministically.

    `dataset` is a top-level key of cow_to_cameo.yaml (`cow_mid_hostility`,
    `icb_crisis`). Returns None when the mapping says "no event" (MID
    hostility 1); raises on an UNMAPPED value — dropped-and-counted beats
    silently guessed.
    """
    table = _crosswalk().get(dataset)
    if table is None:
        raise KeyError(
            f"dataset {dataset!r} has no crosswalk in {_CROSSWALK.name}. "
            f"Top-level keys: {sorted(_crosswalk())}"
        )
    entry = table.get(str(category))
    if entry is None:
        raise KeyError(
            f"{dataset} category {category!r} is not mapped in {_CROSSWALK.name}."
        )
    if entry.get("cameo") is None:
        return None
    return dict(entry)


def code_text_event(text: str, *, api_key: str) -> dict[str, Any]:
    """Path 3: modern non-GDELT text → CAMEO via Claude.

    Returns {cameo_code, actor1, actor2, quad_class, confidence}. Phase 0
    wires this for the marquee spine; until then the signature is the
    contract. Requires the `reasoning` extra and ANTHROPIC_API_KEY.
    """
    raise NotImplementedError(
        "Phase 0 (marquee spine coding) — see docs/build-spec.md section 10"
    )
