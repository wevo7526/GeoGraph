"""Modern-tier market data: yfinance + FRED (section 5.2).

The pack's markets.yaml names the universe — ^GSPC and sector ETFs, ^TASI.SR
(Saudi, 1985), DFMGI.AE (Dubai, 2000), FADX15.FGI (Abu Dhabi, 2000), BZ=F
(Brent), GC=F (gold) — and FRED supplies DGS2/DGS10 yields.

TWO RULES, both fidelity-gradient consequences:
  - VERIFY EACH TICKER'S DEPTH ON INGEST (decision: spec 5.2). A ticker's
    actual history start is recorded against the market's inception_date;
    silent short history is how a "40-year study" quietly becomes a 10-year
    one.
  - INTRADAY IS RECENT-ONLY (~60 days on yfinance). It lands in
    market_intraday and NOTHING may build a dependency on historical
    intraday existing.

Requires the `ingest` extra; FRED needs FRED_API_KEY. PHASE 1 (the first
worked event study needs this data in Postgres).
"""

from __future__ import annotations

from core.settings import Settings


def load_daily(settings: Settings, *, region_pack: str) -> int:
    """Daily bars for every market in the pack → market_observations, with a
    per-ticker depth report printed."""
    raise NotImplementedError("Phase 1 — see docs/build-spec.md section 5.2")


def load_intraday(settings: Settings, *, region_pack: str) -> int:
    """Recent intraday prints → market_intraday."""
    raise NotImplementedError("Phase 1 — see docs/build-spec.md section 5.2")


def load_yields(settings: Settings) -> int:
    """FRED DGS2/DGS10 → market_observations (daily)."""
    raise NotImplementedError("Phase 1 — see docs/build-spec.md section 5.2")
