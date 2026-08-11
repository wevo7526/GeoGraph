"""GDELT via BigQuery — the modern tier's event firehose (section 5.2).

`gdelt-bd.gdeltv2.events`: daily, CAMEO-coded, Goldstein-scored. TRUSTED
DIRECTLY — no reclassification (classifier path 1). Backfill is scoped to a
pack's actor set (Phase 4), tagged fidelity_tier='modern_coded',
temporal_resolution='day', source_scale='goldstein'.

Requires BIGQUERY_PROJECT + GOOGLE_APPLICATION_CREDENTIALS and the `ingest`
extra. PHASE 4.
"""

from __future__ import annotations

from core.settings import Settings


def backfill(settings: Settings, *, region_pack: str, start: str, end: str) -> int:
    """Query events for the pack's actor set and write Events + edges.
    Batched writes — Kuzu is single-writer and a backfill is a batch job."""
    raise NotImplementedError("Phase 4 — see docs/build-spec.md section 5.2")
