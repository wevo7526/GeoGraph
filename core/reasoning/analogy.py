"""The analogy engine — build-spec section 13. Applied history, retrievable.

Retrieve structurally similar past situations for a present question, using
Kuzu's vector index (Event.embedding) PLUS structural matching on actor
roles, escalation trajectory, network position, and regime. The vector match
proposes; the structural match disposes — and `regimes.comparable` is the
admissibility gate: ONLY match within comparable regimes. Matches persist as
Analogue nodes with the rationale on display.

The LLM narrates retrieved analogues; it never invents the similarity score.

PHASE 5.
"""

from __future__ import annotations

from typing import Any


def find_analogues(
    query_ref: str,
    *,
    region_pack: str,
    regime_kind: str = "monetary_order",
    k: int = 5,
) -> list[dict[str, Any]]:
    """Top-k regime-admissible analogues for a present situation, persisted
    as Analogue nodes and returned with similarity + rationale."""
    raise NotImplementedError("Phase 5 — see docs/build-spec.md section 13")
