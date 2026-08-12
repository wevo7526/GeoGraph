"""Seed the graph from a region pack: sources FIRST (provenance ordering),
then regimes, actors, issues, markets, and the marquee spine with its edges,
coded by classifier Head B.

  python scripts/apply_schema.py && python scripts/seed_pack.py mena

Deterministic and idempotent — re-running merges. Stop the API first (Kuzu is
single-writer). Ends with check_provenance and FAILS on any violation; a seed
that cannot cite itself does not get to exist.

`seed()` is the library entry point (scripts/boot.py calls it at container
start) and RAISES SeedError; only `main()` turns that into an exit code, so a
failed seed can be reported by a caller that must keep serving.
"""

from __future__ import annotations

import sys
from typing import Any

from core import packs
from core import settings as settings_module
from core.classifier import escalation
from core.classifier import typing as event_typing
from core.graph import kuzu_store
from core.reasoning import regimes


class SeedError(RuntimeError):
    """The pack could not be seeded. The message names what and why."""


def _written(conn: Any, rel: str, rows: list[dict[str, Any]]) -> int:
    """Count edges back OUT of the graph, and refuse to under-report.

    `merge_edges` MATCHes both endpoints; a MATCH that finds nothing writes
    nothing, yet the row still counted as attempted. On Railway that gap would
    read as a clean seed over a hollow graph. So every batch is counted back
    out — deduplicated by (src, dst) first, because that is exactly the
    identity Kuzu's MERGE uses for these property-keyed-nothing edges.
    """
    intended = len({(r["src"], r["dst"]) for r in rows})
    actual = kuzu_store.query(conn, f"MATCH ()-[r:{rel}]->() RETURN count(*) AS n")[0]["n"]
    if actual < intended:
        raise SeedError(
            f"{rel}: {intended} edge(s) intended, {actual} in the graph. A MERGE whose "
            "endpoints do not both exist writes nothing — the missing nodes were not "
            "seeded first, or an id does not match."
        )
    return intended


def seed(conn: Any, pack: packs.Pack) -> dict[str, int]:
    """Write one pack into the graph. Returns counts per table; raises SeedError.

    Idempotent: every write is a MERGE keyed on node_id or on the edge's key
    slots, so a second run over the same pack changes nothing.
    """
    kuzu_store.apply_schema(conn)
    counts: dict[str, int] = {}

    # Sources before anything that cites them — the ordering IS the invariant.
    counts["sources"] = kuzu_store.merge_nodes(conn, "Source", [
        {"node_id": s["id"], "name": s["name"], "kind": s.get("kind", "dataset"),
         "url": s.get("url", ""), "citation": s.get("citation", "")}
        for s in pack.data["sources"].get("sources", [])
    ])
    counts["regimes"] = kuzu_store.merge_nodes(conn, "Regime", regimes.as_nodes())
    counts["actors"] = kuzu_store.merge_nodes(conn, "Actor", [
        {"node_id": a["id"], "name": a["name"], "actor_type": a["actor_type"],
         "cow_ccode": a.get("cow_ccode"), "iso3": a.get("iso3", ""),
         "region_pack": pack.name}
        for a in pack.actors
    ])
    counts["issues"] = kuzu_store.merge_nodes(conn, "Issue", [
        {"node_id": i["id"], "name": i["name"], "scale": i.get("scale", ""),
         "region_pack": pack.name}
        for i in pack.data["issues"].get("issues", [])
    ])
    counts["markets"] = kuzu_store.merge_nodes(conn, "Market", [
        {"node_id": m["id"], "name": m["name"], "ticker": m["ticker"],
         "market_type": m["market_type"], "trading_calendar": m["trading_calendar"],
         "calendar_eras": m.get("calendar_eras", ""),
         "inception_date": m["inception_date"],
         "native_frequency": m.get("native_frequency", ""), "region_pack": pack.name}
        for m in pack.markets
    ])

    # The durable network: RELATES_TO rows declared beside the roster. Actors
    # are already merged, sources before them — both endpoints and the cited
    # source exist by the time these edges write (the ordering IS the
    # invariant). key_slots (relation_type, valid_from) come from the
    # ontology, so the same dyad may carry a proxy edge and a rivalry edge.
    relates = [
        {
            "src": r["a"], "dst": r["b"], "relation_type": r["relation_type"],
            "valid_from": str(r["valid_from"]), "valid_to": str(r.get("valid_to") or ""),
            "source_id": r["source"],
        }
        for r in pack.relations
    ]
    kuzu_store.merge_edges(conn, "RELATES_TO", relates)
    counts["RELATES_TO"] = _written(conn, "RELATES_TO", relates)

    pack_source = f"source:{pack.name}-marquee"
    events: list[dict[str, Any]] = []
    initiated: list[dict[str, Any]] = []
    directed: list[dict[str, Any]] = []
    derived: list[dict[str, Any]] = []
    stream: list[dict[str, Any]] = []
    for e in pack.marquee_events:
        cameo = str(e.get("cameo") or "").strip()
        if not cameo:
            raise SeedError(
                f"{e['id']} carries no CAMEO code. The spine is the archive's CODED "
                "backbone — an uncoded event has no Goldstein score, so Head B cannot "
                "measure it and it would sit in the graph unclassifiable."
            )
        events.append({
            "node_id": e["id"], "name": e["name"], "event_time": e["date"],
            "action_cameo_code": cameo,
            # Both DERIVED from the code (crosswalks/cameo_goldstein.yaml).
            # packs.load has already refused any pack whose declared quad_class
            # contradicts its own code, so deriving here cannot silently
            # disagree with what the curator wrote.
            "goldstein": event_typing.goldstein_for(cameo),
            "quad_class": event_typing.quad_class_for(cameo),
            "region_pack": pack.name,
            "fidelity_tier": "modern_coded", "temporal_resolution": "day",
            "source_scale": "goldstein",
        })
        # An event with no named target is one-sided — a revolution, a protest.
        # Its dyad is the initiator with itself: an internal rupture still has
        # a relationship whose normal it departs from.
        actor_a = e.get("initiator") or e.get("target")
        if actor_a:
            stream.append({
                "node_id": e["id"], "event_time": e["date"],
                "goldstein": events[-1]["goldstein"],
                "actor_a": actor_a, "actor_b": e.get("target") or actor_a,
            })
        if e.get("initiator"):
            initiated.append({"src": e["id"], "dst": e["initiator"], "source_id": pack_source})
        if e.get("target"):
            directed.append({"src": e["id"], "dst": e["target"], "source_id": pack_source})
        derived.append({"src": e["id"], "dst": pack_source})

    # Classifier Head B (build-spec §10), deterministic and relational: fold
    # the spine into per-dyad EWMA baselines IN TIME ORDER. The escalation
    # slots land ON the Event nodes (§8.2) and each event links to the dyad
    # whose normal it was measured against.
    coding = escalation.code_events(
        stream, names={a["id"]: a["name"] for a in pack.actors}
    )
    coded = {row["node_id"]: row for row in coding.events}
    for row in events:
        result = coded.get(row["node_id"])
        if result:
            row.update({k: v for k, v in result.items() if k.startswith("escalation_")})

    # Dyads BEFORE the OF_DYAD edges that cite them, for the same reason
    # sources come before the edges that cite them: merge_edges MATCHes both
    # endpoints, and a MATCH that finds nothing writes nothing.
    counts["dyads"] = kuzu_store.merge_nodes(conn, "Dyad", coding.dyads)
    counts["events"] = kuzu_store.merge_nodes(conn, "Event", events)

    occurred: list[dict[str, Any]] = []
    for e in pack.marquee_events:
        for regime in regimes.regimes_at(e["date"]).values():
            if regime:
                occurred.append({"src": e["id"], "dst": f"regime:{regime['id']}"})

    for rel, rows in (
        ("INITIATED_BY", initiated),
        ("DIRECTED_AT", directed),
        ("DERIVED_FROM", derived),
        ("OF_DYAD", [{"src": r["node_id"], "dst": r["dyad_id"]} for r in coding.events]),
        ("OCCURRED_IN", occurred),
    ):
        kuzu_store.merge_edges(conn, rel, rows)
        counts[rel] = _written(conn, rel, rows)

    violations = kuzu_store.check_provenance(conn)
    if violations:
        raise SeedError("PROVENANCE VIOLATIONS:\n" + "\n".join(violations))
    return counts


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "mena"
    settings = settings_module.load()
    settings.kuzu_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = kuzu_store.connect(settings.kuzu_db_path)
    try:
        counts = seed(conn, packs.load(name))
    except (SeedError, packs.PackError) as exc:
        sys.exit(str(exc))
    finally:
        kuzu_store.close(conn)
    for table, count in counts.items():
        print(f"{table}: {count}")
    print("provenance: ok")


if __name__ == "__main__":
    main()
