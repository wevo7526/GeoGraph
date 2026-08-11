"""The MCP server — the agent's front door (build-spec section 14).

`python -m core.mcp.server` speaks MCP over stdio with no credentials — the
graph is open data. Tool DESCRIPTIONS carry the honesty rules because the
agent reads descriptions, not documentation: effects are measured not
asserted; deep-tier effects are coarse (resolution rides on every effect);
long-horizon output maps pressure over windows, never dated predictions.

DEPLOYMENT NOTE FOR LATER: Kuzu is single-writer, so a separate MCP process
cannot open a graph the API holds. When the API mounts this server over HTTP
(the MarketGraph api/mcp_mount.py pattern), remember the Starlette trap:
Mount("/mcp") compiles to ^/mcp(?P<path>/.*)$, so a bare POST /mcp misses the
mount and falls through to the SPA catch-all as a 405.
"""

from __future__ import annotations

from typing import Any

from core import settings as settings_module
from core.graph import kuzu_store
from core.mcp import tools


def build_server() -> Any:
    try:
        from mcp.server.mcpserver import MCPServer  # SDK v2 (fastmcp.FastMCP in v1)
    except ImportError as exc:
        raise SystemExit(
            'The MCP SDK is not installed — pip install -e ".[mcp]"'
        ) from exc

    settings = settings_module.load()
    conn = kuzu_store.connect(settings.kuzu_db_path, read_only=True)
    server = MCPServer("geograph")

    @server.tool()
    def find_actor(name: str) -> dict:
        """Find actors by name (substring). Rows are capped; `truncated` says
        when. The actor set is TIME-VARYING — check state_from/state_to before
        claiming an actor existed at a date."""
        return tools.find_actor(conn, name)

    @server.tool()
    def neighbors(node_id: str) -> dict:
        """One hop out along traversable edges from any node."""
        return tools.neighbors(conn, node_id)

    @server.tool()
    def regime_at(date: str) -> dict:
        """The monetary order and polarity epoch covering an ISO date.
        Analogies are only admissible WITHIN a regime — never reason naively
        across the 120-year span."""
        return tools.regime_at(date)

    @server.tool()
    def events_between(actor_a: str, actor_b: str, start: str = "", end: str = "") -> dict:
        """Events between two actors in a window. (Phase 0.)"""
        return tools.events_between(conn, actor_a, actor_b, start or None, end or None)

    @server.tool()
    def escalation_trajectory(dyad_id: str) -> dict:
        """A dyad's escalation path against its own EWMA baseline —
        escalation is relational, never an absolute label. (Phase 2.)"""
        return tools.escalation_trajectory(conn, dyad_id)

    @server.tool()
    def network_metrics(window_start: str, window_end: str) -> dict:
        """Centrality, brokerage and coalitions for a time window —
        deterministic numbers from graph/analytics.py. (Phase 2.)"""
        return tools.network_metrics(conn, window_start, window_end)

    @server.tool()
    def event_effects(event_id: str) -> dict:
        """MEASURED market effects of an event, with the resolution each was
        measured at. Deep-past effects are annual/monthly and coarse — weight
        them accordingly, and never read an abnormal return as a causal
        assertion. (Phase 1.)"""
        return tools.event_effects(conn, event_id)

    @server.tool()
    def analogues_for(query_ref: str) -> dict:
        """Regime-admissible historical analogues with similarity and
        rationale. (Phase 5.)"""
        return tools.analogues_for(query_ref)

    @server.tool()
    def forecast(question: str, mode: str = "near_term") -> dict:
        """Reasoned scenarios. near_term carries likelihoods (Brier-scored);
        long_horizon maps structural pressure over windows and NEVER calls
        dates — surface its boundary statement verbatim. (Phase 5.)"""
        return tools.forecast(question, mode)

    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
