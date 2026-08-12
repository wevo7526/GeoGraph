"""Network analytics (Phase 2): windowed export, deterministic metrics,
idempotent persistence. Runs on a real embedded graph seeded from the MENA
pack — no network, no Postgres."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from core import packs
from core.graph import analytics, kuzu_store

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_pack = _load("seed_pack")


@pytest.fixture()
def db_path(tmp_path):
    # Seeded once, closed, and handed over as a PATH: analytics opens its own
    # connections, and Kuzu is single-writer.
    path = tmp_path / "analytics.kuzu"
    conn = kuzu_store.connect(path)
    try:
        seed_pack.seed(conn, packs.load("mena"))
    finally:
        kuzu_store.close(conn)
    return path


_MODERN = analytics.Window("2020-01-01", "2026-12-31")


def test_the_modern_window_has_structure_and_flow(db_path):
    graph = analytics.export_subgraph(db_path, _MODERN)
    # The proxy web is durable structure...
    assert graph.has_edge("actor:cow-630", "actor:hezbollah")
    assert graph["actor:cow-630"]["actor:hezbollah"]["relation_type"] == "proxy"
    # ...and the twelve-day war is event flow.
    assert graph.has_edge("actor:cow-666", "actor:cow-630")
    assert graph["actor:cow-666"]["actor:cow-630"]["events"] >= 1


def test_a_relation_outside_its_validity_window_is_absent(db_path):
    # Iran–Ansar Allah begins 2014; a 1990s window must not contain it.
    nineties = analytics.Window("1990-01-01", "1999-12-31")
    graph = analytics.export_subgraph(db_path, nineties)
    assert not graph.has_edge("actor:cow-630", "actor:ansar-allah")
    # Iran–Hezbollah (1982) is already standing structure by then.
    assert graph.has_edge("actor:cow-630", "actor:hezbollah")


def test_an_actor_outside_its_membership_window_is_absent(db_path):
    # COW state-system membership is time-varying: a state that left the
    # system before the window opens is not in the network.
    conn = kuzu_store.connect(db_path)
    try:
        kuzu_store.merge_nodes(conn, "Actor", [{
            "node_id": "actor:cow-345", "name": "Yugoslavia", "actor_type": "state",
            "state_from": "1918-12-01", "state_to": "1992-04-27", "region_pack": "mena",
        }])
    finally:
        kuzu_store.close(conn)
    modern = analytics.export_subgraph(db_path, _MODERN)
    assert not modern.has_node("actor:cow-345")
    cold_war = analytics.export_subgraph(db_path, analytics.Window("1950-01-01", "1959-12-31"))
    assert cold_war.has_node("actor:cow-345")


def test_metrics_are_persisted_windowed_and_idempotent(db_path):
    first = analytics.compute_metrics(db_path, _MODERN)
    assert first > 0
    second = analytics.compute_metrics(db_path, _MODERN)
    assert second == first  # same window MERGEs onto itself

    conn = kuzu_store.connect(db_path, read_only=True)
    try:
        rows = kuzu_store.query(
            conn,
            "MATCH (m:NetworkMetric) RETURN m.node_id AS node_id, "
            "m.subject_id AS subject_id, m.metric_name AS metric_name, "
            "m.value AS value, m.method AS method",
        )
    finally:
        kuzu_store.close(conn)
    assert len(rows) == first  # a re-run did not multiply the facts
    for row in rows:
        assert row["method"], f"{row['node_id']}: no method — not reproducible"

    # Iran carries the proxy web AND the war flow — it must be a broker.
    betweenness = {
        r["subject_id"]: r["value"] for r in rows if r["metric_name"] == "betweenness"
    }
    assert betweenness["actor:cow-630"] > 0
    assert betweenness["actor:cow-630"] == max(betweenness.values())


def test_isolated_actors_are_not_measured(db_path):
    # A quiet fund with no edges in the window carries no structural
    # information; a wall of zeros would read as data.
    analytics.compute_metrics(db_path, _MODERN)
    conn = kuzu_store.connect(db_path, read_only=True)
    try:
        rows = kuzu_store.query(
            conn,
            "MATCH (m:NetworkMetric) WHERE m.subject_id = $s RETURN count(*) AS n",
            {"s": "actor:swf-adia"},
        )
    finally:
        kuzu_store.close(conn)
    assert rows[0]["n"] == 0


def test_the_report_reads_only_what_was_computed(db_path):
    analytics.compute_metrics(db_path, _MODERN)
    analytics.compute_metrics(db_path, analytics.Window("1970-01-01", "1979-12-31"))
    report = analytics.regime_shift_report(db_path)
    assert report, "two computed windows must yield a report"
    iran_degree = next(
        r for r in report
        if r["subject_id"] == "actor:cow-630" and r["metric_name"] == "degree"
    )
    starts = [w["start"] for w in iran_degree["windows"]]
    assert starts == sorted(starts)
