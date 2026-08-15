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

This module also owns the CAMEO codebook lookups — `goldstein_for`,
`quad_class_for`, `label_for` over crosswalks/cameo_goldstein.yaml. Both
values are FUNCTIONS OF THE CODE, so whichever of the three paths produced
the code, the score and the quad class follow from it deterministically. Head
B (escalation.py) consumes the Goldstein value; it never looks up a code.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, Literal, cast

import yaml

_CROSSWALKS = Path(__file__).resolve().parent.parent / "ontology" / "crosswalks"
_CROSSWALK = _CROSSWALKS / "cow_to_cameo.yaml"
_CODEBOOK = _CROSSWALKS / "cameo_goldstein.yaml"


@functools.lru_cache(maxsize=1)
def _crosswalk() -> dict[str, Any]:
    with open(_CROSSWALK, encoding="utf-8") as fh:
        return cast(dict[str, Any], yaml.safe_load(fh))


@functools.lru_cache(maxsize=1)
def _codebook() -> dict[str, Any]:
    with open(_CODEBOOK, encoding="utf-8") as fh:
        return cast(dict[str, Any], yaml.safe_load(fh))


@functools.lru_cache(maxsize=1)
def _quad_by_root() -> dict[str, str]:
    """Root code → quad class, inverted from the partition in the codebook."""
    return {
        root: quad
        for quad, roots in _codebook()["quad_class_by_root"].items()
        for root in roots
    }


def _lineage(cameo_code: str | int) -> list[str]:
    """A code and its ancestors, most specific first: 1451 → 145 → 14.

    CAMEO is hierarchical and so is the lookup. A code the codebook has never
    seen still resolves through its root, so the archive is not blocked on
    enumerating all ~300 codes — but a code with no valid ROOT is not CAMEO,
    and that raises.
    """
    code = str(cameo_code).strip()
    if not code.isdigit() or not 2 <= len(code) <= 4:
        raise ValueError(
            f"{cameo_code!r} is not a CAMEO code. Codes are 2-4 digit strings "
            'with leading zeros preserved ("057", "190", "1451").'
        )
    out = [code]
    if len(code) == 4:
        out.append(code[:3])
    if len(code) > 2:
        out.append(code[:2])
    return out


def goldstein_for(cameo_code: str | int) -> float:
    """The Goldstein weight for a CAMEO code — the Head B baseline input.

    NOT a measurement and not a market number: a conflict-cooperation weight
    from a published codebook, used to compare events to each other.
    """
    codes = _codebook()["codes"]
    for candidate in _lineage(cameo_code):
        entry = codes.get(candidate)
        if entry is not None and entry.get("goldstein") is not None:
            return float(entry["goldstein"])
    raise KeyError(
        f"CAMEO code {cameo_code!r} has no Goldstein value in {_CODEBOOK.name}, "
        f"and neither does its root. Roots present: {sorted(_quad_by_root())}."
    )


def quad_class_for(cameo_code: str | int) -> str:
    """The QuadClass a CAMEO code implies. Derived, never stored twice."""
    root = _lineage(cameo_code)[-1]
    quad = _quad_by_root().get(root)
    if quad is None:
        raise KeyError(
            f"CAMEO root {root!r} (from {cameo_code!r}) is not in the quad-class "
            f"partition in {_CODEBOOK.name}. Valid roots: {sorted(_quad_by_root())}."
        )
    return quad


def label_for(cameo_code: str | int) -> str:
    """The codebook's own words for a code, at whatever specificity resolves."""
    codes = _codebook()["codes"]
    for candidate in _lineage(cameo_code):
        entry = codes.get(candidate)
        if entry is not None and entry.get("label"):
            return str(entry["label"])
    raise KeyError(f"CAMEO code {cameo_code!r} has no label in {_CODEBOOK.name}.")


def codebook_entries() -> list[dict[str, Any]]:
    """Every scorable code the codebook carries, with its derived values —
    the what-if composer's vocabulary. Derived from the YAML on each call
    path (the YAML itself is lru_cached), never stored a second time."""
    codes = _codebook()["codes"]
    out: list[dict[str, Any]] = []
    for code in sorted(codes):
        entry = codes[code]
        if entry.get("goldstein") is None:
            continue
        out.append({
            "code": code,
            "label": str(entry.get("label", "")),
            "goldstein": float(entry["goldstein"]),
            "quad_class": quad_class_for(code),
        })
    return out


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


class ClassifierError(RuntimeError):
    """Head A could not type an event. The message names why."""


#: Head A's model. The coder is the cheapest call in the system and the one
#: whose mistakes are hardest to see downstream — a mis-coded event silently
#: corrupts its dyad's whole escalation baseline — so it does not get
#: downgraded to save fractions of a cent.
CODER_MODEL = "claude-opus-5"

#: Reasoning effort. High because the archive is small and curated: the total
#: spend is trivial and the cost of a wrong code is a corrupted baseline.
#: Typed as the SDK's closed effort vocabulary — a bare str stops matching the
#: TypedDict the moment the SDK regenerates its overloads.
CODER_EFFORT: Literal["high"] = "high"

_INSTRUCTIONS = """\
You assign CAMEO event codes to descriptions of geopolitical events, for a \
historical archive spanning 1905 to the present.

Return the single code that best matches the PRINCIPAL ACTION the text \
describes — what was done, not its significance or its consequences. Choose \
from the codebook below and nowhere else; a code outside it cannot be scored \
by the archive's deterministic layer and will be rejected.

Name the initiator as actor1 and the target as actor2, using the names as the \
text gives them. When an event is internal to one actor (a revolution, a \
domestic protest), name that actor for both.

Set confidence to reflect how well the action fits the code you chose — low \
when the text is ambiguous about who acted, or when the action spans several \
codes. Say what made the coding hard in `reasoning`, briefly.

The codebook (code — meaning), most specific codes last:
"""

_CODING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cameo_code": {
            "type": "string",
            "description": 'A code from the codebook, as a string with leading zeros ("057").',
        },
        "actor1": {"type": "string", "description": "The initiator, as the text names it."},
        "actor2": {"type": "string", "description": "The target, as the text names it."},
        "confidence": {
            "type": "number",
            "description": "0.0-1.0: how well the principal action fits the chosen code.",
        },
        "reasoning": {"type": "string", "description": "One or two sentences."},
    },
    "required": ["cameo_code", "actor1", "actor2", "confidence", "reasoning"],
    "additionalProperties": False,
}


@functools.lru_cache(maxsize=1)
def _instructions() -> str:
    """The codebook, rendered as the model's closed vocabulary.

    Building the prompt FROM the crosswalk is the point: Head A can only
    return a code the deterministic layer already knows how to score, so it
    can never hand Head B an event with no Goldstein value.
    """
    codes = _codebook()["codes"]
    lines = [f"  {code} — {entry['label']}" for code, entry in sorted(codes.items())]
    return _INSTRUCTIONS + "\n".join(lines)


def code_text_event(
    text: str, *, api_key: str, model: str = CODER_MODEL
) -> dict[str, Any]:
    """Path 3: modern non-GDELT text → CAMEO via Claude.

    Returns {cameo_code, actor1, actor2, quad_class, goldstein, confidence,
    reasoning}. Requires the `reasoning` extra and ANTHROPIC_API_KEY.

    THE MODEL RETURNS A CODE, NOT A CLASSIFICATION. `quad_class` and
    `goldstein` in the result are DERIVED from the returned code by the
    functions above — the same path a GDELT row or a COW record takes. The
    model exercises the one judgment a codebook cannot automate (which action
    a sentence describes) and originates none of the numbers (build-spec §17);
    `confidence` describes the coding itself and may never reach an effect.

    An unresolvable code raises rather than being coerced to something
    plausible: a dropped event is countable, a wrong one is not.
    """
    try:
        import anthropic
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on extras
        raise ClassifierError(
            "Head A needs the Anthropic SDK: pip install -e '.[reasoning]'. The "
            "deterministic layers (crosswalks, transmission, analytics) do not."
        ) from exc

    client = anthropic.Anthropic(api_key=api_key)
    response = client.beta.messages.create(
        model=model,
        max_tokens=4096,
        # Opus 5's classifiers can decline a request; without a fallback a
        # refusal would just stop. Historical political violence is exactly
        # the benign-but-adjacent material that occasionally trips them.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={
            "effort": CODER_EFFORT,
            "format": {"type": "json_schema", "schema": _CODING_SCHEMA},
        },
        # The codebook is identical on every call and the events are coded in
        # batches, so it is worth caching rather than re-sending.
        system=[
            {
                "type": "text",
                "text": _instructions(),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": text}],
    )

    # stop_reason BEFORE content: a refusal returns 200 with empty or partial
    # content, so indexing content first would raise something misleading.
    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "category", None)
        raise ClassifierError(
            f"the coder declined this text (category: {detail}) and the fallback "
            "model did not answer either. Code this event by hand into the pack's "
            "marquee spine rather than leaving it uncoded."
        )

    raw = next((b.text for b in response.content if b.type == "text"), "")
    if not raw:
        raise ClassifierError(
            f"the coder returned no text (stop_reason: {response.stop_reason})."
        )
    result: dict[str, Any] = json.loads(raw)

    code = str(result["cameo_code"]).strip()
    try:
        goldstein = goldstein_for(code)
        quad_class = quad_class_for(code)
    except (KeyError, ValueError) as exc:
        raise ClassifierError(
            f"the coder returned {code!r}, which is not in {_CODEBOOK.name} — {exc}"
        ) from exc

    return {
        "cameo_code": code,
        "actor1": result["actor1"],
        "actor2": result["actor2"],
        "quad_class": quad_class,
        "goldstein": goldstein,
        "confidence": float(result["confidence"]),
        "reasoning": result["reasoning"],
    }
