"""MCP tools that match the paper — same functions as the API, capped."""

from __future__ import annotations

from core.mcp import tools as mcp_tools


def test_markets_story_tool_covers_a_missing_panel():
    body = mcp_tools.markets_story("mena")
    assert "coverage" in body
    assert "headlines" in body
    assert body["region"] == "mena"
    if body.get("pending"):
        assert body["headlines"] == []


def test_region_call_tool_covers_a_missing_panel():
    body = mcp_tools.region_call("mena")
    assert "coverage" in body
    assert "lead" in body


def test_wire_live_tool_does_not_invent_rows():
    body = mcp_tools.wire_live("mena", limit=3)
    assert "coverage" in body
    assert "rows" in body
    assert len(body["rows"]) <= 3


def test_situation_tool_covers_a_missing_graph():
    body = mcp_tools.situation(None, "mena")
    assert "coverage" in body
    assert body["region"] == "mena"
    assert "wire" in body


def test_event_impact_tool_unknown_event_is_empty_not_zero(monkeypatch):
    monkeypatch.setattr(
        "core.reasoning.impact.event_impact",
        lambda conn, event_id: None,
    )
    body = mcp_tools.event_impact(object(), "event:does-not-exist")
    assert body["markets"] == []
    assert "coverage" in body
    assert "note" in body
    assert "0.0" not in (body.get("note") or "")


def test_analogues_for_does_not_persist_embeddings():
    import inspect
    from pathlib import Path

    src = inspect.getsource(mcp_tools.analogues_for)
    assert "persist=False" in src
    assert "proposed" in src
    analogy = (
        Path(__file__).resolve().parents[1] / "core" / "reasoning" / "analogy.py"
    ).read_text(encoding="utf-8")
    assert "SET e.embedding" not in analogy
    assert "Event.embedding stays unwritten" in analogy


def test_mcp_http_lists_tools_on_bare_post(tmp_path, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "mcp.kuzu"))
    monkeypatch.setenv("GEOGRAPH_JOBS", "0")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        listed = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })
        assert listed.status_code == 200
        names = {t["name"] for t in listed.json()["result"]["tools"]}
        assert "regime_at" in names
        assert "analogues_for" in names
        called = client.post("/mcp", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "regime_at", "arguments": {"date": "2024-01-01"}},
        })
        assert called.status_code == 200
        payload = json.loads(called.json()["result"]["content"][0]["text"])
        assert payload
        assert client.get("/mcp").json()["transport"] == "jsonrpc"
        slashed = client.post("/mcp/", json={
            "jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {},
        })
        assert slashed.status_code == 200
        assert slashed.json()["result"]["serverInfo"]["name"] == "geograph"
