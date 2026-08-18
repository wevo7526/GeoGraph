"""Railway env contract: leftover names, never values."""

from __future__ import annotations

from core import settings as settings_module


def test_leftover_is_empty_on_a_clean_process(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GEOGRAPH_AGENT_MODEL", raising=False)
    monkeypatch.delenv("GEOGRAPH_RESET_GRAPH", raising=False)
    monkeypatch.delenv("GEOGRAPH_DROP_AFFECTED", raising=False)
    monkeypatch.delenv("GEOGRAPH_REBUILD_AFFECTED", raising=False)
    monkeypatch.delenv("GEOGRAPH_READY_IGNORES_GRAPH", raising=False)
    monkeypatch.delenv("GEOGRAPH_JOBS", raising=False)
    monkeypatch.delenv("GEOGRAPH_SEED_ON_BOOT", raising=False)
    monkeypatch.delenv("GEOGRAPH_SNAPSHOT_FROZEN", raising=False)
    monkeypatch.delenv("GEOGRAPH_API_FIRST", raising=False)
    monkeypatch.delenv("GEOGRAPH_STUDY_IN_PROCESS", raising=False)
    monkeypatch.delenv("GEOGRAPH_SEED_PACKS", raising=False)
    for name in (
        "GEOGRAPH_GDELT_ON_BOOT", "GEOGRAPH_RESCORE_ON_BOOT",
        "GEOGRAPH_STUDY_ON_BOOT", "GEOGRAPH_FORECASTS_ON_BOOT",
        "GEOGRAPH_GAMES_ON_BOOT", "GEOGRAPH_BACKTEST_ON_BOOT",
    ):
        monkeypatch.delenv(name, raising=False)
    assert settings_module.leftover_variables() == {}


def test_leftover_names_the_anthropic_key_and_never_echoes_it(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-do-not-echo")
    leftover = settings_module.leftover_variables()
    assert "ANTHROPIC_API_KEY" in leftover
    blob = " ".join(leftover.values())
    assert "sk-ant" not in blob
    assert "secret" not in blob
    assert "OPENAI_API_KEY" in leftover["ANTHROPIC_API_KEY"]


def test_leftover_flags_a_claude_model_override(monkeypatch):
    monkeypatch.setenv("GEOGRAPH_AGENT_MODEL", "claude-sonnet-5")
    leftover = settings_module.leftover_variables()
    assert "GEOGRAPH_AGENT_MODEL" in leftover
    assert "claude-sonnet-5" not in leftover["GEOGRAPH_AGENT_MODEL"]


def test_leftover_flags_one_shot_reset(monkeypatch):
    monkeypatch.setenv("GEOGRAPH_RESET_GRAPH", "1")
    leftover = settings_module.leftover_variables()
    assert "GEOGRAPH_RESET_GRAPH" in leftover
