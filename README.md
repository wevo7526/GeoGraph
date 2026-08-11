# GeoGraph

An applied-history engine for geopolitics and markets: a **120-year network
archive** (1905 → present) of geopolitical actors, relationships and events —
built to a strict provenance discipline — a **transmission layer** that
measures how those events actually moved markets at whatever resolution each
era allows, and a **reasoning layer** that reads the present against that deep
memory and produces forward assessments as reasoned scenarios, roughly 20
years out.

One line: it operationalizes applied history at the scale of the longue durée
— networks over hierarchies, analogy within comparable regimes, honesty about
uncertainty.

Sibling project to [MarketGraph](../marketgraph), from which it inherits its
foundation: a LinkML ontology as the single source of truth, Kuzu as the
embedded knowledge graph, the same provenance invariant (every edge cites a
Source), and the same zero-drama deployment shape on Railway.

**The full design is `docs/build-spec.md`** — the why (Part I) and the how
(Part II). Every decision there is locked; this README is the door, not the
argument.

## What makes it different

- **The fidelity gradient is modeled, not smoothed over.** The deep past is
  event-rich but market-data-poor; fidelity rises toward the present. Every
  event carries its tier and resolution; every measured market effect carries
  the resolution it was measured at; the reasoning layer down-weights coarse
  history rather than pretending a 1912 annual return is a 2012 daily CAR.
- **The geopolitics-to-money link is shown, not asserted.** A deterministic
  event study computes abnormal returns per event per market — only for
  markets that existed at the time, calendar-aware (Gulf trades Sun–Thu, US
  Mon–Fri, and the `first_mover` flag is real information).
- **Two forecasting modes, honestly separated.** Near-term (0–3y): calibrated
  probabilistic scenarios, Brier-scored. Long-horizon (5–20y): structural
  pressure over windows — never dated point predictions, and every output
  says so.
- **The AI reasons; it never originates a number** that lands in an effect, a
  network metric, or the deterministic part of a forecast.

## Run it

```bash
pip install -e ".[dev,api]"
python scripts/seed_pack.py mena     # regimes, actors, markets, marquee spine
python -m core.api.app               # API + explorer on :8000

npm --prefix web install
npm --prefix web run dev             # Vite on :5173, proxies /api

pytest                               # no DB, no network needed
```

Postgres (the market panel) is only needed for the transmission engine:
set `DATABASE_URL` and run `python scripts/apply_schema.py`. Everything is
optional configuration — see `.env.example`.

## Layout

```
core/ontology/    LinkML schema (source of truth), Kuzu DDL derivation, crosswalks
core/graph/       Kuzu store + network analytics
core/panel/       Postgres price panel (multi-frequency) + event-study set
core/ingestion/   deep tier: COW, ICB, JST, Shiller · modern: GDELT, prices, 13F
core/classifier/  Head A event typing · Head B escalation (deterministic)
core/transmission/ the event study — where geopolitics is measured against money
core/reasoning/   regimes, analogy, sensor loop, two forecast modes, calibration
core/api/         FastAPI · core/mcp/ MCP server (agent surface)
packs/mena/       region pack one (proves the model) · packs/china/ (Phase 6)
web/              Vite + React + Tailwind explorer with the 120-year time slider
```

## Status

Phase 0 scaffold: ontology, stores, crosswalks, regime segmentation,
escalation head, calendars, the MENA pack and the explorer shell are in and
tested. The build proceeds by the phased milestones in
`docs/build-spec.md` §18 — next: the marquee spine end to end, then the
transmission engine on one modern episode (the twelve-day war, 2025).

Built by two William & Mary MBAs
