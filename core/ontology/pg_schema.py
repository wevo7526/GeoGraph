"""The wire corpus's Postgres schema, DERIVED FROM THE SAME ONTOLOGY.

WHY ONE CLASS LIVES IN TWO STORES. Kuzu holds the graph: actors, regimes,
markets, the durable RELATES_TO web and the marquee events that moved a market.
Postgres holds the WIRE — the ~1.33M GDELT events the model and the game train
on. They are the same `Event` class, typed by the same YAML, and the split is
about ACCESS PATTERN rather than about meaning.

The evidence for the split is in the queries. Every bulk consumer of the wire —
`models/panel.dyad_event_rows`, `classifier/rescore`, `reasoning/forecasting`,
`games/transition`, `reasoning/analogy` — is one hop, `(e:Event)-[:OF_DYAD]->
(d:Dyad)`, grouped by dyad and ordered by time. That is a GROUP BY, not a
traversal. The only genuinely graph-shaped Event queries are single-event
lookups by node_id, which serve one event a user clicked.

Paying graph cost for a scan workload was measurable, not theoretical: five
edge tables per event, MERGE at ~145 events/sec and SLOWING as the graph grew
(china merged 340,445 into an empty graph at 353/sec; eurasia's identical years
then cost 4-6x each), no VACUUM to reclaim rewritten rows, and a single writer
that forced all of it in front of the health check. On 2026-08-13 that was a
four-hour outage and a full volume.

WHAT DOES NOT CHANGE — and this is the invariant that made the split safe to
make at all: `geograph.linkml.yaml` is still the only source of truth. This
module derives its columns from the `Event` class exactly as `kuzu_schema`
derives the node table, so a slot added to the ontology appears in both stores
and neither keeps a private copy. A wire row and a graph node are the same
thing described once.

THE PROVENANCE INVARIANT GETS STRONGER HERE, not weaker. `kuzu_schema.
validate_edge` exists in Python because Kuzu has no NOT NULL on relationship
properties. Postgres does, and it has foreign keys, so `source_id TEXT NOT NULL
REFERENCES wire_source` is enforced by the database. That is why `wire_source`
is mirrored rather than pointed at across the two stores: a foreign key cannot
span two engines, and an invariant the database checks beats one a validator
remembers to.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any

from core.ontology.kuzu_schema import OntologyError, view

#: LinkML range → Postgres column type.
#:
#: `date` maps to DATE here and to STRING in Kuzu, and the divergence is
#: deliberate rather than an oversight. The graph's string dates exist because
#: a deep-tier event may know only its year, and ISO-8601 sorts lexically so
#: range logic works at every resolution. The wire has no such events: GDELT is
#: daily by construction, every row is `temporal_resolution='day'`, and a real
#: DATE gives a real index for the `(dyad_id, event_time)` scans that are the
#: entire reason this table exists. The assumption is not left to a comment —
#: `_RESOLUTION_CHECK` below makes the database reject a row that breaks it.
_PG_TYPE = {
    "string": "TEXT",
    "integer": "BIGINT",
    "float": "DOUBLE PRECISION",
    "double": "DOUBLE PRECISION",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMPTZ",
    "uriorcurie": "TEXT",
}

#: Slots whose Postgres type is not what their LinkML range implies. Only one,
#: and it is the one the table is ordered by: `event_time` has no explicit
#: range in the ontology (so it induces to `string`), but in THIS table it is
#: always a full ISO day. See the `_PG_TYPE` note — declaring it DATE is what
#: makes the range scans indexable instead of lexical.
_PG_OVERRIDE = {"event_time": "DATE"}

#: Slots that exist on the graph node and have no place in the corpus.
#: `embedding` is a Kuzu FLOAT[1024] backing its vector index; there is no
#: vector index here and a column of nulls is not a schema.
_SKIP = frozenset({"node_id", "embedding"})

#: The edges that become COLUMNS. In the graph an event points at its dyad, its
#: initiator, its target and its source through four relationship tables. Here
#: they are four foreign keys on one row, which is the same fact stored the way
#: the reader asks for it — every bulk query groups by dyad, so the dyad has to
#: be on the row rather than one join away. `dyad_id` is computed at load time
#: by `classifier.escalation.dyad_id`, the same pure function the graph uses,
#: so the two stores cannot disagree about what a dyad is.
_LINK_COLUMNS = (
    ("dyad_id", "TEXT NOT NULL"),
    # The Dyad's display name, carried alongside its id because the graph's
    # `d.name` is one join away here and every consumer reads the pair
    # together. Nullable on purpose: `models/panel` already falls back to the
    # id (`row["dyad_name"] or dyad`), so a row that never got a name degrades
    # to something readable instead of failing.
    ("dyad_name", "TEXT"),
    ("initiator_id", "TEXT NOT NULL"),
    ("target_id", "TEXT NOT NULL"),
    ("source_id", "TEXT NOT NULL REFERENCES wire_source(source_id)"),
)

#: The wire is daily or it is not the wire. Enforced, not assumed — the DATE
#: column above is only correct while this holds, so the database is the thing
#: that holds it.
_RESOLUTION_CHECK = "CONSTRAINT wire_event_is_daily CHECK (temporal_resolution = 'day')"

WIRE_TABLE = "wire_event"
SOURCE_TABLE = "wire_source"


@dataclass(frozen=True)
class Column:
    name: str
    pg_type: str
    required: bool
    derived: bool

    def ddl(self) -> str:
        null = " NOT NULL" if self.required else ""
        return f"{self.name} {self.pg_type}{null}"


@dataclass(frozen=True)
class TableSpec:
    """One Postgres table derived from one ontology class."""

    cls: str
    table: str
    columns: list[Column] = field(default_factory=list)
    extra: tuple[tuple[str, str], ...] = ()
    checks: tuple[str, ...] = ()

    def ddl(self) -> str:
        cols = ["node_id TEXT PRIMARY KEY"]
        cols.extend(c.ddl() for c in self.columns)
        cols.extend(f"{name} {decl}" for name, decl in self.extra)
        cols.extend(self.checks)
        body = ",\n    ".join(cols)
        return f"CREATE TABLE IF NOT EXISTS {self.table} (\n    {body}\n)"


def _annotation(element: Any, key: str) -> str | None:
    """One annotation off a LinkML element, or None.

    A local copy rather than an import of `kuzu_schema._ann`: reaching for
    another module's private helper is how two derivations quietly drift into
    depending on each other's internals.
    """
    annotations = getattr(element, "annotations", None)
    if not annotations:
        return None
    try:
        found = annotations[key]
    except (KeyError, TypeError):
        return None
    value = getattr(found, "value", found)
    return None if value is None else str(value)


def _columns_for(class_name: str) -> list[Column]:
    sv = view()
    out: list[Column] = []
    for slot_name in sv.class_slots(class_name):
        if slot_name in _SKIP:
            continue
        slot = sv.induced_slot(slot_name, class_name)
        rng = (slot.range or "string").lower()
        pg_type = _PG_OVERRIDE.get(slot_name) or _PG_TYPE.get(rng, "TEXT")
        out.append(
            Column(
                name=slot_name,
                pg_type=pg_type,
                # A DERIVED slot is written by a later pass (Head B's escalation
                # fields), so it cannot be NOT NULL at insert time however
                # required the ontology says it is.
                required=bool(slot.required) and not _is_derived(slot),
                derived=_is_derived(slot),
            )
        )
    return out


def _is_derived(slot: Any) -> bool:
    return (_annotation(slot, "derived") or "").strip().lower() in {"true", "1", "yes"}


@functools.lru_cache(maxsize=1)
def wire_spec() -> TableSpec:
    """The corpus table, derived from the ontology's Event class."""
    columns = _columns_for("Event")
    if not columns:
        raise OntologyError(
            "the ontology's Event class produced no columns — "
            f"{WIRE_TABLE} cannot be derived from an empty class"
        )
    return TableSpec(
        cls="Event",
        table=WIRE_TABLE,
        columns=columns,
        extra=_LINK_COLUMNS,
        checks=(_RESOLUTION_CHECK,),
    )


@functools.lru_cache(maxsize=1)
def source_spec() -> TableSpec:
    """The mirrored Source table — the other half of a real foreign key."""
    return TableSpec(cls="Source", table=SOURCE_TABLE, columns=_columns_for("Source"))


def ddl() -> list[str]:
    """Every statement needed to stand the corpus up, in dependency order.

    Sources BEFORE events, which is the same ordering the graph loaders obey
    for the same reason: the foreign key is the provenance invariant, and a
    fact cannot cite a source that does not exist yet. Here the database is
    what refuses, rather than `validate_edge`.
    """
    source = source_spec()
    # The mirror is keyed by source_id, not node_id, because that is the column
    # `wire_event.source_id` references and what the loaders carry.
    source_ddl = source.ddl().replace("node_id TEXT PRIMARY KEY", "source_id TEXT PRIMARY KEY")
    return [
        source_ddl,
        wire_spec().ddl(),
        # The index IS the reason for this table. Every bulk consumer groups by
        # dyad and orders by time, so this one composite serves all of them;
        # region_pack is separate because the packs are queried independently.
        f"CREATE INDEX IF NOT EXISTS {WIRE_TABLE}_dyad_time_idx "
        f"ON {WIRE_TABLE} (dyad_id, event_time)",
        f"CREATE INDEX IF NOT EXISTS {WIRE_TABLE}_region_idx "
        f"ON {WIRE_TABLE} (region_pack)",
        # Head B's rescore reads and writes this column across the whole
        # archive; a partial index keeps "what is still unscored" cheap.
        f"CREATE INDEX IF NOT EXISTS {WIRE_TABLE}_unscored_idx "
        f"ON {WIRE_TABLE} (region_pack) WHERE escalation_magnitude IS NULL",
    ]


def columns() -> list[str]:
    """Insert-order column names — what the loader's COPY must match.

    node_id FIRST and included: it is the primary key, so it is the one column
    a COPY cannot omit, and deriving the list here rather than writing it out
    at the call site is what keeps the loader from drifting off the ontology.
    """
    return (
        ["node_id"]
        + [c.name for c in wire_spec().columns]
        + [name for name, _ in _LINK_COLUMNS]
    )


def summary() -> dict[str, Any]:
    """What was derived, for a boot log or a test that reads the shape."""
    spec = wire_spec()
    return {
        "table": spec.table,
        "derived_from": spec.cls,
        "columns": len(spec.columns) + len(_LINK_COLUMNS),
        "links": [name for name, _ in _LINK_COLUMNS],
        "derived_slots": [c.name for c in spec.columns if c.derived],
    }
