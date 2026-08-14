"""The wire corpus: derived schema, faithful parse, and the row contract.

The corpus is the model's and the game's only input, so the thing worth
pinning is that it says the SAME thing the graph path said. These tests
compare it against the definitions it must not drift from — the ontology, the
shared parser, and Head B — rather than against numbers copied out of a run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core import packs
from core.classifier import escalation
from core.ontology import kuzu_schema, pg_schema
from core.wire import corpus

_ROOT = Path(__file__).resolve().parent.parent


def _a_pack_with_artifacts() -> str:
    installed = corpus.installed()
    if not installed:
        pytest.skip("no derived artifacts in this checkout")
    return installed[0]


# ── the schema is DERIVED, not written ────────────────────────────────────

def test_the_wire_table_is_derived_from_the_ontology():
    # THE INVARIANT THE SECOND STORE MUST NOT BREAK. If these columns were
    # hand-written, adding a slot to the ontology would silently produce two
    # stores describing different things. Every non-link column must come from
    # the Event class.
    event_slots = {p.name for p in kuzu_schema.nodes()["Event"].props}
    links = {name for name, _ in pg_schema._LINK_COLUMNS}
    derived = {c.name for c in pg_schema.wire_spec().columns}
    assert derived, "the Event class produced no columns"
    assert derived <= event_slots, (
        f"{derived - event_slots} exist in Postgres but not in the ontology"
    )
    # The one deliberate omission, and it is a Kuzu storage detail rather than
    # a fact: the vector index has no counterpart here.
    assert "embedding" not in derived
    assert links.isdisjoint(event_slots), "a link column shadows an ontology slot"


def test_the_corpus_columns_include_the_primary_key():
    # COPY cannot omit it, and deriving the list is what keeps the loader from
    # drifting off the schema.
    assert pg_schema.columns()[0] == "node_id"
    assert set(pg_schema.columns()) >= {"dyad_id", "source_id", "event_time"}


def test_provenance_is_a_real_foreign_key():
    # `kuzu_schema.validate_edge` exists because Kuzu has no NOT NULL on rel
    # properties. Postgres does, so the invariant is the database's job here —
    # and that is only true while the constraint is actually in the DDL.
    statements = "\n".join(pg_schema.ddl())
    assert "source_id TEXT NOT NULL REFERENCES wire_source(source_id)" in statements
    assert statements.index("CREATE TABLE IF NOT EXISTS wire_source") < statements.index(
        "CREATE TABLE IF NOT EXISTS wire_event"
    ), "sources must be created before the events that cite them"


def test_the_daily_assumption_is_enforced_not_assumed():
    # event_time is DATE here and STRING in the graph. That is only correct
    # while every row is a daily wire event, so the database refuses the rest
    # rather than leaving it to a comment.
    assert "CHECK (temporal_resolution = 'day')" in "\n".join(pg_schema.ddl())


# ── the parse is the SAME parse ───────────────────────────────────────────

def test_the_corpus_reuses_the_shared_parser_faithfully(real_corpus):
    # A second implementation of the CAMEO filter is a second archive with
    # different contents. Every row must carry the tier the parser assigns.
    name = _a_pack_with_artifacts()
    pack = packs.load(name)
    artifact = corpus.artifacts_for(name)[0]
    rows, result = corpus.parse_artifact(pack, artifact)
    assert rows, "the first artifact produced no rows"
    assert result.dropped == 0 or result.written == len(rows)
    for row in rows[:200]:
        assert row["fidelity_tier"] == "modern_coded"
        assert row["temporal_resolution"] == "day"
        assert row["source_scale"] == "goldstein"
        assert row["region_pack"] == name


def test_the_dyad_key_matches_the_graphs_own_function(real_corpus):
    # Two stores that disagree about what a dyad is would split one rivalry's
    # history in half. The key is computed by the SAME pure function, so this
    # asserts the loader calls it rather than reimplementing the sort.
    name = _a_pack_with_artifacts()
    pack = packs.load(name)
    rows, _ = corpus.parse_artifact(pack, corpus.artifacts_for(name)[0])
    for row in rows[:200]:
        assert row["dyad_id"] == escalation.dyad_id(
            row["initiator_id"], row["target_id"]
        )
        # SORTED, and therefore unordered — swapping the ends is the same dyad.
        assert row["dyad_id"] == escalation.dyad_id(
            row["target_id"], row["initiator_id"]
        )


# ── Head B says the same thing here ───────────────────────────────────────

def test_scoring_matches_head_b_event_for_event():
    # `score()` must BE DyadTracker, not resemble it. Replaying the tracker
    # independently over the same ordered rows is the only check that catches
    # a drift in the fold.
    rows: list[dict[str, Any]] = [
        {"node_id": "event:c", "dyad_id": "dyad:x", "event_time": "2020-01-03",
         "goldstein": -6.0},
        {"node_id": "event:a", "dyad_id": "dyad:x", "event_time": "2020-01-01",
         "goldstein": 2.0},
        {"node_id": "event:b", "dyad_id": "dyad:y", "event_time": "2020-01-02",
         "goldstein": 1.0},
    ]
    scored = corpus.score([dict(r) for r in rows])

    tracker = escalation.DyadTracker()
    expected = []
    for row in sorted(rows, key=lambda r: (r["event_time"], r["node_id"])):
        expected.append(tracker.observe(row["dyad_id"], float(row["goldstein"])))
    for got, want in zip(scored, expected, strict=True):
        assert got["escalation_baseline"] == want["escalation_baseline"]
        assert got["escalation_direction"] == want["escalation_direction"]
        assert got["escalation_magnitude"] == want["escalation_magnitude"]


def test_a_dyads_first_event_is_its_own_baseline():
    # There is no global prior, because a global normal is exactly what
    # relational escalation refuses to assume.
    scored = corpus.score([
        {"node_id": "event:a", "dyad_id": "dyad:x", "event_time": "2020-01-01",
         "goldstein": -8.0},
    ])
    assert scored[0]["escalation_baseline"] == -8.0
    assert scored[0]["escalation_magnitude"] == 0.0
    assert scored[0]["escalation_direction"] == "stable"


def test_scoring_is_time_ordered_regardless_of_input_order():
    # The baseline is history, and history has an order.
    unordered = [
        {"node_id": "event:b", "dyad_id": "dyad:x", "event_time": "2021-01-01",
         "goldstein": -5.0},
        {"node_id": "event:a", "dyad_id": "dyad:x", "event_time": "2020-01-01",
         "goldstein": 5.0},
    ]
    scored = corpus.score(unordered)
    assert [r["event_time"] for r in scored] == ["2020-01-01", "2021-01-01"]
    assert scored[0]["escalation_baseline"] == 5.0
    # The second event is measured against the first, so a swing toward
    # conflict reads as escalating rather than as its own baseline.
    assert scored[1]["escalation_direction"] == "escalating"


# ── the row contract the consumers depend on ──────────────────────────────

def test_the_panel_view_matches_what_the_panel_reads(real_corpus):
    # Identical keys to `models.panel.dyad_event_rows`, which is what lets the
    # fitters read either source without knowing which one they got.
    name = _a_pack_with_artifacts()
    pack = packs.load(name)
    rows, _ = corpus.parse_artifact(pack, corpus.artifacts_for(name)[0])
    view = corpus._as_panel_row(corpus.score(rows)[0])
    assert set(view) == {
        "dyad_id", "dyad_name", "event_time", "direction",
        "magnitude", "goldstein", "quad_class", "region_pack",
    }


def test_the_game_view_carries_both_sides_and_the_initiator(real_corpus):
    # The panel aggregates a dyad-quarter into one intensity; a game needs to
    # know which SIDE did what.
    name = _a_pack_with_artifacts()
    pack = packs.load(name)
    rows, _ = corpus.parse_artifact(pack, corpus.artifacts_for(name)[0])
    view = corpus._as_game_row(rows[0])
    assert set(view) == {
        "dyad_id", "actor_a", "actor_b", "initiator",
        "event_time", "quad_class", "region_pack",
    }
    assert view["actor_a"] < view["actor_b"], "the pair is stored sorted"
    assert view["initiator"] in {view["actor_a"], view["actor_b"]}


def test_both_views_come_from_one_parse(real_corpus):
    # Two projections of the same rows; parsing twice would double the only
    # expensive step and let them drift.
    name = _a_pack_with_artifacts()
    panel_view, game_view = corpus.views(name)
    assert len(panel_view) == len(game_view)
    assert [r["dyad_id"] for r in panel_view[:50]] == [
        r["dyad_id"] for r in game_view[:50]
    ]
