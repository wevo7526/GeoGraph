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

- The ASSESS half is the LLM agent narrating around deterministic context.
  Without ANTHROPIC_API_KEY it is a 503 that names the key and what still
  works; it never fakes an assessment.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from core import packs
from core.classifier import escalation
from core.classifier import typing as event_typing
from core.graph import kuzu_store
from core.reasoning import agent, analogy

router = APIRouter(tags=["reasoning"])


def _conn(request: Request) -> Any:
    conn = request.app.state.graph
    if conn is None:
        raise HTTPException(
            status_code=503, detail=request.app.state.graph_error or "graph unavailable"
        )
    return conn


@router.get("/reasoning/options")
def what_if_options(region: str = "mena") -> dict[str, Any]:
    """The composer's vocabulary: the pack's roster and the codebook's
    scorable CAMEO codes — both derived, neither invented here."""
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


class AssessRequest(BaseModel):
    question: str
    region: str = "mena"


@router.post("/reasoning/assess")
def assess(request: Request, body: AssessRequest) -> dict[str, Any]:
    """The agent's narrated assessment — key-gated, honest when dark.

    The context handed to the agent is assembled HERE, deterministically:
    the region's frozen forecasts and most conflictual dyads, each with its
    node id, so every number the agent cites pre-exists the call.
    """
    conn = _conn(request)
    forecasts = kuzu_store.query(
        conn,
        "MATCH (f:Forecast) WHERE f.region_pack = $region "
        "RETURN f.node_id AS node_id, f.mode AS mode, f.question AS question, "
        "f.generated_at AS generated_at, f.scenarios_json AS scenarios_json, "
        "f.boundary_statement AS boundary_statement "
        "ORDER BY f.generated_at DESC, f.node_id LIMIT 4",
        {"region": body.region},
    )
    dyads = kuzu_store.query(
        conn,
        "MATCH (d:Dyad) RETURN d.node_id AS node_id, d.name AS name, "
        "d.ewma_baseline AS baseline, d.ewma_as_of AS as_of "
        "ORDER BY d.ewma_baseline, d.node_id LIMIT 8",
    )
    context = {
        "frozen_forecasts": forecasts,
        "most_conflictual_dyads": dyads,
        "note": (
            "forecast scenario likelihoods are counted base rates; dyad "
            "baselines are EWMA Goldstein levels (lower = more conflictual)."
        ),
    }
    try:
        return agent.assess(body.question, region_pack=body.region, context=context)
    except agent.AgentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
