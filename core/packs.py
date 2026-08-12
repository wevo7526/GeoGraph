"""The region pack contract — build-spec section 16.

A pack is a directory under `packs/` providing seven YAML files; THE CORE
READS A PACK AND RUNS UNCHANGED. That sentence is the contract: nothing in
`core/` may special-case a region name. MENA is pack one and proves the
model; China/Taiwan is pack two and proves the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from core.classifier import typing as event_typing

_ROOT = Path(__file__).resolve().parent.parent
PACKS_DIR = _ROOT / "packs"

#: The seven files every pack must provide. Locked by the spec.
PACK_FILES: tuple[str, ...] = (
    "actors.yaml",
    "issues.yaml",
    "markets.yaml",
    "assets.yaml",
    "priors.yaml",
    "sources.yaml",
    "marquee_events.yaml",
)


class PackError(RuntimeError):
    """A pack violates the contract. The message names the file and the rule."""


@dataclass(frozen=True)
class Pack:
    name: str
    path: Path
    data: dict[str, Any]  # filename stem → parsed YAML

    @property
    def markets(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.data["markets"].get("markets", []))

    @property
    def actors(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.data["actors"].get("actors", []))

    @property
    def relations(self) -> list[dict[str, Any]]:
        """Durable RELATES_TO rows declared beside the roster they connect."""
        return cast(list[dict[str, Any]], self.data["actors"].get("relations", []))

    @property
    def marquee_events(self) -> list[dict[str, Any]]:
        return cast(list[dict[str, Any]], self.data["marquee_events"].get("events", []))

    @property
    def paper_books(self) -> dict[str, dict[str, float]] | None:
        """The pack's fixed paper-trade translation — signed ticker weights for
        the escalation and reversion books, declared beside the assets they
        trade. Optional: a pack without books simply has no paper model, and
        the endpoints say so rather than borrowing another region's tickers."""
        books = self.data["assets"].get("paper_books")
        return cast(dict[str, dict[str, float]], books) if books else None

    @property
    def case_study(self) -> dict[str, Any] | None:
        """The narrated episode, if the pack declares one.

        Reader-facing prose belongs in the pack, versioned alongside the events
        it describes, rather than in the explorer where it would drift from the
        numbers it is explaining.
        """
        study = self.data["marquee_events"].get("case_study")
        return cast(dict[str, Any], study) if study else None


def available() -> list[str]:
    """Pack names that satisfy the contract completely. A half-finished pack
    directory is simply not listed — absent, not broken."""
    if not PACKS_DIR.exists():
        return []
    return sorted(
        p.name
        for p in PACKS_DIR.iterdir()
        if p.is_dir() and all((p / f).exists() for f in PACK_FILES)
    )


def load(name: str) -> Pack:
    """Load and validate one pack. Raises PackError naming what is wrong."""
    path = PACKS_DIR / name
    if not path.is_dir():
        raise PackError(f"packs/{name} does not exist. Available: {available()}")

    data: dict[str, Any] = {}
    for filename in PACK_FILES:
        file = path / filename
        if not file.exists():
            raise PackError(
                f"packs/{name}/{filename} is missing. The pack contract "
                f"(build-spec section 16) requires all of: {', '.join(PACK_FILES)}"
            )
        with open(file, encoding="utf-8") as fh:
            data[filename.removesuffix(".yaml")] = yaml.safe_load(fh) or {}

    pack = Pack(name=name, path=path, data=data)
    _validate(pack)
    return pack


def _validate(pack: Pack) -> None:
    # Markets without inception dates are how a deep-past event study silently
    # "measures" a market that did not exist. Refused at load, not discovered
    # in a result.
    for market in pack.markets:
        for required in ("ticker", "inception_date", "trading_calendar", "market_type"):
            if not market.get(required):
                raise PackError(
                    f"packs/{pack.name}/markets.yaml: market {market.get('name', market)!r} "
                    f"is missing {required!r}. Every market carries its inception date and "
                    "calendar — the transmission engine skips markets that did not exist "
                    "at event time, and it can only do that if the date is here."
                )
    for actor in pack.actors:
        if not actor.get("actor_type"):
            raise PackError(
                f"packs/{pack.name}/actors.yaml: actor {actor.get('name', actor)!r} "
                "is missing 'actor_type' (state | org | person | swf)."
            )

    # Relations are SOURCED edges between roster actors: a row that names a
    # ghost actor or an undeclared source would seed a provenance violation,
    # so it is refused here, where the filename can be named.
    actor_ids = {a["id"] for a in pack.actors}
    source_ids = {s["id"] for s in pack.data["sources"].get("sources", [])}
    for rel in pack.relations:
        for required in ("a", "b", "relation_type", "valid_from", "source"):
            if not rel.get(required):
                raise PackError(
                    f"packs/{pack.name}/actors.yaml: relation {rel!r} is missing "
                    f"{required!r}."
                )
        for end in ("a", "b"):
            if rel[end] not in actor_ids:
                raise PackError(
                    f"packs/{pack.name}/actors.yaml: relation "
                    f"{rel['a']} → {rel['b']} names {rel[end]!r}, which is not an "
                    "actor in this pack's roster."
                )
        if rel["source"] not in source_ids:
            raise PackError(
                f"packs/{pack.name}/actors.yaml: relation {rel['a']} → {rel['b']} "
                f"cites {rel['source']!r}, which is not in sources.yaml. Sourced "
                "edges cite sources that exist (build-spec section 17)."
            )
    for event in pack.marquee_events:
        for required in ("id", "date", "name"):
            if not event.get(required):
                raise PackError(
                    f"packs/{pack.name}/marquee_events.yaml: event {event!r} is missing "
                    f"{required!r}."
                )
        # The quad class is a PARTITION OF THE CAMEO ROOT CODES, so a curated
        # quad_class that contradicts its own code is a data error. Caught at
        # load rather than seeded: a mis-typed event would otherwise reach the
        # graph, and the CAMEO code also sets the Goldstein score that Head B
        # measures escalation with, so a sign-flipped code corrupts a dyad's
        # whole baseline.
        code = event.get("cameo")
        if not code:
            continue
        try:
            implied = event_typing.quad_class_for(code)
        except (KeyError, ValueError) as exc:
            raise PackError(
                f"packs/{pack.name}/marquee_events.yaml: event {event['id']} has "
                f"cameo {code!r}, which is not resolvable — {exc}"
            ) from exc
        declared = event.get("quad_class")
        if declared and declared != implied:
            raise PackError(
                f"packs/{pack.name}/marquee_events.yaml: event {event['id']} declares "
                f"quad_class {declared!r} but CAMEO {code!r} "
                f"({event_typing.label_for(code)}) implies {implied!r}. The quad class "
                "follows the code — fix whichever is wrong, but they cannot disagree."
            )

    # Paper books are the region's OWN market translation; a malformed book
    # would silently trade the wrong thing, so shape errors are refused here.
    books = pack.paper_books
    if books is not None:
        for side in ("escalation", "reversion"):
            book = books.get(side)
            if not isinstance(book, dict) or not book:
                raise PackError(
                    f"packs/{pack.name}/assets.yaml: paper_books needs a non-empty "
                    f"{side!r} mapping of ticker → signed weight."
                )
            for ticker, weight in book.items():
                if isinstance(weight, bool) or not isinstance(weight, int | float):
                    raise PackError(
                        f"packs/{pack.name}/assets.yaml: paper_books.{side}.{ticker} "
                        f"weight {weight!r} is not a number."
                    )

    _validate_case_study(pack)


def _validate_case_study(pack: Pack) -> None:
    """A narrated episode must name events that exist, and must agree with the
    `phase0_candidate` flags the transmission engine actually runs on.

    Two ways to say which events make up the episode is one way to have them
    disagree — with the pack claiming a story the numbers were never computed
    for.
    """
    study = pack.case_study
    if study is None:
        return
    for required in ("slug", "title", "events"):
        if not study.get(required):
            raise PackError(
                f"packs/{pack.name}/marquee_events.yaml: case_study is missing "
                f"{required!r}."
            )
    known = {e["id"] for e in pack.marquee_events}
    named = list(study["events"])
    for event_id in named:
        if event_id not in known:
            raise PackError(
                f"packs/{pack.name}/marquee_events.yaml: case_study names "
                f"{event_id}, which is not an event in this pack."
            )
    flagged = [e["id"] for e in pack.marquee_events if e.get("phase0_candidate")]
    if flagged and sorted(flagged) != sorted(named):
        raise PackError(
            f"packs/{pack.name}/marquee_events.yaml: case_study names "
            f"{sorted(named)} but phase0_candidate flags {sorted(flagged)}. The "
            "narrated episode and the measured one must be the same episode."
        )
