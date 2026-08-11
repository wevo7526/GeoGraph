"""The GPR index (Caldara–Iacoviello) — a REGIME OVERLAY, not an event source
(section 5.2). Monthly geopolitical-risk pressure series, fetched from
GPR_INDEX_URL, landing in the panel as observations on a synthetic 'GPR'
market. The structural layer (Phase 5) reads it as a slow variable. PHASE 4.
"""

from __future__ import annotations

from core.settings import Settings


def load_index(settings: Settings) -> int:
    """Fetch the GPR spreadsheet and upsert monthly rows into the panel."""
    raise NotImplementedError("Phase 4 — see docs/build-spec.md section 5.2")
