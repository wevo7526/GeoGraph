"""The situation briefing the agent is handed — numbers only, no live LLM."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from core.reasoning import situation as situation_briefing
from core.reasoning.situation import _without_explanations

_ROOT = Path(__file__).resolve().parent.parent


def _walk_keys(value, found: set[str] | None = None) -> set[str]:
    found = found if found is not None else set()
    if isinstance(value, dict):
        found.update(value)
        for item in value.values():
            _walk_keys(item, found)
    elif isinstance(value, list):
        for item in value:
            _walk_keys(item, found)
    return found


def test_assemble_answers_without_a_graph_or_panel():
    body = situation_briefing.assemble(None, "mena")
    assert body["region"] == "mena"
    assert body["region_label"]
    assert "wire" in body and "departures" in body["wire"]
    assert "region_games" in body
    assert "markets" in body
    assert "globe" in body
    assert body["globe"]["placed"] + body["globe"]["unplaced"] > 0
    assert body["forecasts"]["rows"] == []
    assert "note" in body
    keys = _walk_keys(body)
    assert "explanation" not in keys
    assert "scenarios_json" not in keys
    for row in body["wire"]["departures"]:
        assert "name" not in row


def test_audit_paragraphs_are_stripped_from_nested_payloads():
    dirty = {
        "ranking": [{"dyad_name": "A–B", "explanation": ["audit"]}],
        "explanation": ["do not send"],
    }
    clean = _without_explanations(dirty)
    assert "explanation" not in _walk_keys(clean)
    assert clean["ranking"][0]["dyad_name"] == "A–B"


def test_compact_scenarios_keep_likelihoods_not_rationale():
    rows = situation_briefing._compact_scenarios([
        {
            "scenario_name": "further_escalation:dyad:x",
            "likelihood": 0.92,
            "rationale": "do not hand this to the model",
            "market_implication": "nor this",
        }
    ])
    assert rows == [{"scenario": "further_escalation:dyad:x", "likelihood": 0.92}]


def test_situation_endpoint_serves_the_briefing_without_a_key(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    spec = importlib.util.spec_from_file_location(
        "seed_pack", _ROOT / "scripts" / "seed_pack.py",
    )
    assert spec and spec.loader
    seed_pack = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_pack)

    from core import packs
    from core.graph import kuzu_store

    path = tmp_path / "situation.kuzu"
    conn = kuzu_store.connect(path)
    try:
        seed_pack.seed(conn, packs.load("mena"))
    finally:
        kuzu_store.close(conn)

    monkeypatch.setenv("KUZU_DB_PATH", str(path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        response = client.get("/api/reasoning/situation?region=mena")
        assert response.status_code == 200
        body = response.json()
        assert body["region"] == "mena"
        assert "explanation" not in _walk_keys(body)
        missing = client.get("/api/reasoning/situation?region=no-such-pack")
        assert missing.status_code == 404


def test_mcp_situation_tool_is_numbers_only():
    from core.mcp import tools as mcp_tools

    body = mcp_tools.situation(None, "mena")
    assert body["region"] == "mena"
    assert "coverage" in body
    assert "explanation" not in _walk_keys(body)
    unknown = mcp_tools.situation(None, "no-such-pack")
    assert "error" in unknown


def test_with_reader_pins_an_open_pair_and_drops_unknown_desks():
    """The corner desk says where the reader is. That is not a measurement."""
    briefing = {
        "region": "mena",
        "region_games": {
            "ranking": [
                {"dyad_id": "dyad:a--b", "dyad_name": "A–B", "coercive_events": 4},
            ]
        },
        "markets": {"headlines": [{"ticker": "CL=F", "median": 0.01}]},
    }
    tagged = situation_briefing.with_reader(
        briefing,
        surface="relationships",
        focus={"dyad_id": "dyad:a--b", "unknown": "drop me"},
    )
    assert tagged["reader"]["surface"] == "relationships"
    assert tagged["reader"]["looking_at"] == {"dyad_id": "dyad:a--b"}
    assert tagged["reader"]["pair"]["dyad_name"] == "A–B"
    assert "unknown" not in tagged["reader"]["looking_at"]
    ignored = situation_briefing.with_reader(briefing, surface="not-a-desk")
    assert "reader" not in ignored
    # The Wire page folded into Intel; "wire" is no longer a desk name.
    folded = situation_briefing.with_reader(briefing, surface="wire")
    assert "reader" not in folded
