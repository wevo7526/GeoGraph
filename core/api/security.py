"""Production request guards — rate limits, security headers, ops tokens.

GeoGraph is a single public origin. At scale the expensive surfaces
(LLM assess, games explore, MCP tool calls) are free to hammer unless
this process refuses the surplus. No third-party limiter: an in-process
sliding window is enough for one Railway instance, and the cost of the
guard must stay tiny beside a graph read.

Tokens (`GEOGRAPH_MCP_TOKEN`, `GEOGRAPH_OPS_TOKEN`) are OPTIONAL but
flagged when missing — a public MCP without a shared secret is a live
agent surface anyone can drive.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

#: Path prefix → (max requests, window seconds). Longest prefix wins.
_LIMITS: tuple[tuple[str, int, float], ...] = (
    ("/api/reasoning/assess", 6, 60.0),
    ("/api/case-studies/narrate", 6, 60.0),
    ("/api/games/explore", 12, 60.0),
    ("/api/trading/backtest", 20, 60.0),
    ("/api/network/metrics", 30, 60.0),
    ("/api/storage", 10, 60.0),
    ("/mcp", 30, 60.0),
    ("/api/", 120, 60.0),
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    # WordPress embed on l4global.com — CSP frame-ancestors (not X-Frame-Options).
    "Content-Security-Policy": "frame-ancestors 'self' https://l4global.com",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    # API + SPA on one origin; scripts are our own hashed bundles.
    "Cross-Origin-Opener-Policy": "same-origin",
}


def expose_docs() -> bool:
    """OpenAPI UI is off unless explicitly opted in — reduces attack surface."""
    return os.getenv("GEOGRAPH_EXPOSE_DOCS", "0").strip().lower() in {
        "1", "true", "yes",
    }


def mcp_token() -> str | None:
    raw = os.getenv("GEOGRAPH_MCP_TOKEN", "").strip()
    return raw or None


def ops_token() -> str | None:
    raw = os.getenv("GEOGRAPH_OPS_TOKEN", "").strip()
    return raw or None


def bearer_matches(request: Request, expected: str | None) -> bool:
    """Constant-time compare of `Authorization: Bearer <token>`."""
    if not expected:
        return True
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return False
    offered = header[7:].strip()
    if len(offered) != len(expected):
        # secrets.compare_digest requires equal length; reject without leaking.
        secrets.compare_digest(expected, expected)
        return False
    return secrets.compare_digest(offered, expected)


def client_ip(request: Request) -> str:
    """Railway terminates TLS and sets X-Forwarded-For; fall back to peer."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client is not None:
        return request.client.host or "unknown"
    return "unknown"


def _limit_for(path: str) -> tuple[int, float]:
    # Longest matching prefix wins so /api/reasoning/assess is not billed
    # under the generic /api/ bucket.
    best: tuple[int, float] | None = None
    best_len = -1
    for prefix, count, window in _LIMITS:
        if (path == prefix or path.startswith(prefix)) and len(prefix) > best_len:
            best = (count, window)
            best_len = len(prefix)
    return best if best is not None else (120, 60.0)


class _SlidingWindow:
    """Per-key deque of timestamps. One lock — cheap at our request rates."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window: float) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            cutoff = now - window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False, 0
            bucket.append(now)
            return True, max(0, limit - len(bucket))


_WINDOW = _SlidingWindow()


def rate_limits_enabled() -> bool:
    """Off under pytest (`GEOGRAPH_RATE_LIMIT=0` in conftest) and for deliberate load tests."""
    return os.getenv("GEOGRAPH_RATE_LIMIT", "1").strip().lower() not in {
        "0", "false", "no",
    }


class ProductionGuardMiddleware(BaseHTTPMiddleware):
    """Rate-limit expensive routes and attach baseline security headers."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Any]
    ) -> Response:
        path = request.url.path
        # Static hashed assets and the SPA shell are not the abuse surface.
        if path.startswith("/assets/") or path in {"/", "/favicon.ico"}:
            response = await call_next(request)
            self._headers(response)
            return response

        if rate_limits_enabled() and (path.startswith("/api/") or path.startswith("/mcp")):
            limit, window = _limit_for(path)
            ip = client_ip(request)
            ok, remaining = _WINDOW.allow(f"{ip}:{path.split('?', 1)[0]}", limit, window)
            if not ok:
                response = JSONResponse(
                    {"detail": "rate limit exceeded — retry shortly"},
                    status_code=429,
                    headers={"Retry-After": str(int(window))},
                )
                self._headers(response)
                response.headers["X-RateLimit-Limit"] = str(limit)
                response.headers["X-RateLimit-Remaining"] = "0"
                return response
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            self._headers(response)
            return response

        response = await call_next(request)
        self._headers(response)
        return response

    @staticmethod
    def _headers(response: Response) -> None:
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)


def sanitize_error(text: str | None, *, max_chars: int = 280) -> str | None:
    """Collapse a traceback to its final exception line for public health.

    Boot status used to echo full Python tracebacks into /api/health — useful
    in logs, free reconnaissance for anyone who can hit the origin.
    """
    if not text:
        return text
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    # Prefer the last Exception-shaped line; else the last line.
    chosen = lines[-1]
    for line in reversed(lines):
        if line.startswith(("RuntimeError", "Error", "Exception", "Assertion", "OSError",
                            "ValueError", "TypeError", "KeyError", "GraphUnavailable")):
            chosen = line
            break
        if ": " in line and not line.startswith(("File ", "Traceback")):
            chosen = line
            break
    if len(chosen) > max_chars:
        return chosen[: max_chars - 1] + "…"
    return chosen


def sanitize_boot(boot: dict[str, Any] | None) -> dict[str, Any] | None:
    """Walk the boot status tree and truncate nested `error` strings."""
    if boot is None:
        return None

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for key, value in node.items():
                if key == "error" and isinstance(value, str):
                    out[key] = sanitize_error(value)
                else:
                    out[key] = walk(value)
            return out
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(boot)


def operational_alerts(
    *,
    settings: Any,
    boot: dict[str, Any] | None,
    jobs_running: bool,
) -> list[dict[str, str]]:
    """Actionable production warnings for /api/health — names only, no secrets."""
    from core.graph import kuzu_store

    alerts: list[dict[str, str]] = []
    leftover = __import__("core.settings", fromlist=["leftover_variables"]).leftover_variables()

    if "GEOGRAPH_JOBS" in leftover:
        alerts.append({
            "severity": "high",
            "code": "jobs_off",
            "message": (
                "GEOGRAPH_JOBS=0 — the convergence loop is off; markets, "
                "games solves, wire, and narrate will not advance. Unset it."
            ),
        })
    for flag in (
        "GEOGRAPH_RESET_GRAPH",
        "GEOGRAPH_DROP_AFFECTED",
        "GEOGRAPH_REBUILD_AFFECTED",
    ):
        if flag in leftover:
            alerts.append({
                "severity": "critical",
                "code": "oneshot_leftover",
                "message": (
                    f"{flag} is still set after honouring — a landmine on the "
                    "next variable edit. Unset it in Railway now."
                ),
            })
    if "GEOGRAPH_READY_IGNORES_GRAPH" in leftover:
        alerts.append({
            "severity": "high",
            "code": "ready_ignores_graph",
            "message": (
                "GEOGRAPH_READY_IGNORES_GRAPH is set — Railway may promote a "
                "deploy while the graph is dark. Unset for routine deploys."
            ),
        })

    usage = kuzu_store.disk_usage(settings.kuzu_db_path)
    if usage is not None and usage["free"] < kuzu_store.DISK_FLOOR_BYTES:
        free_mb = usage["free"] // (1 << 20)
        alerts.append({
            "severity": "critical",
            "code": "disk_tight",
            "message": (
                f"graph volume has {free_mb} MB free (floor "
                f"{kuzu_store.DISK_FLOOR_BYTES // (1 << 20)} MB) — writes "
                "will fail mid-transaction. Grow the volume or reclaim."
            ),
        })

    if boot:
        packs = boot.get("packs") or []
        failed = [p for p in packs if isinstance(p, dict) and not p.get("ok")]
        if failed:
            names = ", ".join(str(p.get("pack")) for p in failed)
            alerts.append({
                "severity": "high",
                "code": "seed_failed",
                "message": (
                    f"boot seed failed for {names} — relationship/deep-tier "
                    "writes hit a graph error; check sanitized boot.errors."
                ),
            })
        deep = boot.get("deep") or {}
        if isinstance(deep, dict) and deep.get("ok") is False:
            alerts.append({
                "severity": "high",
                "code": "deep_tier_failed",
                "message": "deep-tier load failed on boot — COW relations may be stale.",
            })

    if mcp_token() is None:
        alerts.append({
            "severity": "medium",
            "code": "mcp_public",
            "message": (
                "GEOGRAPH_MCP_TOKEN unset — POST /mcp is a public agent "
                "surface. Set a shared bearer token."
            ),
        })
    if ops_token() is None:
        alerts.append({
            "severity": "low",
            "code": "ops_public",
            "message": (
                "GEOGRAPH_OPS_TOKEN unset — /api/storage is public. Set a "
                "bearer token to restrict ops endpoints."
            ),
        })
    if not jobs_running and "GEOGRAPH_JOBS" not in leftover:
        # Graph may still be warming; not an alert by itself.
        pass
    return alerts
