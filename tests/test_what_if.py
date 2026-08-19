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


def test_propose_cannot_override_the_regime_gate():
    """Identical embeddings do not retrieve a Bretton Woods event for a 2026 query."""
    def embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    query = {
        "name": "a strike", "goldstein": -10.0, "quad_class": "material_conflict",
        "event_time": "2026-01-01",
    }
    out_regime = {
        "node_id": "event:out", "name": "a strike", "event_time": "1960-06-01",
        "goldstein": -10.0, "quad_class": "material_conflict",
    }
    in_regime = {
        "node_id": "event:in", "name": "a meeting", "event_time": "2019-06-01",
        "goldstein": 1.0, "quad_class": "verbal_cooperation",
    }
    top = analogy.propose_candidates(
        query, [out_regime, in_regime], query_date="2026-01-15", embed=embed,
    )
    assert [row["node_id"] for _, row in top] == ["event:in"]


def test_cosine_of_a_vector_with_itself_is_one():
    assert analogy.cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)
    assert analogy.cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


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
        assert body["proposed"] == []
        assert "off on this call" in body["proposed_note"]

        # An unknown CAMEO code is a 422 naming the codebook, never a guess.
        assert client.get(
            "/api/reasoning/what-if?initiator=actor:cow-630&target=actor:cow-666"
            "&cameo=999&date=2026-01-15"
        ).status_code == 422


def test_every_pack_offers_a_worked_example_from_its_own_spine():
    # The composer opens on this rather than on the first two actors
    # alphabetically. It must be a COMPLETE, SCORABLE composition for every
    # region, or the page it seeds is broken for that region only.
    from core.api.routers import reasoning

    for name in packs.available():
        pack = packs.load(name)
        example = reasoning._worked_example(pack)
        assert example is not None, f"{name} offers the composer nothing to open on"
        roster = {a["id"] for a in pack.actors}
        assert example["initiator"] in roster
        assert example["target"] in roster
        # Scorable, or stage 1 opens on a 422.
        assert event_typing.goldstein_for(example["cameo"]) is not None
        assert example["drawn_from"]["event_id"]


def test_the_worked_example_is_the_packs_latest_coded_event():
    from core.api.routers import reasoning

    pack = packs.load("mena")
    example = reasoning._worked_example(pack)
    latest = max(
        (e for e in pack.marquee_events
         if e.get("cameo") and e.get("initiator") and e.get("target")),
        key=lambda e: str(e["date"]),
    )
    assert example is not None
    assert example["drawn_from"]["event_id"] == latest["id"]


def test_a_pack_with_no_coded_spine_offers_no_example_rather_than_a_broken_one():
    from core.api.routers import reasoning

    pack = packs.load("mena")
    data = dict(pack.data)
    data["marquee_events"] = {"events": [
        {"id": "event:x", "date": "2020-01-01", "name": "uncoded"},
    ]}
    stripped = packs.Pack(name="mena", path=pack.path, data=data)
    assert reasoning._worked_example(stripped) is None


def test_the_agent_is_honestly_dark_without_a_key(seeded_graph, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KUZU_DB_PATH", str(seeded_graph))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/reasoning/assess",
            json={"question": "Where does the pressure bind?", "region": "mena"},
        )
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "OPENAI_API_KEY" in detail
        assert "deterministic" in detail  # says what still works


def test_follow_ups_are_still_dark_without_a_key(seeded_graph, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KUZU_DB_PATH", str(seeded_graph))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/reasoning/assess",
            json={
                "question": "And the yields?",
                "region": "mena",
                "history": [
                    {"role": "user", "content": "What is the situation?"},
                    {"role": "assistant", "content": "A prior argument."},
                    {"role": "system", "content": "drop this"},
                ],
                "surface": "markets",
                "focus": {"ticker": "CL=F"},
            },
        )
        assert response.status_code == 503
        assert "OPENAI_API_KEY" in response.json()["detail"]


def test_case_study_narrate_is_dark_without_a_key(seeded_graph, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KUZU_DB_PATH", str(seeded_graph))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            "/api/case-studies/narrate",
            json={"slug": "twelve-day-war"},
        )
        assert response.status_code == 503
        assert "OPENAI_API_KEY" in response.json()["detail"]


def test_study_context_keeps_only_measured_fields():
    from core.reasoning import agent

    compact = agent.study_context({
        "slug": "twelve-day-war",
        "title": "A study",
        "summary": "measured",
        "status": "measured",
        "measured": 9,
        "episodes": [{
            "node_id": "event:a",
            "name": "A strike",
            "effects": [
                {"ticker": f"T{i}", "abnormal_return": 0.01 * i, "window": "car_0_1"}
                for i in range(9)
            ],
        }],
    })
    assert compact["note"]
    assert "originate" in compact["note"]
    assert len(compact["episodes"][0]["effects"]) == 4
    assert compact["episodes"][0]["effects"][0]["abnormal_return"] == pytest.approx(0.08)


def test_conversation_messages_keep_prior_turns_and_the_briefing():
    from core.reasoning import agent

    messages = agent.conversation_messages(
        "What happens next?",
        region_pack="mena",
        context={
            "note": "cite ids",
            "reader": {"surface": "intel", "looking_at": {"dyad_id": "dyad:a--b"}},
        },
        history=[
            {"role": "user", "content": "What is the situation?"},
            {"role": "assistant", "content": "An argument over the briefing."},
            {"role": "tool", "content": "ignore"},
        ],
    )
    assert messages[0]["role"] == "system"
    assert "Never originate" in messages[0]["content"]
    assert "Short paragraphs" in messages[0]["content"]
    assert "Do not use markdown headings" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "What is the situation?"}
    assert messages[2]["role"] == "assistant"
    last = messages[-1]
    assert last["role"] == "user"
    assert "What happens next?" in last["content"]
    assert "intel desk" in last["content"]
    assert "cite ids" in last["content"]
