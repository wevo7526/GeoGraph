"""The reasoning agent — build-spec sections 2, 3 and 13.

Claude over the archive's deterministic context: reads the present against
the graph's deep memory, narrates analogues the structural engine retrieved,
and drafts assessments AROUND the frozen numbers. DIVISION OF LABOUR, locked
(section 17): the agent REASONS and NARRATES; the deterministic core
MEASURES. Every number in an effect, a metric, or the deterministic part of
a forecast exists before the agent sees it. The agent's value is the
argument, not the arithmetic.

Realist strategic logic (Kissinger, bargaining and power-transition
traditions): actors are modeled by interests, resolve, and capability — the
AttributeEstimate layer is the agent's state of belief about those.

DARK BY DEFAULT. Requires ANTHROPIC_API_KEY (and the `reasoning` extra);
without either, `assess` raises AgentUnavailable naming exactly what is
missing, and the API surfaces that as an honest 503 — the deterministic
half of the reasoning page runs regardless.
"""

from __future__ import annotations

import json
import os
from typing import Any

#: Override with GEOGRAPH_AGENT_MODEL. Sonnet is the default deliberately:
#: the agent narrates around numbers it is handed — it needs judgment, not
#: frontier-scale reasoning. The Situation page asks on a click, not on
#: every view; credits are the reader's, not a page-load cost.
_DEFAULT_MODEL = "claude-sonnet-5"

_SYSTEM = (
    "You are GeoGraph's reasoning agent: an applied-history analyst over a "
    "1972–present geopolitical archive with a measured market-transmission "
    "layer. You reason in the realist tradition — interests, resolve, "
    "capability, bargaining position — and by disciplined analogy.\n\n"
    "The context is a SITUATION BRIEFING assembled from layers the archive "
    "already computed: recent wire departures from each pair's own baseline, "
    "a live overlay if this process has one, the persisted region-game "
    "ranking, packed market headlines and transmission skill, globe "
    "coverage (including actors that cannot be placed), and frozen "
    "forecasts. Reason ACROSS those layers together. Do not treat one "
    "layer as the whole situation. Do not quote estimator field names.\n\n"
    "HARD RULES (the archive's section 17, non-negotiable):\n"
    "- You NARRATE and ARGUE; the deterministic core MEASURES. Never "
    "originate a market number, a likelihood, a base rate, or a "
    "measurement. Every number you mention must come from the provided "
    "context, cited by its node id or dyad id in square brackets.\n"
    "- Analogues were retrieved by a deterministic admissibility-gated "
    "engine; you may interpret them, never re-rank or invent them.\n"
    "- State uncertainty plainly. Distinguish what the archive measured "
    "from what you are inferring.\n"
    "- Long-horizon claims are pressure over windows, never dated point "
    "predictions.\n"
    "- Nothing you write is financial advice, and you say so when market "
    "implications come up."
)


class AgentUnavailable(RuntimeError):
    """The agent cannot run here and now; the message names the missing piece."""


def assess(
    question: str,
    *,
    region_pack: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """One reasoned assessment over DETERMINISTIC context the caller
    assembled (the situation briefing: wire, region games, markets,
    globe, frozen forecasts — every item carrying its node id). The agent
    adds the argument; the context already holds all the numbers."""
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise AgentUnavailable(
            "ANTHROPIC_API_KEY is not set — the reasoning agent is dark. "
            "The deterministic layer (the what-if engine, frozen forecasts, "
            "the paper backtest) runs without it; narrated assessments and "
            "generated case studies need the key."
        )
    try:
        import anthropic
    except ImportError as exc:
        raise AgentUnavailable(
            'the `anthropic` package is not installed — pip install -e '
            '".[reasoning]"'
        ) from exc

    model = os.getenv("GEOGRAPH_AGENT_MODEL", "").strip() or _DEFAULT_MODEL
    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model=model,
        max_tokens=1500,
        system=_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Question, through the {region_pack} lens: {question}\n\n"
                "Deterministic context from the archive (every number here "
                "was measured or counted before you saw it):\n"
                f"{json.dumps(context, indent=2, default=str)}"
            ),
        }],
    )
    text = "".join(
        block.text for block in response.content if block.type == "text"
    )
    return {
        "question": question,
        "region_pack": region_pack,
        "assessment": text,
        "model": model,
        "method": (
            "LLM narration over deterministic context only (section 17): "
            "numbers measured/counted upstream, cited by node id; the agent "
            "argued, it did not compute."
        ),
    }
