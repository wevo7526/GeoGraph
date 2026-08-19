"""The analogy engine — build-spec section 13. Applied history, retrievable.

Retrieve structurally similar past situations for a present question. The
structural half is DETERMINISTIC and lands here: candidates are admitted
ONLY within comparable regimes (`regimes.comparable` is the gate, not a
similarity score), and similarity is an arithmetic distance over the coded
record — Goldstein weight, escalation against the dyad's own baseline, quad
class, and actor-role overlap. Matches persist as Analogue nodes with the
formula in the rationale.

The vector half PROPOSES (cosine over event names, request-time, capped);
this structural match still DISPOSES. Proposed hits are never folded into
the transmission average. Event.embedding stays unwritten — a Hobby volume
cannot hold the wire as vectors. The LLM narrates retrieved analogues; it
never invents the similarity score.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.graph import kuzu_store
from core.reasoning import regimes


class ProposeUnavailable(RuntimeError):
    """Vector propose cannot run here; structural ranking still can."""


#: Newest admissible events embedded per propose call. The structural ranker
#: still walks the whole candidate list; this cap is only the PROPOSE pool.
#: Embedding the wire would bill per request and fill the volume if persisted.
_PROPOSE_POOL = 80
_EMBED_MODEL = "text-embedding-3-small"
#: Matches Event.embedding's Kuzu FLOAT[1024]. We do not write those slots —
#: a 5 GB Hobby volume cannot hold a second copy of the wire as vectors.
_EMBED_DIMS = 1024
_EMBED_CACHE: dict[str, tuple[float, ...]] = {}


def _similarity(query: dict[str, Any], candidate: dict[str, Any]) -> float:
    """Deterministic structural similarity in [0, 1].

    Three distances and a role bonus, equally weighted — the formula IS the
    method string on the Analogue, so a reader can recompute any score:
    goldstein gap (scale 20), escalation-magnitude gap (scale 10), baseline
    gap (scale 20), and shared quad class / shared actor / mirrored direction
    as the structural-role term.
    """
    def gap(a: Any, b: Any, scale: float) -> float:
        if a is None or b is None:
            return 0.5  # unknown is HALF-distant: never a free match
        return min(1.0, abs(float(a) - float(b)) / scale)

    goldstein = 1.0 - gap(query.get("goldstein"), candidate.get("goldstein"), 20.0)
    magnitude = 1.0 - gap(
        query.get("escalation_magnitude"), candidate.get("escalation_magnitude"), 10.0
    )
    baseline = 1.0 - gap(
        query.get("escalation_baseline"), candidate.get("escalation_baseline"), 20.0
    )
    role = 0.0
    if query.get("quad_class") and query.get("quad_class") == candidate.get("quad_class"):
        role += 0.5
    query_actors = {query.get("initiator_id"), query.get("target_id")} - {None}
    candidate_actors = {candidate.get("initiator_id"), candidate.get("target_id")} - {None}
    if query_actors & candidate_actors:
        role += 0.3
    if query.get("escalation_direction") == candidate.get("escalation_direction"):
        role += 0.2
    return round((goldstein + magnitude + baseline + min(role, 1.0)) / 4.0, 4)


def candidate_text(row: dict[str, Any]) -> str:
    """The string the vector half embeds. Every figure in it is already on
    the event — this is a retrieval key, not a new measurement."""
    name = str(row.get("name") or row.get("label") or row.get("quad_class") or "event")
    when = str(row.get("event_time") or "")[:10]
    quad = str(row.get("quad_class") or "")
    gold = row.get("goldstein")
    gold_s = f"goldstein {gold}" if gold is not None else ""
    return " ".join(part for part in (name, when, quad, gold_s) if part)


def cosine(left: list[float] | tuple[float, ...], right: list[float] | tuple[float, ...]) -> float:
    """Cosine in [0, 1] after clamping negatives — retrieval rank, not a
    similarity the surface may treat as the structural score."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    na = math.sqrt(sum(a * a for a in left))
    nb = math.sqrt(sum(b * b for b in right))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def embed_texts(texts: list[str]) -> list[list[float]]:
    """OpenAI embeddings, 1024-d, process-cached by exact string.

    Raises ProposeUnavailable on a missing key or SDK. Callers that must
    stay deterministic catch this and return an empty propose list.
    """
    if not texts:
        return []
    missing = [t for t in texts if t not in _EMBED_CACHE]
    if missing:
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise ProposeUnavailable(
                "OPENAI_API_KEY is not set — vector propose is dark. "
                "Structural analogue ranking still runs."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProposeUnavailable(
                'the `openai` package is not installed — pip install -e '
                '".[reasoning]"'
            ) from exc
        client = OpenAI(api_key=key)
        response = client.embeddings.create(
            model=_EMBED_MODEL,
            input=missing,
            dimensions=_EMBED_DIMS,
        )
        by_index = {item.index: item.embedding for item in response.data}
        for i, text in enumerate(missing):
            _EMBED_CACHE[text] = tuple(float(x) for x in by_index[i])
    return [list(_EMBED_CACHE[t]) for t in texts]


def propose_candidates(
    query: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    query_date: str,
    regime_kind: str = "monetary_order",
    k: int = 5,
    embed: Callable[[list[str]], list[list[float]]] | None = None,
) -> list[tuple[float, dict[str, Any]]]:
    """Semantic retrieve, then the SAME admissibility gate as rank_candidates.

    The vector score PROPOSES; `regimes.comparable` DISPOSES. A similar story
    is not a similar code — callers must not fold these into the transmission
    average, which is the structural analogues' measured effects.
    """
    admissible: list[dict[str, Any]] = []
    for row in candidates:
        if row.get("node_id") == query.get("node_id"):
            continue
        when = row.get("event_time")
        if not when:
            continue
        if not regimes.comparable(query_date, str(when), kind=regime_kind):
            continue
        admissible.append(row)
    if not admissible:
        return []
    pool = sorted(
        admissible, key=lambda row: str(row.get("event_time") or ""), reverse=True,
    )[:_PROPOSE_POOL]
    query_row = {
        **query,
        "name": (
            query.get("name") or query.get("label")
            or query.get("quad_class") or "hypothetical"
        ),
        "event_time": query.get("event_time") or query_date,
    }
    texts = [candidate_text(query_row), *[candidate_text(row) for row in pool]]
    vectors = (embed or embed_texts)(texts)
    if len(vectors) != len(texts):
        return []
    qv = vectors[0]
    scored: list[tuple[float, dict[str, Any]]] = []
    for row, vec in zip(pool, vectors[1:], strict=True):
        scored.append((round(cosine(qv, vec), 4), row))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["node_id"]))
    return scored[:k]


#: The candidate shape the similarity formula reads — public so the API can
#: fetch candidates over its OWN connection (a request-time what-if must not
#: open a second connection against the single-writer graph).
EVENT_QUERY = (
    "MATCH (e:Event) "
    "OPTIONAL MATCH (e)-[:INITIATED_BY]->(i:Actor) "
    "OPTIONAL MATCH (e)-[:DIRECTED_AT]->(t:Actor) "
    "RETURN e.node_id AS node_id, e.name AS name, e.event_time AS event_time, "
    "e.goldstein AS goldstein, e.quad_class AS quad_class, "
    "e.escalation_direction AS escalation_direction, "
    "e.escalation_magnitude AS escalation_magnitude, "
    "e.escalation_baseline AS escalation_baseline, "
    "i.node_id AS initiator_id, t.node_id AS target_id "
    "ORDER BY e.event_time, e.node_id"
)


def rank_candidates(
    query: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    query_date: str,
    regime_kind: str = "monetary_order",
    k: int = 5,
) -> list[tuple[float, dict[str, Any]]]:
    """Admissibility-gate then score PROVIDED candidates against a query
    shape — the pure half of `find_analogues`, which also serves the
    what-if surface (where the query is a HYPOTHETICAL event and nothing is
    persisted). Admissibility before similarity, always: an out-of-regime
    candidate is refused before any score exists."""
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in candidates:
        if row.get("node_id") == query.get("node_id"):
            continue
        if not regimes.comparable(query_date, str(row["event_time"]), kind=regime_kind):
            continue
        scored.append((_similarity(query, row), row))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["node_id"]))
    return scored[:k]


def rank_from_conn(
    conn: Any,
    query_ref: str,
    *,
    regime_kind: str = "monetary_order",
    k: int = 5,
    persist: bool = False,
) -> list[dict[str, Any]]:
    """Top-k regime-admissible analogues over an already-open connection.

    THE MCP PATH IS READ-ONLY, so `persist=False` ranks without writing
    Analogue nodes — a second `kuzu.Database` inside the API process would
    fail the single-writer lock, and the MCP server already holds a reader.
    `find_analogues` still persists: that is the batch/offline path.
    """
    rows = kuzu_store.query(conn, EVENT_QUERY)
    by_id = {row["node_id"]: row for row in rows}
    query = by_id.get(query_ref)
    if query is None:
        raise KeyError(f"no such event in the graph: {query_ref}")

    top = rank_candidates(
        query, rows,
        query_date=str(query["event_time"]),
        regime_kind=regime_kind,
        k=k,
    )

    analogues: list[dict[str, Any]] = []
    for similarity, row in top:
        suffix = f"{query_ref.split(':', 1)[1]}--{row['node_id'].split(':', 1)[1]}"
        analogues.append({
            "node_id": f"analogue:{suffix}",
            "query_ref": query_ref,
            "event_id": row["node_id"],
            "similarity": similarity,
            "regime_matched": regime_kind,
            "rationale": (
                f"{row['name']} ({row['event_time']}): structural match "
                f"{similarity:.2f} — goldstein {row['goldstein']} vs "
                f"{query['goldstein']}, escalation "
                f"{row['escalation_direction']}/{row['escalation_magnitude']} vs "
                f"{query['escalation_direction']}/{query['escalation_magnitude']}, "
                f"quad {row['quad_class']}; admissible within {regime_kind}. "
                "Formula: mean of goldstein/magnitude/baseline proximities and "
                "the role term."
            ),
        })
    if persist and analogues:
        kuzu_store.merge_nodes(conn, "Analogue", analogues)
    return analogues


def find_analogues(
    db_path: Path,
    query_ref: str,
    *,
    region_pack: str,
    regime_kind: str = "monetary_order",
    k: int = 5,
) -> list[dict[str, Any]]:
    """Top-k regime-admissible analogues for an event, persisted as Analogue
    nodes and returned with similarity + rationale.

    Admissibility before similarity: a 1973 event under Bretton-Woods
    aftermath is NOT retrievable for a 2025 question however similar its
    shape — `regimes.comparable` refuses the pair and no score is computed.
    """
    del region_pack  # pack-scoped queries live at the call site
    conn = kuzu_store.connect(db_path)
    try:
        return rank_from_conn(
            conn, query_ref, regime_kind=regime_kind, k=k, persist=True,
        )
    finally:
        kuzu_store.close(conn)
