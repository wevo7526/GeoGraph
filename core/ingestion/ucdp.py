"""UCDP — conflict validation for the modern tier (section 5.2). Cross-checks
GDELT-derived conflict events against curated conflict data; disagreements
are FLAGGED for review, never auto-resolved in either direction. PHASE 4."""

from __future__ import annotations


def validate_events(*, region_pack: str, start: str, end: str) -> list[dict]:
    """Return disagreement rows between graph events and UCDP records."""
    raise NotImplementedError("Phase 4 — see docs/build-spec.md section 5.2")
