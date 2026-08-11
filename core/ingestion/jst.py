"""Jordà-Schularick-Taylor Macrohistory Database (section 5.1).

Annual equity, bond, bill and housing returns plus rates, credit and public
debt for 18 advanced economies since 1870 — the deep past's market panel.
Rows land in Postgres `market_observations` with frequency='annual'; many
carry a single `value` rather than OHLC, which the panel schema allows on
purpose. NO GULF DATA EXISTS in this era and none is fabricated: deep-past
transmission runs on US equities, US rates, oil and gold only.

Free download, one Excel/CSV file. PHASE 3.
"""

from __future__ import annotations

from pathlib import Path


def load_panel(path: Path) -> int:
    """JST rows → market_observations (annual)."""
    raise NotImplementedError("Phase 3 — see docs/build-spec.md section 5.1")
