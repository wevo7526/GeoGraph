"""Apply the derived schema to both stores: Kuzu DDL from the ontology, and
the panel DDL to Postgres if DATABASE_URL is set. Idempotent — both sides are
IF NOT EXISTS. Stop the API first: Kuzu is single-writer."""

from __future__ import annotations

from core import settings as settings_module
from core.graph import kuzu_store
from core.panel import pg_store


def main() -> None:
    settings = settings_module.load()

    settings.kuzu_db_path.parent.mkdir(parents=True, exist_ok=True)
    # Closed in a finally like every sibling script: this is the documented
    # first command in the seed sequence, and it must not hold the write lock
    # (or Kuzu's 8 TiB address-space reservation) a moment longer than the DDL.
    conn = kuzu_store.connect(settings.kuzu_db_path)
    try:
        kuzu_store.apply_schema(conn)
        print(f"kuzu schema applied at {settings.kuzu_db_path}")
    finally:
        kuzu_store.close(conn)

    if settings.database_url:
        pg = pg_store.connect(settings)
        try:
            pg_store.apply_schema(pg)
            print("panel schema applied")
        finally:
            pg.close()
    else:
        print("DATABASE_URL unset — panel schema skipped (that is fine for graph-only work)")


if __name__ == "__main__":
    main()
