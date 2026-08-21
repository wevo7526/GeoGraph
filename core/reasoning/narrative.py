"""AI-composed narrative for the analytic surfaces — History / Work / Forecast.

The desk's diagnosis and forecast prose, generalized from `agent.narrate_study`
to the markets, game-theory and relationship surfaces, and made SAFE:

  1. EVIDENCE PACKET — a compact, capped, deterministic JSON of only the
     figures a surface's prose may cite (the generalization of
     `agent.study_context`). Numbers are counted/measured upstream; the packet
     is the only thing the model ever sees.
  2. THREE BLOCKS — every surface is narrated as History (what the record
     shows), Work (what the system did to it, and how far to trust it) and
     Forecast (where it points next). Short paragraphs; the first sentence is
     the claim.
  3. NUMERIC-PROVENANCE VALIDATION — `validate()` refuses prose that states a
     number the packet does not contain. This is what makes "the AI never
     originates a number" a runtime guarantee rather than a note. A failure is
     not an error: the caller falls back to the deterministic templates.

DARK BY DEFAULT, like the rest of the reasoning layer: without OPENAI_API_KEY
(and the `reasoning` extra) `compose` raises AgentUnavailable, the `narrate`
job no-ops, and every surface serves its deterministic prose. Prose is
generated server-side by the convergence loop and persisted per snapshot
(core/panel/pg_store.py `narratives`), so a page load never waits on a model.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from typing import Any

from core.reasoning.agent import AgentUnavailable

#: Same default and override as the desk agent — narration, not a reasoning loop.
_DEFAULT_MODEL = "gpt-4.1"

#: The surfaces this module narrates. Region-scoped surfaces carry subject "".
SURFACES = ("markets", "game_region", "game_dyad", "relationship")

_SYSTEM = (
    "You are GeoGraph's desk: an applied-history analyst over a 1972-present "
    "geopolitical archive with a measured market-transmission layer and solved "
    "games. You are writing the standing read for ONE surface of the platform, "
    "in exactly three blocks:\n\n"
    "  HISTORY  - what the measured record shows for this subject: the events, "
    "the pair's own baseline, precedent, the medians. What HAPPENED.\n"
    "  WORK     - what the system DID to it: the event study, the kernel "
    "counted from the archive, the solve, the calibration/backtest, the "
    "coverage - and, plainly, how far to trust it. A diagnosis, not a tile "
    "dump.\n"
    "  FORECAST - where it POINTS NEXT: the solved-game courses, the forecast "
    "modes, the forward direction - pressure over windows, never a dated "
    "point prediction.\n\n"
    "SHAPE. Each block is 2-4 short paragraphs separated by a blank line. The "
    "first sentence of each is the claim. No markdown headings inside a block. "
    "Bold only names that already appear in the packet.\n\n"
    "HARD RULES (the archive's section 17, non-negotiable):\n"
    "- You NARRATE and DIAGNOSE; the deterministic core MEASURED. Every "
    "number you write - a percent, a count, a likelihood, a t-statistic, a "
    "date - MUST appear in the provided packet. Never originate, round beyond, "
    "or compute a figure. If you need a number the packet does not have, write "
    "around it in words.\n"
    "- Distinguish what was measured from what you are inferring. State "
    "uncertainty plainly.\n"
    "- Rankings and analogues were produced by the deterministic engine; "
    "interpret them, never re-rank or invent them.\n"
    "- Nothing you write is financial advice; say so when market implications "
    "come up.\n\n"
    "Return STRICT JSON: {\"history\": \"...\", \"work\": \"...\", "
    "\"forecast\": \"...\"}. Paragraphs are separated by \\n\\n inside each "
    "string. Do not add other keys."
)

#: Per-surface framing appended to the user message, naming what the three
#: blocks are ABOUT for that surface so the model does not guess.
_BRIEF: dict[str, str] = {
    "markets": (
        "Surface: MARKETS - what this region's geopolitics has done to prices. "
        "History: which markets moved, how far, and the record's largest moves. "
        "Work: the event study and its coverage, the transmission skill, the "
        "paper book. Forecast: where the solved games point prices next and how "
        "long the curve expects a crisis to last."
    ),
    "game_region": (
        "Surface: GAME THEORY, region map. History: who has been pressing whom "
        "(the measured coercive record and standings). Work: the solve - how "
        "many pairs, the kernel coverage, the nash-gap audit, how far to trust "
        "it. Forecast: where the games point across the region (escalatory and "
        "calming courses)."
    ),
    "game_dyad": (
        "Surface: GAME THEORY, one pair's solved game. History: what this pair "
        "is (standing, posture) and its opening state. Work: the stage game, "
        "the kernel, the pricing evidence, how far to trust the solve. "
        "Forecast: the most likely course and where the fan points."
    ),
    "relationship": (
        "Surface: RELATIONSHIP, one pair, answer-first. History: where it has "
        "been - its quarterly trajectory against its own baseline, precedent, "
        "what markets did. Work: how well the system has called this before "
        "(calibration, the paper book, coverage). Forecast: the call - where "
        "tension is heading and the market move associated with it."
    ),
}


# ── evidence packets ────────────────────────────────────────────────────────
#
# Each builder takes an already-assembled deterministic payload and returns the
# compact, capped subset a surface's prose is allowed to cite. Robust to missing
# keys: an absent field is simply not offered to the model.


def _num(value: Any) -> Any:
    """Keep only finite numbers; everything else passes through untouched."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value if math.isfinite(float(value)) else None
    return value


def _cap(rows: Any, n: int) -> list[Any]:
    return list(rows or [])[:n]


def markets_packet(story: dict[str, Any]) -> dict[str, Any]:
    markets = story.get("markets") or []
    ranked = sorted(
        (m for m in markets if isinstance(m, dict)),
        key=lambda m: abs(float(((m.get("headline") or {}).get("median")) or 0.0)),
        reverse=True,
    )
    top_markets = [
        {
            "market": m.get("name"),
            "ticker": m.get("ticker"),
            "kind": (m.get("headline") or {}).get("kind"),
            "median": (m.get("headline") or {}).get("median"),
            "p25": (m.get("headline") or {}).get("p25"),
            "p75": (m.get("headline") or {}).get("p75"),
            "n": (m.get("headline") or {}).get("n"),
            "biggest_moves": [
                {
                    "date": e.get("date"),
                    "name": e.get("name"),
                    "pair": e.get("pair"),
                    "abnormal_return": e.get("abnormal_return"),
                    "kind": e.get("kind"),
                }
                for e in _cap(m.get("biggest_moves"), 3)
                if isinstance(e, dict)
            ],
        }
        for m in _cap(ranked, 6)
    ]
    forward = story.get("forward") or {}
    backtest = story.get("backtest") or {}
    # Coverage counts live under coverage.summary (events / events_measured); the
    # top-level `coverage` also carries a note and the per-dyad trace.
    coverage_summary = (story.get("coverage") or {}).get("summary") or {}
    return {
        "region": story.get("region_label") or story.get("region"),
        "as_of": story.get("as_of"),
        "measured_through": story.get("measured_through"),
        "coverage": {
            "events": coverage_summary.get("events"),
            "events_measured": coverage_summary.get("events_measured"),
        },
        "markets": top_markets,
        "forward_direction": [
            {
                "market": d.get("market_name"),
                "expected_abnormal_return": d.get("expected_abnormal_return"),
                "measurements": d.get("measurements"),
            }
            for d in _cap(forward.get("direction"), 6)
            if isinstance(d, dict)
        ],
        "transmission_skill": story.get("transmission_skill"),
        "paper_book": {
            "total_return": (backtest.get("summary") or {}).get("total_return")
            if isinstance(backtest, dict) else None,
            "hit_rate": (backtest.get("summary") or {}).get("hit_rate")
            if isinstance(backtest, dict) else None,
        },
        "note": "Every figure here is the transmission engine's. Do not originate one.",
    }


def game_region_packet(region_map: dict[str, Any]) -> dict[str, Any]:
    ranking = [
        {
            "dyad_name": r.get("dyad_name"),
            "standing": r.get("standing"),
            "coercive_events": r.get("coercive_events"),
            "hostility": r.get("hostility"),
            "sharp_departure_probability": r.get("sharp_departure_probability"),
            "top_course": (r.get("top_scenario") or {}).get("kind")
            if isinstance(r.get("top_scenario"), dict) else None,
        }
        for r in _cap(region_map.get("ranking"), 8)
        if isinstance(r, dict)
    ]

    def _scn(rows: Any) -> list[dict[str, Any]]:
        return [
            {
                "dyad_name": s.get("dyad_name"),
                "kind": s.get("kind_label") or s.get("kind"),
                "likelihood": s.get("likelihood"),
                "market": (_cap(s.get("market_implications"), 1) or [{}])[0].get("market_name"),
                "market_move": (_cap(s.get("market_implications"), 1) or [{}])[0].get("median"),
            }
            for s in _cap(rows, 4)
            if isinstance(s, dict)
        ]

    return {
        "region": region_map.get("region"),
        "as_of": region_map.get("as_of"),
        "dyads_solved": region_map.get("dyads_solved"),
        "dyads_cinc": region_map.get("dyads_cinc"),
        "dyads_tilted": region_map.get("dyads_tilted"),
        "nash_gap": region_map.get("nash_gap"),
        "kernel": region_map.get("kernel"),
        "ranking": ranking,
        "escalatory": _scn(region_map.get("scenarios_escalatory")),
        "calming": _scn(region_map.get("scenarios_calming")),
        "note": "Ranking and courses are the solver's. Do not re-rank or originate a number.",
    }


def game_dyad_packet(sol: dict[str, Any]) -> dict[str, Any]:
    concepts = sol.get("concepts") or {}
    primary = concepts.get(sol.get("primary_solver") or "qre") or concepts.get("qre") or {}
    scenarios = [
        {
            "kind": s.get("kind_label") or s.get("kind"),
            "likelihood": s.get("likelihood"),
            "market": (_cap(s.get("market_implications"), 1) or [{}])[0].get("market_name"),
            "market_move": (_cap(s.get("market_implications"), 1) or [{}])[0].get("median"),
        }
        for s in _cap(primary.get("scenarios"), 4)
        if isinstance(s, dict)
    ]
    opening = sol.get("opening") or {}
    return {
        "dyad_name": sol.get("dyad_name"),
        "sides": sol.get("sides"),
        "as_of": sol.get("as_of"),
        "opening": {
            "standing": opening.get("standing"),
            "posture": opening.get("posture"),
            "family": opening.get("family"),
            "intensity_band": opening.get("intensity_band"),
            "latest_intensity": opening.get("latest_intensity"),
            "scale": opening.get("scale"),
            "capability": opening.get("capability"),
            "beliefs": opening.get("beliefs"),
        },
        "scenarios": scenarios,
        "nash_gap": (concepts.get("lp") or {}).get("nash_gap"),
        "kernel": sol.get("kernel"),
        "note": "The solve's own fields. Do not originate a number.",
    }


def relationship_packet(bundle: dict[str, Any]) -> dict[str, Any]:
    """Assembled from the pieces the relationship page already reads: the dyad
    game solution (the call + opening), a slice of the measured timeline
    (history), and the region calibration (work)."""
    sol = bundle.get("solution") or {}
    timeline = bundle.get("timeline") or {}
    calibration = bundle.get("calibration") or {}
    series = bundle.get("series") or {}
    events = [
        {
            "date": e.get("date"),
            "name": e.get("name") or e.get("headline"),
            "goldstein": e.get("goldstein"),
            "escalation_direction": e.get("escalation_direction"),
            "markets": [
                {"market": m.get("market_name"), "car": m.get("car"), "window": m.get("window")}
                for m in _cap(e.get("markets"), 2)
                if isinstance(m, dict)
            ],
        }
        for e in _cap(timeline.get("events"), 6)
        if isinstance(e, dict)
    ]
    recent_raw = calibration.get("recent")
    recent: dict[str, Any] = recent_raw if isinstance(recent_raw, dict) else {}
    dyad_pkt = game_dyad_packet(sol) if sol else {}
    return {
        "dyad_name": bundle.get("dyad_name") or dyad_pkt.get("dyad_name"),
        "opening": dyad_pkt.get("opening"),
        "trajectory": {
            "peak": series.get("peak"),
            "active_quarters": series.get("active_quarters"),
            "span": series.get("span"),
        },
        "timeline": events,
        "forecast_courses": dyad_pkt.get("scenarios"),
        "calibration": {
            "brier": calibration.get("brier"),
            "base_rate_brier": calibration.get("base_rate_brier"),
            "skill": calibration.get("skill"),
            "calls": calibration.get("calls"),
            "recent_skill": recent.get("skill"),
            "recent_years": recent.get("years"),
        },
        "note": "Measured record, solve and scoring. Do not originate a number.",
    }


_PACKET_BUILDERS = {
    "markets": markets_packet,
    "game_region": game_region_packet,
    "game_dyad": game_dyad_packet,
    "relationship": relationship_packet,
}


def build_packet(surface: str, payload: dict[str, Any]) -> dict[str, Any]:
    builder = _PACKET_BUILDERS.get(surface)
    if builder is None:
        raise ValueError(f"unknown narrative surface: {surface!r}")
    return builder(payload)


def fingerprint_of(packet: dict[str, Any]) -> str:
    """A short, stable digest of the packet — the snapshot the prose is written
    from. When it changes, the prose is stale and the job rewrites it."""
    blob = json.dumps(packet, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ── numeric-provenance validation ───────────────────────────────────────────

_NUM_RE = re.compile(r"[-+\u2212]?\d[\d,]*(?:\.\d+)?")


def _packet_values(packet: Any) -> list[float]:
    """Every finite number anywhere in the packet, plus numbers embedded in its
    strings (dates, 'since 2014', model ids) — the full set of figures the
    prose is allowed to state."""
    out: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            if math.isfinite(float(node)):
                out.append(float(node))
        elif isinstance(node, str):
            for token in _NUM_RE.findall(node):
                try:
                    out.append(float(token.replace(",", "").replace("\u2212", "-")))
                except ValueError:
                    continue
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(packet)
    return out


def _covered(x: float, values: list[float]) -> bool:
    """Does some packet value explain the prose number x? Tolerant of the
    fraction/percent duality (medians are stored as fractions, shown as %) and
    of display rounding. Four-digit years are always allowed (factual dates,
    and typically present in packet strings anyway)."""
    if x == int(x) and 1900 <= x <= 2100:
        return True
    for v in values:
        for candidate in (v, v * 100.0, v / 100.0, -v, -v * 100.0):
            if math.isclose(x, candidate, rel_tol=0.01, abs_tol=0.011):
                return True
    return False


def validate(blocks: dict[str, Any], packet: dict[str, Any]) -> tuple[bool, list[str]]:
    """True when every number in the prose is explained by the packet. On
    False, the offending tokens are returned and the caller falls back to the
    deterministic templates — strict on purpose: a rogue figure is worse than a
    wooden sentence, and the whole product rests on it."""
    values = _packet_values(packet)
    offending: list[str] = []
    for key in ("history", "work", "forecast"):
        text = blocks.get(key)
        if not isinstance(text, str):
            continue
        for token in _NUM_RE.findall(text):
            raw = token.replace(",", "").replace("\u2212", "-")
            try:
                x = float(raw)
            except ValueError:
                continue
            if not _covered(x, values):
                offending.append(token)
    return (not offending), offending


# ── composition ─────────────────────────────────────────────────────────────


def _model() -> str:
    return os.getenv("GEOGRAPH_AGENT_MODEL", "").strip() or _DEFAULT_MODEL


def _narrate(surface: str, packet: dict[str, Any]) -> dict[str, str]:
    """One model call returning the three blocks. Dark without a key."""
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise AgentUnavailable(
            "OPENAI_API_KEY is not set — AI narrative is dark. Every surface "
            "serves its deterministic prose without it."
        )
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AgentUnavailable(
            'the `openai` package is not installed — pip install -e ".[reasoning]"'
        ) from exc

    client = OpenAI(api_key=key)
    response = client.chat.completions.create(
        model=_model(),
        max_tokens=1400,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"{_BRIEF.get(surface, '')}\n\n"
                    "Write the three blocks from this packet (every number here "
                    "was measured or counted before you saw it):\n"
                    f"{json.dumps(packet, indent=2, default=str)}"
                ),
            },
        ],
    )
    choice = response.choices[0] if response.choices else None
    message = choice.message if choice is not None else None
    text = (getattr(message, "content", None) or "{}") if message is not None else "{}"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentUnavailable(f"model returned non-JSON narrative: {exc}") from exc
    return {
        key: str(parsed.get(key) or "").strip()
        for key in ("history", "work", "forecast")
    }


def served_narrative(
    panel: Any,
    *,
    surface: str,
    region: str,
    subject_id: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The persisted narrative for a surface, shaped for the API — NO model call
    at read time. When `payload` is given, its live fingerprint is compared to
    the stored one to flag staleness (the numbers moved since the prose was
    written). Returns None when nothing is persisted, so the caller falls back
    to its deterministic prose."""
    from core.panel import pg_store

    stored = pg_store.narrative(
        panel, region_pack=region, surface=surface, subject_id=subject_id
    )
    if stored is None:
        return None
    blocks = stored.get("blocks") or {}
    stale: bool | None = None
    if payload is not None:
        try:
            stale = fingerprint_of(build_packet(surface, payload)) != stored.get("fingerprint")
        except Exception:  # noqa: BLE001 - a fingerprint we cannot compute is not a failure
            stale = None
    return {
        "history": blocks.get("history"),
        "work": blocks.get("work"),
        "forecast": blocks.get("forecast"),
        "model": stored.get("model"),
        "generated_at": stored.get("generated_at"),
        "stale": stale,
    }


def compose(surface: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Build the packet, narrate, and validate. Returns the persistable record,
    or None when the prose fails numeric-provenance validation (the caller then
    keeps the deterministic prose). Raises AgentUnavailable when the key/package
    is missing, which the `narrate` job treats as "skip, stay dark"."""
    packet = build_packet(surface, payload)
    blocks = _narrate(surface, packet)
    if not any(blocks.get(k) for k in ("history", "work", "forecast")):
        return None
    ok, offending = validate(blocks, packet)
    if not ok:
        return None
    return {
        "surface": surface,
        "fingerprint": fingerprint_of(packet),
        "blocks": blocks,
        "evidence": packet,
        "model": _model(),
    }
