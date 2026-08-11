"""Correlates of War — the deep tier's backbone (build-spec section 5.1).

Five COW products, five jobs:
  - Militarized Interstate Disputes + the war datasets (1816–2014): Events,
    hostility levels through crosswalks/cow_to_cameo.yaml and the escalation
    scale map — NEVER the LLM.
  - National Material Capabilities → CINC: seeds Actor `clout`
    AttributeEstimates (method='cinc_seed') per state per year.
  - Formal Alliances + IGO memberships: RELATES_TO edges with validity
    windows — the deep past's durable network.
  - State-system membership: Actor.state_from/state_to — THE ACTOR SET IS
    TIME-VARYING; empires appear and dissolve, and this file is why the graph
    knows it.
  - ICOW territorial claims (1816–2001): salience measure → seeds `salience`
    AttributeEstimates (method='icow_seed').

Flat CSV files, no credentials. PHASE 3.
"""

from __future__ import annotations

from pathlib import Path


def load_state_system(csv_path: Path) -> int:
    """COW state-system membership → Actor nodes with membership windows."""
    raise NotImplementedError("Phase 3 — see docs/build-spec.md section 5.1")


def load_mids(csv_path: Path) -> int:
    """MIDs → Events via cow_to_cameo + escalation crosswalks, with
    INITIATED_BY / DIRECTED_AT / DERIVED_FROM edges."""
    raise NotImplementedError("Phase 3 — see docs/build-spec.md section 5.1")


def load_cinc(csv_path: Path) -> int:
    """NMC → per-state per-year clout AttributeEstimates (CINC seed)."""
    raise NotImplementedError("Phase 3 — see docs/build-spec.md section 5.1")


def load_alliances_and_igos(alliances_csv: Path, igos_csv: Path) -> int:
    """Formal Alliances + IGO membership → RELATES_TO with validity windows."""
    raise NotImplementedError("Phase 3 — see docs/build-spec.md section 5.1")


def load_icow_claims(csv_path: Path) -> int:
    """ICOW territorial claims → salience AttributeEstimates (ICOW seed)."""
    raise NotImplementedError("Phase 3 — see docs/build-spec.md section 5.1")
