"""International Crisis Behavior — interstate crises from 1918 (section 5.1).

Severity and violence measures map through crosswalks/escalation_scale_map.yaml
(icb_severity → Goldstein-equivalent) and crosswalks/cow_to_cameo.yaml
(icb_crisis → CAMEO). Deterministic; source_scale='icb_severity' rides on
every Event so downstream reasoning knows the escalation number is coarse.

Flat files, no credentials. PHASE 3.
"""

from __future__ import annotations

from pathlib import Path


def load_crises(csv_path: Path) -> int:
    """ICB crises → Events + actor edges, harmonized through the crosswalks."""
    raise NotImplementedError("Phase 3 — see docs/build-spec.md section 5.1")
