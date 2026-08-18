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
