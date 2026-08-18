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
    # THE CORPUS KNOWS THINGS THE GRAPH CANNOT, and the contract is a
    # SUPERSET rather than an equality because of it. The graph's lean copy of
    # the wire holds no actor types and no source counts (they are corpus-only
    # projections, like `action_geo`), so the two flags derived from them ride
    # here and the graph reader `.get`s them — `models.panel.build` falls back
    # to the quad class when `coercion` is absent, which is what keeps an old
    # store readable instead of silently counting nothing.
    required = {
        "dyad_id", "dyad_name", "event_time", "direction",
        "magnitude", "goldstein", "quad_class", "region_pack",
    }
    derived = {"co_participation", "coercion"}
    assert required <= set(view), "the shared contract every source must meet"
    assert set(view) == required | derived


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
        # See the panel view above: derived from corpus-only columns.
        "co_participation", "coercion",
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


def test_pooled_reads_dedupe_shared_roster_events(real_corpus):
    # Overlapping rosters (mena and eurasia both hold USA/RUS/TUR) ship the
    # same wire event in two packs' artifacts — 627 shared ids in 2022 alone.
    # forecast_rows and all_panel_rows must count each event ONCE, or the
    # all-region ledger and the training pool double-count those dyads.
    installed = corpus.installed()
    if len(installed) < 2:
        pytest.skip("need at least two lenses to exercise the overlap")

    fr_ids = [r["event_id"] for r in corpus.forecast_rows()]
    assert len(fr_ids) == len(set(fr_ids)), "forecast_rows returned duplicate event ids"

    # all_panel_rows carries no id, so prove the dedupe by count: the pooled
    # length must be strictly less than the naive per-pack concatenation when
    # any id is shared.
    naive = sum(len(corpus.load(name)) for name in installed)
    pooled = len(corpus.all_panel_rows())
    per_pack_ids: set[str] = set()
    for name in installed:
        per_pack_ids.update(r["node_id"] for r in corpus.load(name))
    assert pooled == len(per_pack_ids), "all_panel_rows did not dedupe to distinct ids"
    if naive != len(per_pack_ids):
        assert pooled < naive, "shared ids exist but the pool did not shrink"


def test_the_corpus_rows_are_handed_over_one_at_a_time(monkeypatch):
    """THE READER IS AN ITERATOR, AND THAT IS THE WHOLE POINT.

    `serving`'s own docstring is the promise this keeps: "what is kept is the
    small derived shape, not the rows … parsing 1.33M events yields hundreds of
    megabytes of dicts". `rows_of` handed exactly those dicts back — a whole
    lens materialised at once — and its one caller then built a filtered list
    beside it. On 2026-08-17 that took the container from ~4 GB to its 8 GB
    ceiling within four seconds of "job: wire starting", four container lives
    in a row, ending in a CRASHED deployment and a site that was down until a
    human noticed.

    A list would pass every behavioural test this file has; only the type says
    the memory is bounded.
    """
    import types

    from core.wire import serving

    monkeypatch.setattr(serving, "_WARMED", True)
    monkeypatch.setattr(serving, "available", lambda: True)
    monkeypatch.setattr(serving, "_EVENTS", {
        "t": [serving._slim({
            "event_time": f"2020-01-0{i}", "node_id": f"event:gdelt-{i}",
            "name": "x", "action_cameo_code": "190", "quad_class": "material_conflict",
            "goldstein": -9.0, "escalation_direction": "escalating",
            "escalation_magnitude": 5.0, "escalation_baseline": -3.0,
            "fidelity_tier": "modern_coded", "temporal_resolution": "day",
            "source_scale": "goldstein", "region_pack": "t",
            "initiator_id": "actor:a", "target_id": "actor:b",
            "dyad_id": "dyad:a--b", "source_id": "source:gdelt",
        }) for i in range(1, 6)
    ]})

    stream = serving.iter_rows_of("t")
    assert isinstance(stream, types.GeneratorType), (
        "a list here is a whole lens in memory at once"
    )
    first = next(stream)
    assert first["node_id"] == "event:gdelt-1"
    assert first["goldstein"] == -9.0
    assert len(list(stream)) == 4, "and the rest arrive on demand"


def _row(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "quad_class": "material_conflict", "action_cameo_code": "190",
        "num_sources": 9, "actor1_type": "", "actor2_type": "",
        "action_geo": "UKR", "initiator_iso3": "RUS", "target_iso3": "UKR",
    }
    base.update(kw)
    return base


def test_coercion_between_states_is_not_the_quad_class():
    """WHAT MADE US-UK A MORE COERCIVE PAIR THAN US-RUSSIA.

    Measured over the shipped artifacts, last four quarters: US–UK 194
    material-conflict rows against US–Russia's 188. The composition says
    everything the count hides — US–UK's single largest contributor was CAMEO
    173, "arrest, detain or charge", 73 of the 194, on British or American
    soil; US–Russia's was 163, "impose embargo, boycott or sanctions", 41 of
    188, on Russian soil. British police arresting somebody is not the United
    Kingdom coercing the United States.
    """
    from core.classifier import coercion

    assert coercion.counts_as_coercion(_row(action_cameo_code="163"))
    assert coercion.counts_as_coercion(_row(action_cameo_code="190"))
    # Coercion applied to PERSONS — a country's own police blotter.
    assert not coercion.counts_as_coercion(_row(action_cameo_code="173"))
    assert not coercion.counts_as_coercion(_row(action_cameo_code="1741"))
    # One article is not corroboration; it is where "Poland vs the Associated
    # Press" came from.
    assert not coercion.counts_as_coercion(_row(num_sources=1))
    # An actor's country code is filled in for anyone with a nationality, so a
    # newspaper arrives wearing its country's name.
    assert not coercion.counts_as_coercion(_row(actor2_type="MED"))
    assert not coercion.counts_as_coercion(_row(actor1_type="BUS"))
    assert coercion.counts_as_coercion(_row(actor1_type="GOV", actor2_type="MIL"))
    # A row from a store that predates the quality columns degrades to the old
    # reading rather than to silence.
    old = _row()
    del old["num_sources"]
    assert coercion.counts_as_coercion(old)


def test_a_declared_ally_fighting_on_its_own_soil_is_presence_not_war():
    """The rule that had to be scoped, and the measurement that scoped it.

    Dropping every root-19 event on a pair's own soil flattered the ranking
    and deleted the war: Russia–Ukraine fell from 1,707 to 342, because that
    war is fought on Ukrainian soil. It survives only where a DECLARED DEFENCE
    PACT makes the reading safe.
    """
    from core.classifier import coercion

    war = _row(action_cameo_code="190", action_geo="UKR",
               initiator_iso3="RUS", target_iso3="UKR")
    assert coercion.counts_as_coercion(war, allied=False), "the war is real"
    assert coercion.counts_as_coercion(war, allied=True) is False

    partners = _row(action_cameo_code="190", action_geo="GBR",
                    initiator_iso3="USA", target_iso3="GBR")
    assert not coercion.counts_as_coercion(partners, allied=True)
    # Sanctions between allies are still coercion — only "fight" is read as
    # presence, and only on a partner's own ground.
    assert coercion.counts_as_coercion(
        _row(action_cameo_code="163", action_geo="GBR",
             initiator_iso3="USA", target_iso3="GBR"), allied=True)


def test_a_consult_is_not_an_actionable_live_kind():
    """The live wire's dump bucket must not ship as a trade.

    A Goldstein +1.0 consult is `stable` on the raw-score fallback. Head B
    against a nearby baseline is also `stable`. That cell is the region's
    ordinary day — attaching it as a high-confidence trade is how Iran–Iraq
    consultations shipped with "short Dubai / long the S&P".
    """
    from core.api.routers.events import _ACTIONABLE_KINDS, _implied_kind
    from core.reasoning import markets as markets_module

    assert _implied_kind(1.0) == "stable"
    assert _implied_kind(1.0) not in _ACTIONABLE_KINDS
    assert _implied_kind(-2.5) in _ACTIONABLE_KINDS
    assert _implied_kind(-7.0) in _ACTIONABLE_KINDS
    assert _implied_kind(3.0) in _ACTIONABLE_KINDS
    assert markets_module.kind_of("stable", 0.0) not in _ACTIONABLE_KINDS
    assert markets_module.kind_of("deescalating", 4.0) in _ACTIONABLE_KINDS
    assert markets_module.kind_of("de-escalating", 4.0) == "de-escalation"
