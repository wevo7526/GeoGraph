"""ACLED — event-level conflict validation for the modern tier (section 5.2),
the finer-grained companion to ucdp.py. Same contract: flag disagreements,
never auto-resolve. PHASE 4."""

from __future__ import annotations


def validate_events(*, region_pack: str, start: str, end: str) -> list[dict]:
    """Return disagreement rows between graph events and ACLED records."""
    raise NotImplementedError("Phase 4 — see docs/build-spec.md section 5.2")
