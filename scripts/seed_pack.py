"""Seed the graph from a region pack: sources FIRST (provenance ordering),
then regimes, actors, issues, markets, and the marquee spine with its edges.

  python scripts/apply_schema.py && python scripts/seed_pack.py mena

Deterministic and idempotent — re-running merges. Stop the API first (Kuzu is
single-writer). Ends with check_provenance and FAILS on any violation; a seed
that cannot cite itself does not get to exist.
"""

from __future__ import annotations

import sys

from core import packs
from core import settings as settings_module
from core.graph import kuzu_store
from core.reasoning import regimes


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "mena"
    pack = packs.load(name)
    settings = settings_module.load()
    settings.kuzu_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = kuzu_store.connect(settings.kuzu_db_path)
    kuzu_store.apply_schema(conn)

    # Sources before anything that cites them — the ordering IS the invariant.
    sources = pack.data["sources"].get("sources", [])
    n = kuzu_store.merge_nodes(conn, "Source", [
        {"node_id": s["id"], "name": s["name"], "kind": s.get("kind", "dataset"),
         "url": s.get("url", ""), "citation": s.get("citation", "")}
        for s in sources
    ])
    print(f"sources: {n}")

    print(f"regimes: {kuzu_store.merge_nodes(conn, 'Regime', regimes.as_nodes())}")

    n = kuzu_store.merge_nodes(conn, "Actor", [
        {"node_id": a["id"], "name": a["name"], "actor_type": a["actor_type"],
         "cow_ccode": a.get("cow_ccode"), "iso3": a.get("iso3", ""),
         "region_pack": pack.name}
        for a in pack.actors
    ])
    print(f"actors: {n}")

    n = kuzu_store.merge_nodes(conn, "Issue", [
        {"node_id": i["id"], "name": i["name"], "scale": i.get("scale", ""),
         "region_pack": pack.name}
        for i in pack.data["issues"].get("issues", [])
    ])
    print(f"issues: {n}")

    n = kuzu_store.merge_nodes(conn, "Market", [
        {"node_id": m["id"], "name": m["name"], "ticker": m["ticker"],
         "market_type": m["market_type"], "trading_calendar": m["trading_calendar"],
         "inception_date": m["inception_date"],
         "native_frequency": m.get("native_frequency", ""), "region_pack": pack.name}
        for m in pack.markets
    ])
    print(f"markets: {n}")

    pack_source = f"source:{pack.name}-marquee"
    events, initiated, directed, derived = [], [], [], []
    for e in pack.marquee_events:
        events.append({
            "node_id": e["id"], "name": e["name"], "event_time": e["date"],
            "action_cameo_code": str(e.get("cameo", "")),
            "quad_class": e.get("quad_class", ""), "region_pack": pack.name,
            "fidelity_tier": "modern_coded", "temporal_resolution": "day",
            "source_scale": "goldstein",
        })
        if e.get("initiator"):
            initiated.append({"src": e["id"], "dst": e["initiator"], "source_id": pack_source})
        if e.get("target"):
            directed.append({"src": e["id"], "dst": e["target"], "source_id": pack_source})
        derived.append({"src": e["id"], "dst": pack_source})
    print(f"events: {kuzu_store.merge_nodes(conn, 'Event', events)}")
    print(f"INITIATED_BY: {kuzu_store.merge_edges(conn, 'INITIATED_BY', initiated)}")
    print(f"DIRECTED_AT: {kuzu_store.merge_edges(conn, 'DIRECTED_AT', directed)}")
    print(f"DERIVED_FROM: {kuzu_store.merge_edges(conn, 'DERIVED_FROM', derived)}")

    occurred = []
    for e in pack.marquee_events:
        for regime in regimes.regimes_at(e["date"]).values():
            if regime:
                occurred.append({"src": e["id"], "dst": f"regime:{regime['id']}"})
    print(f"OCCURRED_IN: {kuzu_store.merge_edges(conn, 'OCCURRED_IN', occurred)}")

    violations = kuzu_store.check_provenance(conn)
    if violations:
        sys.exit("PROVENANCE VIOLATIONS:\n" + "\n".join(violations))
    print("provenance: ok")


if __name__ == "__main__":
    main()
