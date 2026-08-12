"""Jordà-Schularick-Taylor Macrohistory Database (section 5.1).

Annual equity, bond, bill and housing returns plus rates, credit and public
debt for 18 advanced economies since 1870 — the deep past's market panel.
Rows land in Postgres `market_observations` with frequency='annual'; many
carry a single `value` rather than OHLC, which the panel schema allows on
purpose. NO GULF DATA EXISTS in this era and none is fabricated: deep-past
transmission runs on US equities, US rates, oil and gold only.

Free download (the site's "xlsx" link actually serves Stata .dta, which
pandas reads natively). PHASE 3 NOTE: not yet wired into transmission ON
PURPOSE — deep-past transmission runs on US equities, US rates, oil and gold
only (build-spec section 5.3), and the US is covered FINER by Shiller's
monthly series from 1871. JST's annual rows become load-bearing when a
non-US pack (Phase 6's China, Europe later) needs its 18-economy panel;
`eq_tr` is a RETURN, so loading means deriving a cumulative index level and
saying so in source_ref.
"""

from __future__ import annotations

from pathlib import Path


def load_panel(path: Path) -> int:
    """JST rows → market_observations (annual)."""
    raise NotImplementedError("Phase 3 — see docs/build-spec.md section 5.1")
