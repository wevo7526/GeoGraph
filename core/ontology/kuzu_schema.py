"""The ontology, read at runtime from `core/ontology/geograph.linkml.yaml`.

THIS MODULE IS WHY THE YAML IS NOT A BUILD ARTIFACT — the MarketGraph pattern,
carried over whole. The Kuzu DDL, the write-time validators, the sourced-edge
list and the traversable-edge list are all DERIVED HERE from the schema, every
process start. Nothing downstream keeps its own copy, so nothing can drift.

WHERE THE PROVENANCE INVARIANT IS ENFORCED. Kuzu has no NOT NULL on
relationship properties, so `validate_edge` is the chokepoint: it reads
`required: true` off the schema and refuses the write. Every writer goes
through it (`core.graph.kuzu_store.merge_edges`); `check_provenance` is the
post-ingest backstop.

The generated Pydantic models and JSON Schema (scripts/generate_ontology.py →
core/ontology/generated/) serve the INGESTION boundary; this module serves the
STORAGE boundary. Both read the same YAML.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from linkml_runtime.utils.schemaview import SchemaView

SCHEMA_PATH = Path(__file__).resolve().parent / "geograph.linkml.yaml"

#: LinkML range → Kuzu column type. Dates are STRING deliberately: every date in
#: this project is an ISO-8601 string — deep-tier events may only know their
#: year, and ISO-8601 sorts lexically, so range comparisons are correct at every
#: resolution without a date type or a driver conversion layer.
_KUZU_TYPE = {
    "string": "STRING",
    "integer": "INT64",
    "float": "DOUBLE",
    "double": "DOUBLE",
    "boolean": "BOOLEAN",
    "date": "STRING",
    "datetime": "STRING",
    "uriorcurie": "STRING",
}


class OntologyError(RuntimeError):
    """A write violates the ontology. The message names the rule and the fix."""


@dataclass(frozen=True)
class Prop:
    name: str
    kuzu_type: str
    required: bool
    derived: bool


@dataclass(frozen=True)
class NodeSpec:
    """A node class: one Kuzu node table."""

    cls: str
    table: str
    id_prefix: str
    props: list[Prop] = field(default_factory=list)

    def ddl(self) -> str:
        cols = ", ".join(f"{p.name} {p.kuzu_type}" for p in self.props)
        return (
            f"CREATE NODE TABLE IF NOT EXISTS {self.table}"
            f"(node_id STRING, {cols}, PRIMARY KEY(node_id))"
        )


@dataclass(frozen=True)
class EdgeSpec:
    """An edge class: one Kuzu rel table."""

    cls: str
    rel: str
    src: str
    dst: str
    sourced: bool
    #: Properties that IDENTIFY an edge rather than describe it — they belong in
    #: the MERGE pattern, so two values mean two edges. `as_of` on FLOW makes
    #: two quarters two rows; `window` on AFFECTED makes car_0_1 and car_0_5
    #: two measurements instead of one overwriting the other.
    key_slots: tuple[str, ...] = ()
    props: list[Prop] = field(default_factory=list)

    def ddl(self) -> str:
        cols = "".join(f", {p.name} {p.kuzu_type}" for p in self.props)
        return f"CREATE REL TABLE IF NOT EXISTS {self.rel}(FROM {self.src} TO {self.dst}{cols})"

    @property
    def required_props(self) -> list[str]:
        return [p.name for p in self.props if p.required]


def _ann(element: Any, key: str) -> str | None:
    """Read one annotation off a LinkML element.

    LinkML exposes annotations as a `JsonObj`, not a dict — it has no `.get` —
    and each entry is an `Annotation` whose payload is under `.value`. Both
    layers are unwrapped here so callers see a plain string or None.
    """
    annotations = getattr(element, "annotations", None)
    if annotations is None:
        return None
    entry = (
        annotations.get(key)
        if hasattr(annotations, "get")
        else getattr(annotations, key, None)
    )
    if entry is None:
        return None
    return str(getattr(entry, "value", entry))


def _is_true(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"true", "yes", "1"}


@functools.lru_cache(maxsize=1)
def view() -> SchemaView:
    if not SCHEMA_PATH.exists():
        raise OntologyError(
            f"{SCHEMA_PATH} is missing. It is the source of truth for the "
            "ontology — the DDL and the validators are generated from it."
        )
    return SchemaView(str(SCHEMA_PATH))


def _props_for(sv: SchemaView, class_name: str, skip: set[str]) -> list[Prop]:
    out: list[Prop] = []
    for slot_name in sv.class_slots(class_name):
        if slot_name in skip:
            continue
        slot = sv.induced_slot(slot_name, class_name)
        # A `kuzu_type` annotation overrides the range mapping — how the
        # analogy embedding declares FLOAT[1024] for Kuzu's vector index
        # without inventing a LinkML vector type.
        override = _ann(slot, "kuzu_type")
        rng = (slot.range or "string").lower()
        # An enum range is stored as its string value. The closed vocabulary
        # is NOT enforced at this boundary — validate_node/validate_edge check
        # required-presence only; the generated Pydantic models (the ingestion
        # boundary) are where enum membership is checked.
        kuzu_type = override or _KUZU_TYPE.get(rng, "STRING")
        out.append(
            Prop(
                name=slot_name,
                kuzu_type=kuzu_type,
                required=bool(slot.required),
                derived=_is_true(_ann(slot, "derived")),
            )
        )
    return out


@functools.lru_cache(maxsize=1)
def nodes() -> dict[str, NodeSpec]:
    """Node classes, keyed by Kuzu table name."""
    sv = view()
    out: dict[str, NodeSpec] = {}
    for name, cls in sv.all_classes().items():
        if cls.abstract or _ann(cls, "graph_element") != "node":
            continue
        table = _ann(cls, "kuzu_table") or name
        out[table] = NodeSpec(
            cls=name,
            table=table,
            id_prefix=_ann(cls, "id_prefix") or table.lower(),
            props=_props_for(sv, name, skip={"node_id"}),
        )
    return out


@functools.lru_cache(maxsize=1)
def edges() -> dict[str, EdgeSpec]:
    """Edge classes, keyed by Kuzu relationship name."""
    sv = view()
    node_table = {spec.cls: spec.table for spec in nodes().values()}
    out: dict[str, EdgeSpec] = {}
    for name, cls in sv.all_classes().items():
        if cls.abstract or _ann(cls, "graph_element") != "edge":
            continue
        rel = _ann(cls, "kuzu_rel") or name.upper()
        src_cls = _ann(cls, "from") or ""
        dst_cls = _ann(cls, "to") or ""
        out[rel] = EdgeSpec(
            cls=name,
            rel=rel,
            src=node_table.get(src_cls, src_cls),
            dst=node_table.get(dst_cls, dst_cls),
            sourced=_is_true(_ann(cls, "sourced")),
            key_slots=tuple(
                part.strip() for part in (_ann(cls, "key_slots") or "").split(",") if part.strip()
            ),
            props=_props_for(sv, name, skip=set()),
        )
    return out


@functools.lru_cache(maxsize=1)
def sourced_edges() -> tuple[str, ...]:
    """Edge types that assert a fact and therefore must cite a Source.

    Read off the schema rather than hardcoded, so a new edge class that asserts
    something automatically inherits the provenance requirement instead of
    quietly escaping it.
    """
    return tuple(sorted(rel for rel, spec in edges().items() if spec.sourced))


@functools.lru_cache(maxsize=1)
def traversable_edges() -> tuple[str, ...]:
    """Edge types that mean something in "how does A connect to B".

    Classification edges are excluded: every Event points at the same few
    Regime and Source nodes, so including OCCURRED_IN or DERIVED_FROM makes any
    two events two hops apart — true and completely uninformative.
    """
    raw = (view().schema.settings or {}).get("traversable_edges")
    value = getattr(raw, "setting_value", raw)
    if not value:
        return tuple(sorted(edges()))
    return tuple(part.strip() for part in str(value).split(",") if part.strip())


@functools.lru_cache(maxsize=1)
def id_prefixes() -> dict[str, str]:
    """Kuzu table → the typed-id prefix used across the API and the explorer."""
    return {spec.table: spec.id_prefix for spec in nodes().values()}


def node_id(table: str, key: str) -> str:
    """Build a typed, stable node id — `actor:cow-670`, `regime:bretton-woods`."""
    return f"{id_prefixes().get(table, table.lower())}:{key}"


def ddl() -> list[str]:
    """The full schema, in dependency order: node tables before rel tables."""
    return [spec.ddl() for spec in nodes().values()] + [
        spec.ddl() for spec in edges().values()
    ]


def validate_edge(rel: str, props: dict[str, Any]) -> None:
    """THE PROVENANCE CHOKEPOINT. Raises rather than writing an invalid edge.

    Kuzu cannot express "this relationship property is required", so this is
    where the schema's `required: true` becomes enforcement. Every writer calls
    it; nothing writes an edge without passing through here.
    """
    spec = edges().get(rel)
    if spec is None:
        raise OntologyError(
            f"{rel!r} is not an edge type in the ontology. Valid: {sorted(edges())}"
        )
    for name in spec.required_props:
        if props.get(name) in (None, ""):
            raise OntologyError(
                f"{rel} requires {name!r} and it is missing. "
                + (
                    "This is the provenance invariant: an edge asserting a fact "
                    "about the world must name the Source it came from."
                    if name == "source_id"
                    else f"See `{name}` in core/ontology/geograph.linkml.yaml."
                )
            )


def validate_node(table: str, props: dict[str, Any]) -> None:
    spec = nodes().get(table)
    if spec is None:
        raise OntologyError(
            f"{table!r} is not a node type in the ontology. Valid: {sorted(nodes())}"
        )
    for prop in spec.props:
        if prop.required and props.get(prop.name) in (None, ""):
            raise OntologyError(f"{table} requires {prop.name!r} and it is missing.")


def summary() -> dict[str, Any]:
    """What the ontology currently declares — surfaced by the API so the
    running schema is inspectable rather than taken on trust."""
    return {
        "nodes": {
            spec.table: {
                "idPrefix": spec.id_prefix,
                "props": [p.name for p in spec.props],
                "derived": [p.name for p in spec.props if p.derived],
            }
            for spec in nodes().values()
        },
        "edges": {
            spec.rel: {
                "from": spec.src,
                "to": spec.dst,
                "sourced": spec.sourced,
                "required": spec.required_props,
                "keySlots": list(spec.key_slots),
                "props": [p.name for p in spec.props],
            }
            for spec in edges().values()
        },
        "sourcedEdges": list(sourced_edges()),
        "traversableEdges": list(traversable_edges()),
    }
