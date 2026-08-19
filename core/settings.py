"""Configuration with NO REQUIRED VALUES — the MarketGraph rule, kept.

A fresh clone serves whatever graph exists with no `.env` at all. Each unset
value disables ONE capability, and the API's health payload names which:

  DATABASE_URL                 → the Postgres panel (transmission engine input)
  OPENAI_API_KEY               → the reasoning layer (never numbers anyway)
  FRED_API_KEY                 → Treasury yields
  GPR_INDEX_URL                → the GPR regime overlay
  BIGQUERY_PROJECT             → unused BigQuery transport (wire is raw files)

Values that ARE set are validated by CONTENT, not presence — a setting that is
present and wrong passes every "is it set?" check and fails somewhere else.

`leftover_variables()` names Railway flags that are unused or one-shot and
still set. It never returns secret VALUES — only names and a reason to delete.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """A setting is present and wrong. The message names the fix."""


@dataclass(frozen=True)
class Settings:
    #: The Kuzu graph directory. Single-writer: batch jobs write one at a time.
    kuzu_db_path: Path
    #: Postgres for the market panel; None disables panel-backed features.
    database_url: str | None
    openai_api_key: str | None
    bigquery_project: str | None
    fred_api_key: str | None
    gpr_index_url: str | None
    port: int

    def missing_capabilities(self) -> dict[str, str]:
        """What is switched off and why — surfaced by /api/health so a
        half-configured deployment is visible rather than silently partial."""
        out: dict[str, str] = {}
        if not self.database_url:
            out["panel"] = "DATABASE_URL unset — market panel and event studies disabled"
        if not self.openai_api_key:
            out["reasoning"] = (
                "OPENAI_API_KEY unset — agent, coder and analogy narration disabled"
            )
        # NOT a GDELT-disabled signal: the wire loads credential-free from the
        # raw files (Phase 4), and production serves 1.33M events with
        # BIGQUERY_PROJECT unset. Only the optional BigQuery TRANSPORT is off.
        if not self.bigquery_project:
            out["bigquery"] = (
                "BIGQUERY_PROJECT unset — optional BigQuery transport off "
                "(the GDELT raw-file path needs no credential and is the default)"
            )
        if not self.fred_api_key:
            out["fred"] = "FRED_API_KEY unset — Treasury yield ingestion disabled"
        return out


def leftover_variables() -> dict[str, str]:
    """Names of env vars that are safe (or urgent) to delete.

    Inspects presence only. Values never leave this function — a health
    payload must not echo keys. Empty means the Railway variable list has
    nothing this process considers leftover.
    """
    out: dict[str, str] = {}
    if _present("ANTHROPIC_API_KEY"):
        out["ANTHROPIC_API_KEY"] = (
            "unused — the desk reads OPENAI_API_KEY; delete this"
        )
    if _present("GOOGLE_APPLICATION_CREDENTIALS"):
        out["GOOGLE_APPLICATION_CREDENTIALS"] = (
            "unused — the BigQuery transport is unbuilt; the wire loads "
            "credential-free from raw files"
        )
    model = os.getenv("GEOGRAPH_AGENT_MODEL", "").strip().lower()
    if "claude" in model or "anthropic" in model:
        out["GEOGRAPH_AGENT_MODEL"] = (
            "still names Claude; the desk is OpenAI (gpt-4.1). Delete, or "
            "set an OpenAI model id"
        )

    # One-shot boot flags. Honour-once means a leftover value is a landmine
    # on the next edit, not a current action — unset after it has run.
    for name, note in (
        ("GEOGRAPH_RESET_GRAPH", "one-shot graph delete; unset after it has run"),
        ("GEOGRAPH_DROP_AFFECTED", "one-shot AFFECTED drop; unset after it has run"),
        ("GEOGRAPH_REBUILD_AFFECTED", "one-shot AFFECTED repair; unset after it has run"),
    ):
        if _present(name):
            out[name] = note

    if _truthy("GEOGRAPH_READY_IGNORES_GRAPH"):
        out["GEOGRAPH_READY_IGNORES_GRAPH"] = (
            "healthcheck will pass while the graph is dark; unset for routine deploys"
        )
    if _falsy("GEOGRAPH_JOBS"):
        out["GEOGRAPH_JOBS"] = (
            "convergence loop is off; delete so jobs run (the production default)"
        )
    if _falsy("GEOGRAPH_SEED_ON_BOOT"):
        out["GEOGRAPH_SEED_ON_BOOT"] = (
            "seeding skipped; a fresh volume would stay empty. Delete unless a "
            "batch job currently holds the write lock"
        )
    if _falsy("GEOGRAPH_SNAPSHOT_FROZEN"):
        out["GEOGRAPH_SNAPSHOT_FROZEN"] = (
            "live harvest will append onto the training snapshot; unset to keep the freeze"
        )
    if _falsy("GEOGRAPH_API_FIRST"):
        out["GEOGRAPH_API_FIRST"] = (
            "serial boot — the healthcheck waits on write steps. Delete for routine deploys"
        )
    if _truthy("GEOGRAPH_STUDY_IN_PROCESS"):
        out["GEOGRAPH_STUDY_IN_PROCESS"] = (
            "study forced in-process; the child path exists because this OOM'd. Delete"
        )
    packs = os.getenv("GEOGRAPH_SEED_PACKS", "").strip()
    if packs:
        out["GEOGRAPH_SEED_PACKS"] = (
            f"set — default is every complete pack, not {packs!r}. Delete "
            "unless you meant to seed only these"
        )

    # Opt-in measuring boots. Safe for a deliberate measuring deploy; leftover
    # on a code-ship deploy is graph-dark time. Named so they can be seen.
    for name, note in (
        (
            "GEOGRAPH_GDELT_ON_BOOT",
            "opt-in wire load (hours of graph-dark); unset for routine deploys",
        ),
        (
            "GEOGRAPH_RESCORE_ON_BOOT",
            "opt-in archive rescore (hours, un-resumable); unset for routine deploys",
        ),
        (
            "GEOGRAPH_STUDY_ON_BOOT",
            "opt-in measuring boot (~600s graph-dark); unset for routine deploys",
        ),
        (
            "GEOGRAPH_FORECASTS_ON_BOOT",
            "opt-in forecast freeze; unset — the last freeze persists",
        ),
        (
            "GEOGRAPH_GAMES_ON_BOOT",
            "opt-in region solve; unset — the games job re-solves in the background",
        ),
        (
            "GEOGRAPH_BACKTEST_ON_BOOT",
            "opt-in paper backtest; unset — the ledger persists in Postgres",
        ),
    ):
        if _truthy(name):
            out[name] = note
    return out


def _present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes"}


def _falsy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"0", "false", "no"}


def load() -> Settings:
    raw_db = os.getenv("KUZU_DB_PATH", "").strip()

    database_url = os.getenv("DATABASE_URL", "").strip() or None
    if database_url and not database_url.startswith(("postgres://", "postgresql://")):
        raise ConfigError(
            f"DATABASE_URL={database_url!r} is not a Postgres URL. GeoGraph's "
            "panel is Postgres (build-spec section 6); on Railway, reference "
            "the Postgres service's DATABASE_URL variable."
        )

    gpr = os.getenv("GPR_INDEX_URL", "").strip() or None
    if gpr and not gpr.startswith(("http://", "https://")):
        raise ConfigError(f"GPR_INDEX_URL={gpr!r} is not a URL.")

    return Settings(
        kuzu_db_path=Path(raw_db) if raw_db else _ROOT / "data" / "geograph.kuzu",
        database_url=database_url,
        # OPTIONAL, AND ITS ABSENCE IS NOT AN ERROR. The deterministic layers —
        # crosswalks, transmission, network analytics — have no model
        # dependency at all; only the reasoning layer does.
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip() or None,
        bigquery_project=os.getenv("BIGQUERY_PROJECT", "").strip() or None,
        fred_api_key=os.getenv("FRED_API_KEY", "").strip() or None,
        gpr_index_url=gpr,
        port=int(os.getenv("PORT", "8000")),
    )
