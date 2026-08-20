"""MCP over HTTP on the API process — JSON-RPC 2.0, same tools as stdio.

WHY THIS EXISTS. `python -m core.mcp.server` speaks stdio and opens its own
read-only graph. The API already holds the single-writer lock, so a second
process cannot open that file. The tools still have to be reachable from an
agent that talks HTTP, and they have to see the same graph the site does.

WHY NOT `Mount("/mcp")`. Starlette compiles that to `^/mcp(?P<path>/.*)$`, so
a bare `POST /mcp` misses the mount and falls through. The SPA catch-all is
GET-only, which is why POST would 405 rather than 200-as-index.html — still
wrong. Registering `POST /mcp` and `POST /mcp/` on the app itself is the
fix; GET is registered too so discovery is not stolen by the SPA.

The protocol surface is the MCP subset an agent actually needs:
`initialize`, `tools/list`, `tools/call`. Notifications (`initialized`)
are acknowledged with 204. Same functions as `core.mcp.tools`, never a
second estimator.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from core.mcp import tools as mcp_tools

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "geograph"
SERVER_VERSION = "0.0.1"

#: Tools an agent can call. Order matches `core.mcp.server` so the two
#: surfaces cannot drift in what they advertise. `conn` is injected from
#: `app.state.graph`; tools that do not take it still run when the graph
#: is down (regime lookup, panel-backed stories).
_TOOL_CALLABLES: dict[str, Any] = {
    "find_actor": mcp_tools.find_actor,
    "neighbors": mcp_tools.neighbors,
    "regime_at": mcp_tools.regime_at,
    "events_between": mcp_tools.events_between,
    "escalation_trajectory": mcp_tools.escalation_trajectory,
    "network_metrics": mcp_tools.network_metrics,
    "event_effects": mcp_tools.event_effects,
    "analogues_for": mcp_tools.analogues_for,
    "forecast": mcp_tools.forecast,
    "markets_story": mcp_tools.markets_story,
    "event_impact": mcp_tools.event_impact,
    "region_call": mcp_tools.region_call,
    "wire_live": mcp_tools.wire_live,
    "situation": mcp_tools.situation,
}

_GRAPH_OPTIONAL = frozenset({
    "regime_at", "markets_story", "region_call", "wire_live", "situation",
})


def _json_type(annotation: Any) -> str:
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        args = [a for a in getattr(annotation, "__args__", ()) if a is not type(None)]
        if args:
            return _json_type(args[0])
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"
    return "string"


def _input_schema(fn: Any) -> dict[str, Any]:
    sig = inspect.signature(fn)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in sig.parameters.items():
        if name == "conn":
            continue
        properties[name] = {"type": _json_type(param.annotation)}
        if param.default is inspect.Parameter.empty:
            required.append(name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def list_tools() -> list[dict[str, Any]]:
    """The MCP `tools/list` payload — names, docs, JSON Schema."""
    rows = []
    for name, fn in _TOOL_CALLABLES.items():
        rows.append({
            "name": name,
            "description": (inspect.getdoc(fn) or "").strip(),
            "inputSchema": _input_schema(fn),
        })
    return rows


def _coerce(value: Any, annotation: Any) -> Any:
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        args = [a for a in getattr(annotation, "__args__", ()) if a is not type(None)]
        if args:
            return _coerce(value, args[0])
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes"}
        return bool(value)
    return value


def call_tool(name: str, arguments: dict[str, Any] | None, conn: Any) -> dict[str, Any]:
    """Dispatch one `tools/call`. Missing graph is an error, not an empty archive."""
    fn = _TOOL_CALLABLES.get(name)
    if fn is None:
        raise KeyError(f"unknown tool: {name}")
    if conn is None and name not in _GRAPH_OPTIONAL:
        raise RuntimeError(
            "graph unavailable — the API holds the single-writer lock and "
            "it is not open yet; retry after /api/health shows graph=open"
        )
    arguments = dict(arguments or {})
    sig = inspect.signature(fn)
    kwargs: dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if pname == "conn":
            continue
        if pname in arguments:
            value = arguments[pname]
            if param.annotation is not inspect.Parameter.empty:
                try:
                    value = _coerce(value, param.annotation)
                except (TypeError, ValueError):
                    value = arguments[pname]
            kwargs[pname] = value
        elif param.default is inspect.Parameter.empty:
            raise TypeError(f"{name} requires `{pname}`")
    if "conn" in sig.parameters:
        return fn(conn, **kwargs)
    return fn(**kwargs)


def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def handle(body: Any, conn: Any) -> tuple[int, dict[str, Any] | None]:
    """JSON-RPC 2.0 → (status, payload). `None` payload is a notification."""
    if not isinstance(body, dict):
        return 200, _rpc_error(None, -32600, "invalid request")
    req_id = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    if method in {"notifications/initialized", "initialized"}:
        return (204, None) if req_id is None else (200, {
            "jsonrpc": "2.0", "id": req_id, "result": {},
        })

    if method == "initialize":
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method == "ping":
        return 200, {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": list_tools()},
        }

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            result = call_tool(name, arguments, conn)
        except KeyError as exc:
            return 200, _rpc_error(req_id, -32601, str(exc))
        except (TypeError, ValueError) as exc:
            return 200, _rpc_error(req_id, -32602, str(exc))
        except Exception as exc:  # noqa: BLE001 - tool failure is an RPC error
            return 200, {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }
        text = json.dumps(result, default=str)
        return 200, {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            },
        }

    if not method:
        return 200, _rpc_error(req_id, -32600, "method is required")
    return 200, _rpc_error(req_id, -32601, f"unknown method: {method}")


def attach(app: FastAPI) -> None:
    """Register GET+POST `/mcp` and `/mcp/` on the app, before the SPA."""

    async def mcp_endpoint(request: Request) -> Any:
        if request.method == "GET":
            return {
                "name": SERVER_NAME,
                "transport": "jsonrpc",
                "protocolVersion": PROTOCOL_VERSION,
                "endpoint": "/mcp",
            }
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - parse error is JSON-RPC, not 400
            return JSONResponse(_rpc_error(None, -32700, "parse error"))
        status, payload = handle(body, getattr(request.app.state, "graph", None))
        if payload is None:
            return Response(status_code=status)
        return JSONResponse(payload, status_code=status)

    app.add_api_route("/mcp", mcp_endpoint, methods=["GET", "POST"], include_in_schema=False)
    app.add_api_route("/mcp/", mcp_endpoint, methods=["GET", "POST"], include_in_schema=False)
