"""The reasoning agent — build-spec sections 2, 3 and 13.

Claude over the MCP tool surface: reads the present against the graph's deep
memory, reasons explicitly about surprises (updating latent estimates through
the sensor loop's machinery, with the reasoning on display), holds
counterfactual branch points, and drafts the scenario rationales that
forecasting.py assembles.

DIVISION OF LABOUR, locked (section 17): the agent REASONS and NARRATES; the
deterministic core MEASURES. Every number in an effect, a metric, or the
deterministic part of a forecast exists before the agent sees it. The agent's
value is the argument, not the arithmetic.

Realist strategic logic (Kissinger, bargaining and power-transition
traditions): actors are modeled by interests, resolve, and capability — the
AttributeEstimate layer is the agent's state of belief about those.

PHASE 5. Requires the `reasoning` extra and ANTHROPIC_API_KEY.
"""

from __future__ import annotations

from typing import Any


def assess(question: str, *, region_pack: str) -> dict[str, Any]:
    """One reasoned assessment: analogues retrieved, latent estimates read,
    scenario rationales drafted — every citation a node_id."""
    raise NotImplementedError("Phase 5 — see docs/build-spec.md section 13")
