"""SEC EDGAR 13F — SWF flows (section 5.2). Reuses MarketGraph's ingestion
patterns: the polite client (rate limit, cache beside the graph, EdgarBlocked
circuit breaker), filings-before-edges ordering, and per-item fault isolation
with dropped-and-counted failures.

PIF, Mubadala and ADIA file quarterly. THE VIEW IS COARSE AND SAYS SO
everywhere it is served: US-listed long equity only, 45-day lag, quarterly.
Positions land as FLOW edges (Actor swf → Market), `as_of` in the edge key so
two quarters are two edges.

PHASE 4.
"""

from __future__ import annotations

from core.settings import Settings

#: The SWFs the packs care about, by EDGAR CIK. Extended by pack sources.yaml.
SWF_CIKS: dict[str, str] = {
    "PIF": "0001767640",
    "Mubadala": "0001679826",
    "ADIA": "0001067983",  # placeholder — verify CIKs on first real ingest
}


def load_flows(settings: Settings, *, region_pack: str) -> int:
    """Quarterly 13F holdings for the pack's SWFs → FLOW edges."""
    raise NotImplementedError("Phase 4 — see docs/build-spec.md section 5.2")
