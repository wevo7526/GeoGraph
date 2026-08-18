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
    def find_actor(name: str) -> dict[str, Any]:
        """Find actors by name (substring). Rows are capped; `truncated` says
        when. The actor set is TIME-VARYING — check state_from/state_to before
        claiming an actor existed at a date."""
        return tools.find_actor(conn, name)

    @server.tool()
    def neighbors(node_id: str) -> dict[str, Any]:
        """One hop out along traversable edges from any node."""
        return tools.neighbors(conn, node_id)

    @server.tool()
    def regime_at(date: str) -> dict[str, Any]:
        """The monetary order and polarity epoch covering an ISO date.
        Analogies are only admissible WITHIN a regime — never reason naively
        across the archive (1972 → present)."""
        return tools.regime_at(date)

    @server.tool()
    def events_between(
        actor_a: str, actor_b: str, start: str = "", end: str = ""
    ) -> dict[str, Any]:
        """Events on the dyad two actors form, in time order, with each event's
        escalation against that dyad's own baseline. Pass actor node_ids
        (`actor:cow-2`). The dyad is UNORDERED — one relationship, whichever
        side acted. Read the `coverage` note before concluding an event is
        absent from history rather than from this archive."""
        return tools.events_between(conn, actor_a, actor_b, start or None, end or None)

    @server.tool()
    def escalation_trajectory(dyad_id: str) -> dict[str, Any]:
        """A dyad's escalation path against its own EWMA baseline. Escalation
        is RELATIONAL: the same Goldstein score is routine in a rivalry and a
        rupture in an alliance, so cite the baseline whenever you cite a
        magnitude. A first observation has no history and reads as `stable`
        with magnitude 0 — that means "nothing to compare to", not "calm"."""
        return tools.escalation_trajectory(conn, dyad_id)

    @server.tool()
    def network_metrics(window_start: str, window_end: str) -> dict[str, Any]:
        """Centrality, brokerage and coalitions for a time window —
        deterministic numbers from graph/analytics.py. (Phase 2.)"""
        return tools.network_metrics(conn, window_start, window_end)

    @server.tool()
    def event_effects(event_id: str) -> dict[str, Any]:
        """MEASURED market effects of an event, with the resolution each was
        measured at. Deep-past effects are annual/monthly and coarse — weight
        them accordingly, and never read an abnormal return as a causal
        assertion. An empty result means nothing was MEASURED (the engine may
        not have run, or every market was skipped), never that the event had no
        effect. `overlapping` marks a window another event falls inside: report
        that caveat with the number, do not quietly drop it."""
        return tools.event_effects(conn, event_id)

    @server.tool()
    def analogues_for(query_ref: str) -> dict[str, Any]:
        """Regime-admissible historical analogues with similarity and
        rationale. Structural match only — the vector-index half is unbuilt."""
        return tools.analogues_for(conn, query_ref)

    @server.tool()
    def forecast(question: str, mode: str = "near_term") -> dict[str, Any]:
        """Frozen Forecast nodes. near_term carries likelihoods (Brier-scored);
        long_horizon maps structural pressure over windows and NEVER calls
        dates — surface its boundary statement verbatim. Does not compute a
        new call from the question string."""
        return tools.forecast(conn, question, mode)

    @server.tool()
    def markets_story(region: str = "mena") -> dict[str, Any]:
        """Persisted markets story for a region pack: headline cells and the
        leave-one-out transmission-skill block. Same store as GET
        /api/markets/story. Pending means not computed yet, never a zero.
        Does not re-run the event study."""
        return tools.markets_story(region)

    @server.tool()
    def event_impact(event_id: str) -> dict[str, Any]:
        """Measured vs expected vs surprise for one event — same function as
        GET /api/impact/{event_id}. An empty markets list means nothing was
        MEASURED, never that the event had no effect. Cite the coverage note."""
        return tools.event_impact(conn, event_id)

    @server.tool()
    def region_call(region: str = "mena") -> dict[str, Any]:
        """The persisted region map's lead pair and how many dyads were solved.
        Same store as GET /api/games/region. Does not re-solve live."""
        return tools.region_call(region)

    @server.tool()
    def wire_live(region: str = "mena", limit: int = 12) -> dict[str, Any]:
        """Newest scored live-overlay rows for a region (GDELT 2.0 cache).
        Empty means this process has no live batch yet, not that the region
        is quiet. Rows are capped; `truncated` says when."""
        return tools.wire_live(region, limit)

    @server.tool()
    def situation(region: str = "mena") -> dict[str, Any]:
        """Compact situation briefing for a region pack: wire departures,
        live overlay if cached, persisted region-game ranking, packed market
        headlines, globe coverage, frozen forecasts. Same object POST
        /api/reasoning/assess narrates. Numbers only — this tool does not
        call a model. Cite node_id / dyad_id; never originate a figure."""
        return tools.situation(conn, region)

    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
