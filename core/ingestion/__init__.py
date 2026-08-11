"""Ingestion — build-spec section 5. One module per source, organized by era.

THE FIDELITY GRADIENT IS THE ORGANIZING FACT: the deep tier (~1905–1979) is
structured historical datasets entering through DETERMINISTIC crosswalks;
the modern tier (~1979–) is coded daily events and daily-to-intraday prices.
Every loader tags its records with fidelity_tier, temporal_resolution and
source_scale, writes a Source node FIRST (the provenance ordering: sources
before the edges that cite them), and goes through kuzu_store / pg_store —
never around them.

Loaders never infer a fact to tidy a parse failure. Failures are dropped and
counted; counts are printed. (The MarketGraph rule, unchanged.)

Deep tier:   cow, icb, jst, shiller          (flat files, no credentials)
Modern tier: gdelt, ucdp, acled, gpr, market_data, edgar_13f
"""
