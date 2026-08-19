"""The reasoning agent — build-spec sections 2, 3 and 13.

OpenAI over the archive's deterministic context: reads the present against
the graph's deep memory, narrates analogues the structural engine retrieved,
and drafts assessments AROUND the frozen numbers. DIVISION OF LABOUR, locked
(section 17): the agent REASONS and NARRATES; the deterministic core
MEASURES. Every number in an effect, a metric, or the deterministic part of
a forecast exists before the agent sees it. The agent's value is the
argument, not the arithmetic.

Realist strategic logic (Kissinger, bargaining and power-transition
traditions): actors are modeled by interests, resolve, and capability — the
AttributeEstimate layer is the agent's state of belief about those.

DARK BY DEFAULT. Requires OPENAI_API_KEY (and the `reasoning` extra);
without either, `assess` raises AgentUnavailable naming exactly what is
missing, and the API surfaces that as an honest 503 — the deterministic
half of the reasoning page runs regardless.
"""

from __future__ import annotations

import json
import os
from typing import Any

#: Override with GEOGRAPH_AGENT_MODEL. 4.1 is the default deliberately:
#: the agent narrates around numbers it is handed — it needs judgment, not
#: a reasoning-model loop. Intel opens a reading on arrival; follow-ups
#: and the corner desk are the reader's, not a poll.
_DEFAULT_MODEL = "gpt-4.1"

#: Prior turns kept on a follow-up. The briefing is re-sent every call, so
#: this is only the argument, not a second copy of the numbers.
_HISTORY_CAP = 16
_TURN_CHARS = 4000

_SYSTEM = (
    "You are GeoGraph's desk: an applied-history analyst over a "
    "1972–present geopolitical archive with a measured market-transmission "
    "layer. Intel is your office — the desk's reading sits above the wire "
    "itself. A reader may also summon you from any other desk. You reason "
    "in the realist tradition — interests, resolve, capability, bargaining "
    "position — and by disciplined analogy.\n\n"
    "SHAPE OF THE READING. Short paragraphs separated by a blank line. "
    "The first sentence is the claim. Do not use markdown headings. Bold "
    "only names that already appear in the briefing. A short list is fine "
    "if the items are already in the context; do not pad.\n\n"
    "The context is a BRIEFING assembled from layers the archive already "
    "computed: recent wire departures from each pair's own baseline, a live "
    "overlay if this process has one, the persisted region-game ranking, "
    "packed market headlines and transmission skill, globe coverage "
    "(including actors that cannot be placed), and frozen forecasts. A "
    "`reader` block, when present, says which desk they are on and which "
    "pair, market or event they have open. Speak to that first. Reason "
    "ACROSS the layers together. Do not quote estimator field names.\n\n"
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
    "implications come up.\n"
    "- Follow-ups continue the same argument. You still may not invent a "
    "figure that is not in this turn's context."
)


class AgentUnavailable(RuntimeError):
    """The agent cannot run here and now; the message names the missing piece."""


def history_messages(history: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Prior user/assistant turns, capped. Roles other than those two drop."""
    cleaned: list[dict[str, str]] = []
    for turn in history or []:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip().lower()
        content = str(turn.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        cleaned.append({"role": role, "content": content[:_TURN_CHARS]})
    return cleaned[-_HISTORY_CAP:]


def conversation_messages(
    question: str,
    *,
    region_pack: str,
    context: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """The exact prompt: system, prior turns, then this question + briefing."""
    reader = context.get("reader") if isinstance(context.get("reader"), dict) else None
    where = ""
    if reader:
        surface = reader.get("surface")
        if surface:
            where = f" The reader is on the {surface} desk."
        looking = reader.get("looking_at")
        if isinstance(looking, dict) and looking:
            where += f" They have open: {json.dumps(looking, default=str)}."
    return [
        {"role": "system", "content": _SYSTEM},
        *history_messages(history),
        {
            "role": "user",
            "content": (
                f"Question, through the {region_pack} lens: {question}.{where}\n\n"
                "Deterministic context from the archive (every number here "
                "was measured or counted before you saw it):\n"
                f"{json.dumps(context, indent=2, default=str)}"
            ),
        },
    ]


def assess(
    question: str,
    *,
    region_pack: str,
    context: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One reasoned assessment over DETERMINISTIC context the caller
    assembled (the intel briefing: wire, region games, markets, globe,
    frozen forecasts — every item carrying its node id). Follow-ups pass
    prior turns in `history`; the briefing is still assembled this call.
    The agent adds the argument; the context already holds all the numbers."""
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise AgentUnavailable(
            "OPENAI_API_KEY is not set — the reasoning agent is dark. "
            "The deterministic layer (the what-if engine, frozen forecasts, "
            "the paper backtest) runs without it; narrated assessments and "
            "generated case studies need the key."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AgentUnavailable(
            'the `openai` package is not installed — pip install -e '
            '".[reasoning]"'
        ) from exc

    model = os.getenv("GEOGRAPH_AGENT_MODEL", "").strip() or _DEFAULT_MODEL
    client = OpenAI(api_key=key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=1500,
        messages=conversation_messages(
            question, region_pack=region_pack, context=context, history=history,
        ),
    )
    choice = response.choices[0] if response.choices else None
    message = choice.message if choice is not None else None
    text = (getattr(message, "content", None) or "") if message is not None else ""
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


#: Case-study argument — same §17 rules, a narrower job: the study already
#: stated the numbers; this is the desk reading them, not a second summary.
_STUDY_SYSTEM = (
    "You are GeoGraph's desk writing the argument beside a measured case "
    "study. The payload already contains every figure; you interpret. "
    "Short paragraphs separated by a blank line. The first sentence is "
    "the claim. Do not use markdown headings. Bold only names that "
    "already appear in the study.\n\n"
    "HARD RULES (section 17):\n"
    "- Never originate a market number, a t-statistic, a p-value, or a "
    "likelihood. Cite a node id in square brackets when you mention a "
    "figure.\n"
    "- Analogues, if present, were retrieved by the structural engine; "
    "do not re-rank them.\n"
    "- If the study is not_yet_measured, say that the spine has no "
    "numbers yet. Do not invent effects.\n"
    "- Nothing you write is financial advice."
)


def study_context(study: dict[str, Any]) -> dict[str, Any]:
    """The numbers the desk is allowed to see — compact, already measured."""
    episodes = []
    for episode in study.get("episodes") or []:
        if not isinstance(episode, dict):
            continue
        effects = [
            row for row in (episode.get("effects") or [])
            if isinstance(row, dict) and row.get("abnormal_return") is not None
        ]
        effects.sort(key=lambda row: abs(float(row.get("abnormal_return") or 0.0)), reverse=True)
        episodes.append({
            "node_id": episode.get("node_id"),
            "name": episode.get("name"),
            "event_time": episode.get("event_time"),
            "cameo_code": episode.get("cameo_code"),
            "quad_class": episode.get("quad_class"),
            "goldstein": episode.get("goldstein"),
            "escalation_direction": episode.get("escalation_direction"),
            "escalation_magnitude": episode.get("escalation_magnitude"),
            "escalation_baseline": episode.get("escalation_baseline"),
            "missing": episode.get("missing"),
            "effects": [
                {
                    "ticker": row.get("ticker"),
                    "market": row.get("market"),
                    "window": row.get("window"),
                    "abnormal_return": row.get("abnormal_return"),
                    "t_stat": row.get("t_stat"),
                    "p_value": row.get("p_value"),
                    "first_mover": row.get("first_mover"),
                    "overlapping": row.get("overlapping"),
                }
                for row in effects[:4]
            ],
        })
    return {
        "slug": study.get("slug"),
        "title": study.get("title"),
        "dek": study.get("dek"),
        "summary": study.get("summary"),
        "status": study.get("status"),
        "measured": study.get("measured"),
        "pack": study.get("pack"),
        "episodes": episodes,
        "note": (
            "Every figure here is the transmission engine's. Cite node_id "
            "when you mention a number. Do not originate one."
        ),
    }


def narrate_study(study: dict[str, Any]) -> dict[str, Any]:
    """An argument over a case-study payload. Same darkness rule as assess."""
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise AgentUnavailable(
            "OPENAI_API_KEY is not set — generated case-study readings are "
            "dark. The pack's prose and the measured tables still run."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AgentUnavailable(
            'the `openai` package is not installed — pip install -e '
            '".[reasoning]"'
        ) from exc

    model = os.getenv("GEOGRAPH_AGENT_MODEL", "").strip() or _DEFAULT_MODEL
    compact = study_context(study)
    client = OpenAI(api_key=key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=900,
        messages=[
            {"role": "system", "content": _STUDY_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Write the reading of this measured episode.\n\n"
                    f"{json.dumps(compact, indent=2, default=str)}"
                ),
            },
        ],
    )
    choice = response.choices[0] if response.choices else None
    message = choice.message if choice is not None else None
    text = (getattr(message, "content", None) or "") if message is not None else ""
    return {
        "slug": compact.get("slug"),
        "title": compact.get("title"),
        "desk_reading": text,
        "model": model,
        "context": compact,
        "method": (
            "LLM argument over the study's own measured fields (section 17): "
            "the transmission engine originated every figure."
        ),
    }
