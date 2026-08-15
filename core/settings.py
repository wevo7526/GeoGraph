"""Configuration with NO REQUIRED VALUES — the MarketGraph rule, kept.

A fresh clone serves whatever graph exists with no `.env` at all. Each unset
value disables ONE capability, and the API's health payload names which:

  DATABASE_URL                 → the Postgres panel (transmission engine input)
  ANTHROPIC_API_KEY            → the reasoning layer (never numbers anyway)
  BIGQUERY_PROJECT / GOOGLE_APPLICATION_CREDENTIALS → GDELT ingestion
  FRED_API_KEY                 → Treasury yields
  GPR_INDEX_URL                → the GPR regime overlay

Values that ARE set are validated by CONTENT, not presence — a setting that is
present and wrong passes every "is it set?" check and fails somewhere else.
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
    anthropic_api_key: str | None
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
        if not self.anthropic_api_key:
            out["reasoning"] = (
                "ANTHROPIC_API_KEY unset — agent, coder and analogy narration disabled"
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
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip() or None,
        bigquery_project=os.getenv("BIGQUERY_PROJECT", "").strip() or None,
        fred_api_key=os.getenv("FRED_API_KEY", "").strip() or None,
        gpr_index_url=gpr,
        port=int(os.getenv("PORT", "8000")),
    )
