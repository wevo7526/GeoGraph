"""The Phase 0 HTTP surface, against a real seeded graph in a temp directory.

No Postgres and no network: the AFFECTED edges are written directly with
synthetic EffectResults, because what these tests check is the API's contract —
what it exposes, what it refuses, and whether it can tell "not measured" from
"no effect" — not the arithmetic, which test_event_study.py pins.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core import packs
from core.graph import kuzu_store
from core.transmission import effects as effects_writer
from core.transmission.event_study import EffectResult

_ROOT = Path(__file__).resolve().parent.parent


def _load_seed() -> Any:
    spec = importlib.util.spec_from_file_location(
        "seed_pack", _ROOT / "scripts" / "seed_pack.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _effect(event: str, ticker: str, window: str, **kwargs: Any) -> EffectResult:
    return EffectResult(
        event_node_id=event, market_ticker=ticker, window=window, resolution="day",
        raw_return=kwargs.get("raw", -0.13), expected_return=-0.0006,
        abnormal_return=kwargs.get("abn", -0.1319), t_stat=kwargs.get("t", -5.32),
        p_value=kwargs.get("p", 0.0000005),
        first_mover=kwargs.get("first", False), overlapping=kwargs.get("ov", False),
        method="constant-mean;est=120day;test",
    )


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "api.kuzu"
    monkeypatch.setenv("KUZU_DB_PATH", str(db))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GEOGRAPH_BOOT_STATUS", raising=False)

    pack = packs.load("mena")
    conn = kuzu_store.connect(db)
    _load_seed().seed(conn, pack)
    effects_writer.write_effects(
        conn,
        [
            _effect("event:mena-2025-midnight-hammer", "BZ=F", "car_0_1"),
            _effect("event:mena-2025-midnight-hammer", "^TASI.SR", "car_0_1",
                    abn=0.0113, t=0.82, p=0.4157, first=True),
            _effect("event:mena-2025-rising-lion", "BZ=F", "car_0_5", ov=True),
        ],
        market_node_ids={m["ticker"]: m["id"] for m in pack.markets},
        source_id="source:yfinance",
    )
    # CLOSE, not `del`: dropping the reference releases neither the write
    # lock the app is about to need nor the database's virtual reservation.
    kuzu_store.close(conn)

    from core.api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


# ── health ───────────────────────────────────────────────────────────────────


def test_health_is_200_and_names_what_is_switched_off(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["graph"] == "open"
    # A capability that is unconfigured is REPORTED, not silently missing.
    assert "panel" in body["disabled"]
    assert body["boot"] is None


def test_health_survives_a_graph_it_cannot_open(tmp_path, monkeypatch):
    # THE CONTAINER RULE: health must answer even when nothing else can, or a
    # broken graph becomes a restart loop instead of a diagnosis.
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "nope.kuzu"))
    monkeypatch.setattr(
        kuzu_store, "connect",
        lambda *a, **k: (_ for _ in ()).throw(kuzu_store.GraphUnavailable("boom")),
    )
    from core.api.app import create_app

    with TestClient(create_app()) as broken:
        body = broken.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["graph"] == "unavailable"
        assert "boom" in body["graphError"]
        # And a data endpoint says 503 rather than pretending the graph is empty.
        assert broken.get("/api/events").status_code == 503


def test_the_boot_status_is_surfaced_when_present(tmp_path, monkeypatch):
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "boot.kuzu"))
    monkeypatch.setenv("GEOGRAPH_BOOT_STATUS", '{"seeded": true, "study": {"ok": false}}')
    from core.api.app import create_app

    with TestClient(create_app()) as booted:
        assert booted.get("/api/health").json()["boot"]["study"] == {"ok": False}


# ── events ───────────────────────────────────────────────────────────────────


def test_events_come_back_in_time_order_with_their_coding(client):
    body = client.get("/api/events").json()
    times = [row["event_time"] for row in body["rows"]]
    assert times == sorted(times)
    for row in body["rows"]:
        assert row["goldstein"] is not None
        assert row["escalation_direction"] in {"escalating", "stable", "deescalating"}


def test_coverage_counts_the_archive_by_year(client):
    # Route-order regression guard too: /events/coverage must hit the
    # aggregate, not fall into /events/{node_id:path} as a 404.
    body = client.get("/api/events/coverage").json()
    assert body["total"] == sum(body["years"].values()) > 0
    assert body["years"]["1973"] == 1  # the embargo
    year_of = client.get("/api/events").json()["rows"][0]["event_time"][:4]
    assert year_of in body["years"]


def test_a_date_range_filters_lexically(client):
    body = client.get("/api/events?start=2025-01-01&end=2025-12-31").json()
    assert {r["node_id"] for r in body["rows"]} == {
        "event:mena-2025-rising-lion", "event:mena-2025-midnight-hammer",
    }


def test_the_row_cap_is_reported_rather_than_silent(client):
    body = client.get("/api/events?limit=2").json()
    assert len(body["rows"]) == 2
    assert body["truncated"] is True


def test_one_event_carries_its_actors_dyad_regimes_and_sources(client):
    body = client.get("/api/events/event:mena-2025-midnight-hammer").json()
    assert body["initiator"]["node_id"] == "actor:cow-2"
    assert body["target"]["node_id"] == "actor:cow-630"
    assert body["dyad"]["node_id"] == "dyad:cow-2--cow-630"
    assert body["regimes"], "an event with no regime cannot be reasoned about by analogy"
    assert body["sources"], "the provenance invariant should be visible over HTTP"


def test_an_unknown_event_is_a_404_not_an_empty_object(client):
    assert client.get("/api/events/event:nope").status_code == 404


# ── effects ──────────────────────────────────────────────────────────────────


def test_effects_expose_the_numbers_and_the_method(client):
    body = client.get("/api/events/event:mena-2025-midnight-hammer/effects").json()
    assert body["measured"] == 2
    brent = next(r for r in body["rows"] if r["ticker"] == "BZ=F")
    assert brent["abnormal_return"] == pytest.approx(-0.1319)
    assert brent["p_value"] < 0.001
    assert "constant-mean" in brent["method"]
    assert brent["source_id"] == "source:yfinance"


def test_an_unmeasured_event_reports_zero_measured_rather_than_no_effect(client):
    # The distinction the whole archive rests on: nothing measured is not the
    # same claim as no effect.
    body = client.get("/api/events/event:mena-1973-embargo/effects").json()
    assert body["measured"] == 0
    assert body["rows"] == []


def test_the_overlap_flag_survives_to_the_api(client):
    body = client.get("/api/events/event:mena-2025-rising-lion/effects").json()
    assert [r["overlapping"] for r in body["rows"]] == [True]


def test_the_first_mover_flag_survives_to_the_api(client):
    body = client.get("/api/events/event:mena-2025-midnight-hammer/effects").json()
    movers = {r["ticker"]: r["first_mover"] for r in body["rows"]}
    assert movers == {"^TASI.SR": True, "BZ=F": False}


# ── escalation ───────────────────────────────────────────────────────────────


def test_a_dyad_trajectory_is_ordered_and_carries_its_baseline(client):
    body = client.get("/api/escalation/dyad:cow-2--cow-630").json()
    assert body["name"] == "United States – Iran"
    times = [e["event_time"] for e in body["events"]]
    assert times == sorted(times)
    # The JCPOA sets a cooperative baseline the later ruptures are measured
    # against — that is what makes escalation relational rather than absolute.
    first, second = body["events"][0], body["events"][1]
    assert first["goldstein"] > 0
    assert second["escalation_baseline"] == first["goldstein"]
    assert second["escalation_direction"] == "escalating"


def test_an_unknown_dyad_is_a_404(client):
    assert client.get("/api/escalation/dyad:nope--nope").status_code == 404


def test_dyads_are_listed_most_conflictual_first(client):
    rows = client.get("/api/dyads").json()["rows"]
    baselines = [r["ewma_baseline"] for r in rows]
    assert baselines == sorted(baselines)


# ── the case study ───────────────────────────────────────────────────────────


def test_the_case_study_lists_itself(client):
    # Every pack that declares a study appears, keyed to the pack that owns
    # it. Asserted as a rule over the packs rather than as a fixed roster, so
    # region four does not fail a test about region one.
    rows = client.get("/api/case-studies").json()["rows"]
    expected = {
        pack.case_study["slug"]: name
        for name in packs.available()
        if (pack := packs.load(name)).case_study is not None
    }
    assert {r["slug"]: r["pack"] for r in rows} == expected
    assert expected["twelve-day-war"] == "mena"


def test_the_case_study_pairs_the_prose_with_the_measured_numbers(client):
    body = client.get("/api/case-studies/twelve-day-war").json()
    assert body["title"] == "The Twelve-Day War"
    for prose in ("dek", "summary", "reading", "caveat"):
        assert body[prose].strip(), f"{prose} should carry the pack's own words"
    assert [e["node_id"] for e in body["episodes"]] == [
        "event:mena-2025-rising-lion", "event:mena-2025-midnight-hammer",
    ]
    assert body["status"] == "measured"
    assert body["measured"] == 3


def test_the_case_study_reports_the_first_movers_it_measured(client):
    body = client.get("/api/case-studies/twelve-day-war").json()
    hammer = next(
        e for e in body["episodes"] if e["node_id"] == "event:mena-2025-midnight-hammer"
    )
    assert hammer["first_movers"] == ["^TASI.SR"]
    assert hammer["escalation_direction"] == "escalating"


def test_an_unmeasured_case_study_says_so_instead_of_narrating(tmp_path, monkeypatch):
    # A story with no numbers under it must not read like a finding.
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "bare.kuzu"))
    conn = kuzu_store.connect(tmp_path / "bare.kuzu")
    _load_seed().seed(conn, packs.load("mena"))
    kuzu_store.close(conn)
    from core.api.app import create_app

    with TestClient(create_app()) as bare:
        body = bare.get("/api/case-studies/twelve-day-war").json()
        assert body["measured"] == 0
        assert body["status"] == "not_yet_measured"


def test_an_unknown_case_study_names_the_ones_that_exist(client):
    body = client.get("/api/case-studies/nope")
    assert body.status_code == 404
    assert "twelve-day-war" in body.json()["detail"]


# ── unbuilt layers ───────────────────────────────────────────────────────────


def test_forecasts_serve_empty_before_any_freeze(client):
    # Built with the reasoning layer: an archive nobody has frozen a call
    # over answers with an empty trail — never a 501, never an invention.
    response = client.get("/api/forecasts")
    assert response.status_code == 200
    assert response.json() == {"rows": []}
    assert client.get("/api/forecasts/forecast:nope").status_code == 404


def test_network_metrics_serves_empty_before_any_window_is_computed(client):
    # Built in Phase 2: an uncomputed archive answers with an empty row set —
    # a fact about computation coverage — never a 501 and never an error.
    response = client.get("/api/network/metrics")
    assert response.status_code == 200
    assert response.json() == {"rows": [], "truncated": False}


def test_an_unknown_api_route_is_a_json_404_not_the_spa(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_the_lens_keeps_the_global_backbone(tmp_path, monkeypatch):
    # A pack filter shows the region's tagged events PLUS untagged global
    # records — the deep tier belongs to every lens that includes its actors.
    # Standalone graph: the shared fixture's app already holds the write lock.
    db = tmp_path / "lens.kuzu"
    monkeypatch.setenv("KUZU_DB_PATH", str(db))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    conn = kuzu_store.connect(db)
    _load_seed().seed(conn, packs.load("mena"))
    kuzu_store.merge_nodes(conn, "Event", [{
        "node_id": "event:test-global", "name": "Global backbone record",
        "event_time": "2020-06-15", "action_cameo_code": "190",
        "goldstein": -10.0, "quad_class": "material_conflict",
        "region_pack": "", "fidelity_tier": "deep_structured",
        "temporal_resolution": "day", "source_scale": "cow_hostility",
    }])
    kuzu_store.close(conn)

    from core.api.app import create_app

    with TestClient(create_app()) as lens_client:
        ids = {
            r["node_id"]
            for r in lens_client.get(
                "/api/events?pack=mena&start=2020-01-01&end=2020-12-31"
            ).json()["rows"]
        }
        assert "event:test-global" in ids
        body = lens_client.get("/api/events/coverage?pack=mena").json()
        assert body["years"].get("2020", 0) >= 1


# ── the static surface: containment and cache discipline ─────────────────────

_DIST = _ROOT / "web" / "dist"
_needs_dist = pytest.mark.skipif(
    not (_DIST / "index.html").exists(), reason="web/dist not built"
)


@_needs_dist
def test_the_spa_never_serves_a_file_outside_dist(client):
    # A percent-encoded '..' survives HTTP normalisation and reaches the
    # handler literally; before the containment check it resolved to any file
    # the process could read — source, the graph on the volume, secrets.
    secret = _ROOT / "web" / "spa-traversal-canary.txt"
    secret.write_text("CANARY", encoding="utf-8")
    try:
        response = client.get("/%2e%2e/spa-traversal-canary.txt")
        assert response.status_code == 200
        assert "CANARY" not in response.text  # served index.html instead
    finally:
        secret.unlink()


@_needs_dist
def test_index_html_is_no_cache_and_hashed_assets_are_immutable(client):
    # index.html without an explicit Cache-Control is heuristically cached by
    # browsers, so a deploy leaves readers on a stale page pointing at hashed
    # bundles the new container no longer ships. The bundles themselves are
    # content-hashed — new content is a new URL — so they cache forever.
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/relationship").headers["cache-control"] == "no-cache"
    asset = next((_DIST / "assets").glob("index-*.js")).name
    assert (
        client.get(f"/assets/{asset}").headers["cache-control"]
        == "public, max-age=31536000, immutable"
    )


# ── non-finite measurements never reach a JSON boundary ──────────────────────


def test_nan_measurements_become_null_at_both_boundaries(tmp_path):
    # A zero-variance estimation window yields t_stat = nan by construction.
    # Starlette renders JSON with allow_nan=False, so ONE NaN on an AFFECTED
    # edge used to 500 every read touching it. The writer nulls it now; the
    # read boundary (_plain) covers edges written before the fix.
    db = tmp_path / "nan.kuzu"
    pack = packs.load("mena")
    conn = kuzu_store.connect(db)
    try:
        _load_seed().seed(conn, pack)
        effects_writer.write_effects(
            conn,
            [_effect("event:mena-2025-midnight-hammer", "BZ=F", "car_0_1",
                     t=float("nan"), p=float("nan"))],
            market_node_ids={m["ticker"]: m["id"] for m in pack.markets},
            source_id="source:yfinance",
        )
        rows = kuzu_store.query(
            conn,
            "MATCH (e:Event {node_id: $id})-[a:AFFECTED]->(:Market) "
            "RETURN a.t_stat AS t, a.p_value AS p",
            {"id": "event:mena-2025-midnight-hammer"},
        )
        assert rows and rows[0]["t"] is None and rows[0]["p"] is None
        # The read boundary alone, for legacy NaN already on a volume.
        raw = kuzu_store.query(conn, "RETURN sqrt(-1.0) AS bad")
        assert raw[0]["bad"] is None
    finally:
        kuzu_store.close(conn)


# ── the explorer reads the graph ∪ the wire corpus (the post-2022 gap) ────────

_HAS_CORPUS = bool(__import__("core.wire.corpus", fromlist=["installed"]).installed())
_needs_corpus = pytest.mark.skipif(not _HAS_CORPUS, reason="no wire artifacts in checkout")


@_needs_corpus
def test_events_include_wire_years_the_graph_never_merged(real_corpus, client):
    # THE BUG THE USER HIT: the graph holds the spine and whatever wire years a
    # loading boot merged (~through 2022); the corpus runs to the present. A
    # graph-only read went silent after 2022. The union restores the tail.
    # (real_corpus opts this test into the shipped artifacts; the suite
    # otherwise runs corpus-free — see conftest.)
    body = client.get("/api/events?start=2024-01-01&end=2024-12-31&limit=500&pack=mena").json()
    assert body["rows"], "no events after 2023 — the corpus tail is missing"
    assert all(r["event_time"].startswith("2024") for r in body["rows"])
    # The wire years the graph never held are present too — a 2026 window that
    # a graph-only read returned nothing for.
    tail = client.get("/api/events?start=2026-01-01&end=2026-12-31&limit=50&pack=mena").json()
    assert tail["rows"] and all(r["event_time"].startswith("2026") for r in tail["rows"])
    # And the coverage strip counts those years, so the slider shows them.
    years = client.get("/api/events/coverage?pack=mena").json()["years"]
    assert int(years.get("2025", 0)) > 0 and int(years.get("2026", 0)) > 0


@_needs_corpus
def test_a_wire_only_event_resolves_its_detail_from_the_corpus(real_corpus, client):
    listed = client.get(
        "/api/events?start=2025-01-01&end=2025-12-31&limit=5&pack=mena"
    ).json()["rows"]
    assert listed, "no 2025 wire events to detail"
    node_id = listed[0]["node_id"]
    detail = client.get(f"/api/events/{node_id}").json()
    assert detail["node_id"] == node_id
    assert detail["name"] and detail["event_time"].startswith("2025")
    # Actor names resolve through the pack roster, not left as raw ids.
    if detail["initiator"]:
        assert detail["initiator"]["name"] != detail["initiator"]["node_id"]
