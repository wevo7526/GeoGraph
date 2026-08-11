"""Shiller's long US series (section 5.1): monthly equity index and long
rates since 1871 — the resolution step between JST's annual and yfinance's
daily. Rows land in `market_observations` with frequency='monthly'.

Free download. PHASE 3.
"""

from __future__ import annotations

from pathlib import Path


def load_monthly(path: Path) -> int:
    """Shiller monthly rows → market_observations (monthly, US)."""
    raise NotImplementedError("Phase 3 — see docs/build-spec.md section 5.1")
