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
    rows = client.get("/api/case-studies").json()["rows"]
    assert [r["slug"] for r in rows] == ["twelve-day-war"]
    assert rows[0]["pack"] == "mena"


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


def test_unbuilt_endpoints_name_their_phase(client):
    response = client.get("/api/forecasts/scenarios")
    assert response.status_code == 501
    assert "Phase" in response.json()["detail"]


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
