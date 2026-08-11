"""The store, end to end on a real embedded graph: DDL applies, merges are
idempotent, key slots make identity, the provenance chokepoint holds, and the
backstop catches what the chokepoint cannot."""

from __future__ import annotations

import pytest

from core.graph import kuzu_store
from core.ontology.kuzu_schema import OntologyError


@pytest.fixture()
def conn(tmp_path):
    connection = kuzu_store.connect(tmp_path / "test.kuzu")
    kuzu_store.apply_schema(connection)
    return connection


def _seed(conn):
    kuzu_store.merge_nodes(conn, "Source", [
        {"node_id": "source:test", "name": "Test source", "kind": "dataset",
         "url": "", "citation": ""},
    ])
    kuzu_store.merge_nodes(conn, "Actor", [
        {"node_id": "actor:a", "name": "Alpha", "actor_type": "state"},
        {"node_id": "actor:b", "name": "Beta", "actor_type": "state"},
    ])


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
