"""The analogy engine — build-spec section 13. Applied history, retrievable.

Retrieve structurally similar past situations for a present question. The
structural half is DETERMINISTIC and lands here: candidates are admitted
ONLY within comparable regimes (`regimes.comparable` is the gate, not a
similarity score), and similarity is an arithmetic distance over the coded
record — Goldstein weight, escalation against the dyad's own baseline, quad
class, and actor-role overlap. Matches persist as Analogue nodes with the
formula in the rationale.

The vector-index half (Event.embedding, semantic retrieval over narratives)
is the LLM side of Phase 5 and composes on top: the vector match will
PROPOSE, this structural match still DISPOSES. The LLM narrates retrieved
analogues; it never invents the similarity score.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.graph import kuzu_store
from core.reasoning import regimes


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
    conn = kuzu_store.connect(db_path)
    try:
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
        if analogues:
            kuzu_store.merge_nodes(conn, "Analogue", analogues)
        return analogues
    finally:
        kuzu_store.close(conn)
