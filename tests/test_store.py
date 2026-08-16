"""The store, end to end on a real embedded graph: DDL applies, merges are
idempotent, key slots make identity, the provenance chokepoint holds, and the
backstop catches what the chokepoint cannot."""

from __future__ import annotations

from typing import Any

import pytest

from core.graph import kuzu_store
from core.ontology.kuzu_schema import OntologyError


@pytest.fixture()
def conn(tmp_path):
    # CLOSED after each test, not just dereferenced: each open Kuzu database
    # reserves an 8 TiB virtual allocation, so a suite that leaks them dies
    # around the fifteenth graph with a buffer-manager error.
    connection = kuzu_store.connect(tmp_path / "test.kuzu")
    kuzu_store.apply_schema(connection)
    yield connection
    kuzu_store.close(connection)


def _seed(conn: Any) -> None:
    kuzu_store.merge_nodes(conn, "Source", [
        {"node_id": "source:test", "name": "Test source", "kind": "dataset",
         "url": "", "citation": ""},
    ])
    kuzu_store.merge_nodes(conn, "Actor", [
        {"node_id": "actor:a", "name": "Alpha", "actor_type": "state"},
        {"node_id": "actor:b", "name": "Beta", "actor_type": "state"},
    ])


def test_apply_schema_adds_columns_the_model_gained(tmp_path):
    # THE RAILWAY VOLUME CASE: a graph created before the ontology gained a
    # property survives the deploy (that is what the volume is for), and
    # CREATE IF NOT EXISTS skips it — so without the ALTER pass the next seed
    # dies with a binder error naming the new column, and boot keeps serving
    # the STALE graph. Reproduced here by pre-creating Market one column
    # short of the current model.
    connection = kuzu_store.connect(tmp_path / "old.kuzu")
    try:
        from core.ontology import kuzu_schema as ontology

        market = ontology.nodes()["Market"]
        assert any(p.name == "calendar_eras" for p in market.props)
        stripped = ", ".join(
            f"{p.name} {p.kuzu_type}" for p in market.props if p.name != "calendar_eras"
        )
        connection.execute(
            f"CREATE NODE TABLE Market(node_id STRING, {stripped}, PRIMARY KEY(node_id))"
        )
        kuzu_store.apply_schema(connection)
        live = {
            row["name"]
            for row in kuzu_store.query(connection, "CALL table_info('Market') RETURN *")
        }
        assert "calendar_eras" in live
        # And the write that used to be refused now lands.
        kuzu_store.merge_nodes(connection, "Market", [{
            "node_id": "market:x", "name": "X", "ticker": "X", "market_type": "equity_index",
            "trading_calendar": "us", "calendar_eras": "", "inception_date": "2000-01-01",
            "native_frequency": "", "region_pack": "mena",
        }])
    finally:
        kuzu_store.close(connection)


def test_schema_applies_and_merge_is_idempotent(conn):
    _seed(conn)
    _seed(conn)  # merging twice is one node, not two
    rows = kuzu_store.query(conn, "MATCH (a:Actor) RETURN count(*) AS n")
    assert rows[0]["n"] == 2


def test_count_is_a_plain_int_not_a_decimal(conn):
    _seed(conn)
    n = kuzu_store.query(conn, "MATCH (a:Actor) RETURN count(*) AS n")[0]["n"]
    assert type(n) is int  # the _plain boundary — Decimal here becomes a JSON string


def test_edge_without_source_is_unwritable(conn):
    _seed(conn)
    with pytest.raises(OntologyError, match="provenance"):
        kuzu_store.merge_edges(conn, "RELATES_TO", [
            {"src": "actor:a", "dst": "actor:b", "relation_type": "alliance",
             "valid_from": "1949-04-04"},
        ])


def test_key_slots_make_two_windows_two_edges(conn):
    _seed(conn)
    for valid_from in ("1949-04-04", "1966-07-01"):
        kuzu_store.merge_edges(conn, "RELATES_TO", [
            {"src": "actor:a", "dst": "actor:b", "relation_type": "alliance",
             "valid_from": valid_from, "source_id": "source:test"},
        ])
    rows = kuzu_store.query(conn, "MATCH ()-[r:RELATES_TO]->() RETURN count(*) AS n")
    assert rows[0]["n"] == 2


def test_provenance_backstop_catches_a_ghost_source(conn):
    _seed(conn)
    kuzu_store.merge_edges(conn, "RELATES_TO", [
        {"src": "actor:a", "dst": "actor:b", "relation_type": "rivalry",
         "valid_from": "1979-01-16", "source_id": "source:ghost"},
    ])
    violations = kuzu_store.check_provenance(conn)
    assert any("source:ghost" in v for v in violations)


def test_provenance_clean_graph_reports_clean(conn):
    _seed(conn)
    kuzu_store.merge_edges(conn, "RELATES_TO", [
        {"src": "actor:a", "dst": "actor:b", "relation_type": "alliance",
         "valid_from": "1949-04-04", "source_id": "source:test"},
    ])
    assert kuzu_store.check_provenance(conn) == []


def test_unknown_tables_are_refused(conn):
    with pytest.raises(OntologyError):
        kuzu_store.merge_nodes(conn, "Wizard", [{"node_id": "w:1"}])
    with pytest.raises(OntologyError):
        kuzu_store.merge_edges(conn, "ENCHANTS", [{"src": "a", "dst": "b"}])


def test_the_page_cache_is_sized_to_the_container_not_the_host(tmp_path, monkeypatch):
    """THE OOM THAT BROKE THE DATABASE (2026-08-16).

    Kuzu sizes its buffer pool at 80% of what `sysconf(_SC_PHYS_PAGES)` reports,
    and inside a container that is the HOST's memory, not the cgroup limit the
    process is held to. On Railway the pool kept filling past the 8 GB limit
    until the kernel killed the process — mid-write, which is what corrupted
    the write-ahead log and left every graph endpoint on 503.

    So the pool is sized from the limit the process is ACTUALLY held to, and
    not all of it: this process also carries the wire corpus and the jobs.
    """
    limit = tmp_path / "memory.max"
    limit.write_text(str(8 << 30), encoding="utf-8")
    monkeypatch.setattr(kuzu_store, "CGROUP_LIMIT_FILES", (limit,))

    assert kuzu_store.container_memory_bytes() == 8 << 30
    pool = kuzu_store.buffer_pool_bytes()
    assert 0 < pool < (8 << 30), "the pool must leave the container room"
    assert pool == int((8 << 30) * kuzu_store.BUFFER_POOL_SHARE)

    # "max" and a v1 sentinel both mean unlimited — fall back to Kuzu's default.
    limit.write_text("max", encoding="utf-8")
    assert kuzu_store.container_memory_bytes() is None
    assert kuzu_store.buffer_pool_bytes() == 0

    # Not containerised at all: same fallback, no guessing.
    monkeypatch.setattr(kuzu_store, "CGROUP_LIMIT_FILES", (tmp_path / "nope",))
    assert kuzu_store.container_memory_bytes() is None


def test_an_unreplayable_write_ahead_log_is_moved_aside_not_kept(tmp_path):
    """A killed write must not make the database permanently unopenable.

    Kuzu replays its WAL on open. A process killed mid-commit can leave a tail
    that describes a write the last checkpoint ALREADY contains, and the replay
    then fails on the primary key it is re-inserting — production served
    `Found duplicated primary key value event:gdelt-455102626` on every open
    for 25 minutes. The data was never lost; only the tail could not be
    replayed.

    So the tail is renamed (evidence, and small) and the database opens at its
    checkpoint. That is only safe because every writer here is watermarked and
    idempotent — the jobs re-measure whatever the tail held.
    """
    db = tmp_path / "graph.kuzu"
    db.write_bytes(b"the checkpointed database")
    wal = tmp_path / "graph.kuzu.wal"
    wal.write_bytes(b"the tail that will not replay")
    shadow = tmp_path / "graph.kuzu.shadow"
    shadow.write_bytes(b"")

    moved = kuzu_store._quarantine_wal(db)

    assert moved is not None and moved.exists(), "the tail is kept as evidence"
    assert "broken" in moved.name
    assert not wal.exists(), "the unreplayable tail must not be replayed again"
    assert not shadow.exists(), "the shadow pages go with it"
    assert db.read_bytes() == b"the checkpointed database", "the data is untouched"
    assert kuzu_store._quarantine_wal(db) is None, "nothing to do twice"
