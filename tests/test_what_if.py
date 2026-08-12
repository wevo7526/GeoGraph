"""The what-if surface: deterministic coding, admissibility-gated analogue
retrieval over provided rows, and the agent's honest darkness without a key."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from core import packs
from core.classifier import typing as event_typing
from core.graph import kuzu_store
from core.reasoning import analogy

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_codebook_entries_carry_the_derived_values():
    entries = {e["code"]: e for e in event_typing.codebook_entries()}
    assert entries["057"]["quad_class"] == "verbal_cooperation"
    assert entries["190"]["goldstein"] == -10.0
    assert all(e["label"] for e in entries.values())


def test_rank_candidates_refuses_out_of_regime_history():
    shape: dict[str, Any] = {
        "goldstein": -10.0, "quad_class": "material_conflict",
        "escalation_direction": "escalating", "escalation_magnitude": 5.0,
        "escalation_baseline": -5.0,
        "initiator_id": "actor:x", "target_id": "actor:y",
    }
    in_regime = {**shape, "node_id": "event:in", "name": "in",
                 "event_time": "2019-06-01"}
    out_regime = {**shape, "node_id": "event:out", "name": "out",
                  "event_time": "1960-06-01"}  # Bretton Woods: refused
    top = analogy.rank_candidates(
        {**shape, "node_id": None}, [in_regime, out_regime],
        query_date="2026-01-15",
    )
    assert [row["node_id"] for _, row in top] == ["event:in"]
    # The identical in-regime shape scores as a full match.
    assert top[0][0] == pytest.approx(1.0)


@pytest.fixture()
def seeded_graph(tmp_path):
    seed_pack = _load("seed_pack")
    path = tmp_path / "whatif.kuzu"
    conn = kuzu_store.connect(path)
    try:
        seed_pack.seed(conn, packs.load("mena"))
    finally:
        kuzu_store.close(conn)
    return path


def test_the_what_if_reads_the_archive_deterministically(seeded_graph, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KUZU_DB_PATH", str(seeded_graph))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        options = client.get("/api/reasoning/options?region=mena").json()
        assert {"actor:cow-630", "actor:cow-666"} <= {a["id"] for a in options["actors"]}
        assert any(c["code"] == "190" for c in options["codes"])

        body = client.get(
            "/api/reasoning/what-if?initiator=actor:cow-630&target=actor:cow-666"
            "&cameo=190&date=2026-01-15&region=mena"
        ).json()
        assert body["hypothetical"]["goldstein"] == -10.0
        assert body["hypothetical"]["quad_class"] == "material_conflict"
        # The Iran–Israel dyad exists in the seeded spine, so the hypothetical
        # is read against ITS baseline, not a global prior.
        assert body["dyad"]["node_id"] == "dyad:cow-630--cow-666"
        assert body["dyad"]["baseline"] is not None
        assert body["analogues"], "the fiat-floating spine offers analogues"
        for entry in body["analogues"]:
            assert 0.0 <= entry["similarity"] <= 1.0
            assert entry["event_time"] >= "1971-08-15"  # admissibility held
        assert "not a prediction" in body["transmission"]["label"]

        # An unknown CAMEO code is a 422 naming the codebook, never a guess.
        assert client.get(
            "/api/reasoning/what-if?initiator=actor:cow-630&target=actor:cow-666"
            "&cameo=999&date=2026-01-15"
        ).status_code == 422


def test_the_agent_is_honestly_dark_without_a_key(seeded_graph, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KUZU_DB_PATH", str(seeded_graph))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/reasoning/assess",
            json={"question": "Where does the pressure bind?", "region": "mena"},
        )
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "ANTHROPIC_API_KEY" in detail
        assert "deterministic" in detail  # says what still works
