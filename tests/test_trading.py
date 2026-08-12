"""The walk-forward paper backtest and the trading API surface: as-of
truncation, recorded skips, compounding — every number hand-derivable, and
the endpoints honest when the panel or the ledger is absent."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from core import packs
from core.graph import kuzu_store
from core.reasoning import backtest, paper

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── quarter arithmetic ───────────────────────────────────────────────────────


def test_quarter_ends_cover_the_range_inclusive():
    assert backtest.quarter_ends("2015-01-15", "2015-12-31") == [
        "2015-03-31", "2015-06-30", "2015-09-30", "2015-12-31",
    ]
    assert backtest.quarter_ends("2015-03-31", "2015-03-31") == ["2015-03-31"]
    assert backtest.quarter_ends("2015-04-01", "2015-06-29") == []


# ── the walk-forward, hand-checkable ─────────────────────────────────────────
# One dyad escalates once every quarter 2015Q1–2019Q4. The panel holds one
# rising close per month. Books: escalation is fully long "X", reversion is
# flat — so the net weight IS the escalation likelihood, and every quarter's
# P&L is p * notional * (mark/entry - 1) by hand.


def _rows() -> list[dict[str, Any]]:
    rows = []
    for i in range(20):
        year, month = 2015 + i // 4, 3 * (i % 4) + 1
        rows.append({
            "dyad_id": "dyad:a--b", "dyad_name": "A – B", "baseline": -2.0,
            "event_id": f"event:x-{i}", "event_time": f"{year}-{month:02d}-15",
            "direction": "escalating", "region_pack": "testpack",
        })
    return rows


def _series() -> dict[str, list[dict[str, Any]]]:
    out = []
    price = 100.0
    for year in range(2015, 2021):
        for month in range(1, 13):
            out.append({"obs_date": f"{year}-{month:02d}-15", "price": price})
            price += 1.0
    return {"X": out}


def _walk() -> dict[str, Any]:
    return backtest.walk_forward(
        _rows(), _series(),
        region_pack="testpack",
        escalation_book={"X": 1.0},
        reversion_book={"X": 0.0},
    )


def test_thin_quarters_are_recorded_skips_not_trades():
    result = _walk()
    # Episodes accumulate one per quarter; the floor is MIN_EPISODES, so the
    # first traded cutoff is the quarter that reaches it.
    first = result["ledger"][0]
    assert first["episodes"] == backtest.MIN_EPISODES
    thin = [s for s in result["skipped"] if "too thin" in s["reason"]]
    assert len(thin) == backtest.MIN_EPISODES - 1
    assert all(
        s["quarter_end"] < first["quarter_end"] for s in thin
    )


def test_the_walk_forward_has_no_lookahead_and_compounds():
    result = _walk()
    ledger = result["ledger"]
    # Episodes grow strictly by one per later cutoff: each quarter's forecast
    # saw exactly the past, never the future.
    episode_counts = [entry["episodes"] for entry in ledger]
    assert episode_counts == list(range(
        episode_counts[0], episode_counts[0] + len(ledger)
    ))
    # Every quarter by hand: p = (episodes-1)/episodes (all but the newest
    # episode saw a later one), entry is the first monthly close after the
    # cutoff, the mark is two closes later (+2.0), and equity compounds.
    equity = float(paper.NOTIONAL_USD)
    for entry in ledger:
        p = (entry["episodes"] - 1) / entry["episodes"]
        assert entry["escalation_likelihood"] == pytest.approx(p, abs=5e-5)
        marked = {pos["ticker"]: pos for pos in entry["positions"]}["X"]
        assert marked["mark"] - marked["entry"] == pytest.approx(2.0)
        expected_return = entry["escalation_likelihood"] * (
            marked["mark"] / marked["entry"] - 1.0
        )
        assert entry["quarter_return"] == pytest.approx(expected_return, abs=1e-4)
        equity *= 1.0 + entry["quarter_return"]
        assert entry["equity_usd"] == pytest.approx(equity, abs=1.0)
    summary = result["summary"]
    assert summary["final_equity_usd"] == ledger[-1]["equity_usd"]
    assert summary["hit_rate"] == 1.0          # monotone panel: every quarter up
    assert summary["max_drawdown"] == 0.0


def test_a_quarter_the_panel_cannot_fill_is_a_recorded_skip():
    series = _series()
    series["X"] = [row for row in series["X"] if row["obs_date"] >= "2018-01-01"]
    result = backtest.walk_forward(
        _rows(), series,
        region_pack="testpack",
        escalation_book={"X": 1.0},
        reversion_book={"X": 0.0},
    )
    unfillable = [s for s in result["skipped"] if "no panel closes" in s["reason"]]
    assert unfillable, "pre-2018 quarters cleared the episode floor but had no fills"
    assert all(entry["quarter_end"] >= "2017-12-31" for entry in result["ledger"])


# ── the API surface ──────────────────────────────────────────────────────────


@pytest.fixture()
def frozen_graph(tmp_path):
    seed_pack = _load("seed_pack")
    path = tmp_path / "trading.kuzu"
    conn = kuzu_store.connect(path)
    try:
        seed_pack.seed(conn, packs.load("mena"))
    finally:
        kuzu_store.close(conn)
    _load("run_forecasts").freeze(path, region_pack="mena")
    return path


def test_the_backtest_endpoint_is_honest_without_a_panel(
    frozen_graph, monkeypatch
):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KUZU_DB_PATH", str(frozen_graph))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/api/trading/backtest?region=mena").status_code == 503
        assert client.get("/api/trading/backtest?region=nope").status_code == 404


def test_the_forward_view_serves_the_book_and_the_boundary(
    frozen_graph, monkeypatch
):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KUZU_DB_PATH", str(frozen_graph))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core.api.app import create_app

    with TestClient(create_app()) as client:
        body = client.get("/api/trading/forward?region=mena").json()
        assert 0.0 <= body["forecast"]["escalation_likelihood"] <= 1.0
        # No panel: the book is null WITH the reason — never an invented fill.
        assert body["book"] is None
        assert "DATABASE_URL" in body["book_unavailable"]
        # The net weights still blend the pack's own books.
        assert set(body["net_weights"]) == {"BZ=F", "GC=F", "^TASI.SR", "DFMGI.AE"}
        # Long-horizon ALWAYS carries the boundary statement.
        assert body["pressure"]["boundary_statement"]
        assert body["pressure"]["trajectory"]
