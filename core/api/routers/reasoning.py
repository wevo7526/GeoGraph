"""The reasoning surface — the what-if engine and the (key-gated) agent.

The page has two halves and they degrade honestly:

- The WHAT-IF half is fully deterministic and always on: the caller poses a
  hypothetical event (initiator, target, CAMEO code, date) and gets back its
  coded shape (Goldstein, quad class, escalation against the dyad's own
  standing baseline), the regime-admissible analogues the structural engine
  retrieves for it, and the MEASURED market effects of those analogues,
  aggregated and labeled as what they are — an analogy, never a prediction.
  Nothing is persisted: a hypothetical writes nothing into an archive of
  things that happened.

- The ASSESS half is the LLM agent narrating around the intel briefing
  (wire, region games, markets, globe, frozen forecasts). GET
  /reasoning/situation serves that briefing with no model. Without
  OPENAI_API_KEY, POST /reasoning/assess is a 503 that names the key
  and what still works; it never fakes an assessment. Follow-ups pass
  prior turns; a `reader` block says which desk summoned the agent.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core import packs
from core.classifier import escalation
from core.classifier import typing as event_typing
from core.graph import kuzu_store
from core.reasoning import agent, analogy
from core.reasoning import situation as situation_briefing

router = APIRouter(tags=["reasoning"])


def _conn(request: Request) -> Any:
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    return conn


def _worked_example(pack: packs.Pack) -> dict[str, Any] | None:
    """A composition the region can open on, DERIVED from its own spine.

    An empty composer is the reason this surface reads as inscrutable: the
    first two actors alphabetically doing whatever code sorts first is not a
    question anyone would ask, so the engine's output looked like noise
    before the reader had any way to judge it. The pack's most recent fully
    coded marquee event is a question they already recognise — asked again,
    at today's date, which is what makes it a hypothetical rather than a
    replay. Derived per pack, so a new region brings its own example and no
    region name appears here.
    """
    coded = [
        event
        for event in pack.marquee_events
        if event.get("cameo") and event.get("initiator") and event.get("target")
    ]
    if not coded:
        return None
    latest = max(coded, key=lambda e: str(e["date"]))
    return {
        "initiator": latest["initiator"],
        "target": latest["target"],
        "cameo": str(latest["cameo"]),
        "drawn_from": {"event_id": latest["id"], "name": latest["name"],
                       "date": str(latest["date"])},
    }


@router.get("/reasoning/options")
def what_if_options(region: str = "mena") -> dict[str, Any]:
    """The composer's vocabulary: the pack's roster, the codebook's scorable
    CAMEO codes, and a worked example — all derived, none invented here."""
    try:
        pack = packs.load(region)
    except packs.PackError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "region": region,
        "actors": [
            {"id": a["id"], "name": a["name"], "actor_type": a["actor_type"]}
            for a in pack.actors
        ],
        "codes": event_typing.codebook_entries(),
        "example": _worked_example(pack),
    }


@router.get("/reasoning/what-if")
def what_if(
    request: Request,
    initiator: str,
    target: str,
    cameo: str,
    date: str,
    region: str = "mena",
    k: int = Query(default=5, ge=1, le=12),
) -> dict[str, Any]:
    """A hypothetical event, read through the archive. Deterministic end to
    end; the response labels every section with what it is and is not."""
    try:
        goldstein = event_typing.goldstein_for(cameo)
        quad_class = event_typing.quad_class_for(cameo)
        label = event_typing.label_for(cameo)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if len(date) < 4 or not date[:4].isdigit():
        raise HTTPException(status_code=422, detail=f"{date!r} is not an ISO date")

    conn = _conn(request)

    # The dyad's standing baseline — escalation is relational, so the same
    # code reads differently on a rivalry than on an alliance. An unseen
    # dyad's first event IS its baseline (no global prior, by design).
    did = escalation.dyad_id(initiator, target)
    dyad_rows = kuzu_store.query(
        conn,
        "MATCH (d:Dyad {node_id: $id}) RETURN d.node_id AS node_id, "
        "d.name AS name, d.ewma_baseline AS baseline, d.ewma_as_of AS as_of",
        {"id": did},
    )
    baseline = dyad_rows[0]["baseline"] if dyad_rows else None
    coding = escalation.classify(goldstein, baseline)
    dyad: dict[str, Any] = {
        "node_id": did,
        "name": dyad_rows[0]["name"] if dyad_rows else None,
        "baseline": baseline,
        "baseline_as_of": dyad_rows[0]["as_of"] if dyad_rows else None,
        **coding,
    }
    if not dyad_rows:
        dyad["note"] = (
            "this dyad has no coded history — its first event would BE its "
            "baseline, so the hypothetical reads as stable by construction."
        )

    # Regime-admissible analogues for the hypothetical shape. Cross-region on
    # purpose (history is one archive); admissibility is the regime gate.
    candidates = kuzu_store.query(conn, analogy.EVENT_QUERY)
    query_shape = {
        "node_id": None,
        "goldstein": goldstein,
        "quad_class": quad_class,
        "initiator_id": initiator,
        "target_id": target,
        "escalation_direction": coding["escalation_direction"],
        "escalation_magnitude": coding["escalation_magnitude"],
        "escalation_baseline": coding["escalation_baseline"],
    }
    top = analogy.rank_candidates(query_shape, candidates, query_date=date, k=k)
    analogues = [
        {
            "event_id": row["node_id"],
            "name": row["name"],
            "event_time": row["event_time"],
            "similarity": similarity,
            "goldstein": row["goldstein"],
            "quad_class": row["quad_class"],
            "escalation_direction": row["escalation_direction"],
        }
        for similarity, row in top
    ]

    # The analogues' MEASURED effects, aggregated per market and window. One
    # small query per analogue (k <= 12) — a list-parameter IN against a
    # 170k-event table is the kind of query this engine punishes.
    effects: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in analogues:
        rows = kuzu_store.query(
            conn,
            "MATCH (e:Event {node_id: $id})-[a:AFFECTED]->(m:Market) "
            "RETURN m.ticker AS ticker, m.name AS market, a.window AS window, "
            "a.abnormal_return AS abnormal_return",
            {"id": entry["event_id"]},
        )
        measured = 0
        for row in rows:
            if row["abnormal_return"] is None:
                continue
            measured += 1
            key = (str(row["ticker"]), str(row["window"]))
            bucket = effects.setdefault(
                key,
                {"ticker": row["ticker"], "market": row["market"],
                 "window": row["window"], "sum": 0.0, "n": 0},
            )
            bucket["sum"] += float(row["abnormal_return"])
            bucket["n"] += 1
        entry["measured_effects"] = measured

    transmission = sorted(
        (
            {
                "ticker": bucket["ticker"],
                "market": bucket["market"],
                "window": bucket["window"],
                "mean_abnormal_return": round(bucket["sum"] / bucket["n"], 6),
                "n": bucket["n"],
            }
            for bucket in effects.values()
        ),
        key=lambda r: (str(r["ticker"]), str(r["window"])),
    )

    return {
        "region": region,
        "hypothetical": {
            "initiator": initiator, "target": target, "date": date,
            "cameo": cameo, "label": label,
            "goldstein": goldstein, "quad_class": quad_class,
        },
        "dyad": dyad,
        "analogues": analogues,
        "transmission": {
            "rows": transmission,
            "label": (
                "mean measured abnormal returns of the admissible analogues, "
                "by market and window — an ANALOGY drawn from what comparable "
                "events did, not a prediction of what this one would do."
            ),
        },
        "method": (
            "deterministic throughout: CAMEO -> Goldstein/quad from the "
            "codebook; escalation classified against the dyad's own EWMA "
            "baseline; analogues admissibility-gated by monetary order and "
            "ranked by the structural-similarity formula; transmission is "
            "the analogues' measured AFFECTED edges, averaged. Nothing "
            "persisted, nothing originated by a model."
        ),
    }


class AssessTurn(BaseModel):
    role: str
    content: str


class AssessRequest(BaseModel):
    question: str = "What is the situation?"
    region: str = "mena"
    history: list[AssessTurn] = Field(default_factory=list)
    surface: str | None = None
    focus: dict[str, str] | None = None


def _briefing(request: Request, region: str) -> dict[str, Any]:
    """The intel object — graph optional, pack required."""
    try:
        packs.load(region)
    except packs.PackError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return situation_briefing.assemble(request.app.state.graph, region)


@router.get("/reasoning/situation")
def situation(request: Request, region: str = "mena") -> dict[str, Any]:
    """The compact briefing the agent is handed — numbers only, no LLM.

    Same object POST /reasoning/assess wraps in a narration. External agents
    (MCP `situation`) and tests read it here so a dark key still shows what
    would have been argued over.
    """
    return _briefing(request, region)


@router.post("/reasoning/assess")
def assess(request: Request, body: AssessRequest) -> dict[str, Any]:
    """The agent's narrated assessment — key-gated, honest when dark.

    The context handed to the agent is the intel briefing assembled HERE,
    deterministically, from the same stores Intel and the Wire read: wire
    departures, live overlay, persisted region games, packed markets, globe
    coverage, frozen forecasts. `reader` names the desk and the open pair
    or market when the caller said so. Every number the agent cites
    pre-exists the call. A missing key is a 503 that names it; the
    briefing itself is still at GET /reasoning/situation.
    """
    question = (body.question or "").strip() or "What is the situation?"
    context = situation_briefing.with_reader(
        _briefing(request, body.region),
        surface=body.surface,
        focus=body.focus,
    )
    history = [turn.model_dump() for turn in body.history]
    try:
        result = agent.assess(
            question,
            region_pack=body.region,
            context=context,
            history=history,
        )
    except agent.AgentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    # THE CONTEXT COMES BACK WITH THE ANSWER. Section 17 says the agent never
    # originates a number; a reader can only hold it to that if they can see
    # what it was handed. Returning the exact context makes the rule checkable
    # instead of merely asserted — every figure in the prose should appear
    # here, and one that does not is a bug the reader can now catch.
    result["context"] = context
    return result
