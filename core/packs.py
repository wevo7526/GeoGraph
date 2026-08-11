"""The region pack contract — build-spec section 16.

A pack is a directory under `packs/` providing seven YAML files; THE CORE
READS A PACK AND RUNS UNCHANGED. That sentence is the contract: nothing in
`core/` may special-case a region name. MENA is pack one and proves the
model; China/Taiwan is pack two and proves the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

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
        return self.data["markets"].get("markets", [])

    @property
    def actors(self) -> list[dict[str, Any]]:
        return self.data["actors"].get("actors", [])

    @property
    def marquee_events(self) -> list[dict[str, Any]]:
        return self.data["marquee_events"].get("events", [])


def available() -> list[str]:
    """Pack names that satisfy the contract completely. A half-finished pack
    directory (packs/china before Phase 6) is simply not listed — absent, not
    broken."""
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
    for event in pack.marquee_events:
        for required in ("id", "date", "name"):
            if not event.get(required):
                raise PackError(
                    f"packs/{pack.name}/marquee_events.yaml: event {event!r} is missing "
                    f"{required!r}."
                )
