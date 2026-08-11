"""The market-as-sensor loop — build-spec section 4, the forward-looking core.

The true mechanisms are private information we do not have. So: estimate the
latent variables (position, salience, clout, resolve) from observable
proxies, hold them as DISTRIBUTIONS, and treat the market reaction as a
second sensor. The residual between the expected and the REALIZED market
move — both computed by the deterministic transmission engine — is our
estimate of the private information open sources could not show, and it
updates the AttributeEstimate distributions.

THE LOOP IS POWERED ONLY BY REALIZED OUTCOMES, NEVER BY THE MODEL'S OWN
PREDICTIONS. That sentence is the whole defense against the loop eating its
own tail; it is restated here so no future refactor forgets it.

Updates write new AttributeEstimate nodes (method='sensor_update') — the old
estimate is history, not overwritten state, so the trajectory of belief is
itself queryable.

PHASE 5.
"""

from __future__ import annotations

from typing import Any


def update_from_effect(event_node_id: str) -> list[dict[str, Any]]:
    """Read the event's measured AFFECTED edges, compare against the expected
    move given current estimates, and emit updated AttributeEstimate rows for
    the actors involved. Returns what it wrote."""
    raise NotImplementedError("Phase 5 — see docs/build-spec.md sections 4 and 13")
