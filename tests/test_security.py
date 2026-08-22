"""Production security guards — rate limits, docs off, error sanitisation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from core.api import security as security_module


def test_sanitize_error_keeps_the_exception_not_the_traceback():
    raw = (
        '  File "/app/core/graph/kuzu_store.py", line 782, in merge_edges\n'
        "    conn.execute(cypher, parameters={\"rows\": batch})\n"
        "RuntimeError: Assertion failed in file "
        '"/tmp/x/csr_node_group.cpp" on line 411: KU_UNREACHABLE'
    )
    cleaned = security_module.sanitize_error(raw)
    assert cleaned is not None
    assert "RuntimeError" in cleaned
    assert "KU_UNREACHABLE" in cleaned
    assert "File \"" not in cleaned


def test_sanitize_boot_walks_nested_pack_errors():
    boot = {
        "packs": [
            {"pack": "mena", "ok": False, "error": "File \"x\"\nRuntimeError: boom"},
        ],
        "deep": {"ok": False, "error": "Traceback\nOSError: full"},
    }
    out = security_module.sanitize_boot(boot)
    assert out is not None
    assert out["packs"][0]["error"] == "RuntimeError: boom"
    assert "Traceback" not in (out["deep"]["error"] or "")


def test_docs_are_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "docs.kuzu"))
    monkeypatch.delenv("GEOGRAPH_EXPOSE_DOCS", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


def test_docs_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "docs-on.kuzu"))
    monkeypatch.setenv("GEOGRAPH_EXPOSE_DOCS", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/openapi.json").status_code == 200


def test_rate_limit_trips_on_expensive_path(tmp_path, monkeypatch):
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "rl.kuzu"))
    monkeypatch.setenv("GEOGRAPH_RATE_LIMIT", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Tiny window for the test: reuse the module's limiter with a fake path.
    from core.api.app import create_app

    # Patch limits so we do not need dozens of real requests.
    monkeypatch.setattr(
        security_module,
        "_LIMITS",
        (("/api/health", 3, 60.0), ("/api/", 100, 60.0)),
    )
    # Fresh window so parallel tests do not share counts.
    security_module._WINDOW = security_module._SlidingWindow()

    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health").status_code == 200
        blocked = client.get("/api/health")
        assert blocked.status_code == 429
        assert "rate limit" in blocked.json()["detail"]


def test_ops_token_gates_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "ops.kuzu"))
    monkeypatch.setenv("GEOGRAPH_OPS_TOKEN", "ops-secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/api/storage").status_code == 401
        ok = client.get(
            "/api/storage",
            headers={"Authorization": "Bearer ops-secret"},
        )
        assert ok.status_code == 200
        assert "graph_volume" in ok.json()


def test_security_headers_allow_l4global_embed(tmp_path, monkeypatch):
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "embed.kuzu"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.headers.get("x-frame-options") is None
        csp = response.headers.get("content-security-policy", "")
        assert "frame-ancestors" in csp
        assert "https://l4global.com" in csp
        assert "'self'" in csp
        assert response.headers.get("cross-origin-resource-policy") is None


def test_alerts_flag_jobs_off_and_oneshot(monkeypatch, tmp_path):
    monkeypatch.setenv("GEOGRAPH_JOBS", "0")
    monkeypatch.setenv("GEOGRAPH_RESET_GRAPH", "yes")
    monkeypatch.delenv("GEOGRAPH_MCP_TOKEN", raising=False)
    monkeypatch.delenv("GEOGRAPH_OPS_TOKEN", raising=False)

    class _Settings:
        kuzu_db_path = tmp_path / "missing.kuzu"

    alerts = security_module.operational_alerts(
        settings=_Settings(),
        boot={"packs": [{"pack": "mena", "ok": False, "error": "boom"}],
              "deep": {"ok": False, "error": "boom"}},
        jobs_running=False,
    )
    codes = {a["code"] for a in alerts}
    assert "jobs_off" in codes
    assert "oneshot_leftover" in codes
    assert "seed_failed" in codes
    assert "deep_tier_failed" in codes
    assert "mcp_public" in codes
