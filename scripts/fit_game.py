"""Fit the game's payoff parameters and write their artifact — OFFLINE.

  python scripts/fit_game.py                     # every installed region
  python scripts/fit_game.py --region mena       # one
  python scripts/fit_game.py --family ally       # the ally game, on ally pairs
  python scripts/fit_game.py --dry-run           # report, write nothing

SAME REASON THE MODEL TRAINS OFFLINE. Indirect inference solves the
equilibrium and simulates a panel once per objective evaluation — a hundred
and twenty of them per region, which is minutes. The boot's forecast step has
five minutes for EVERY region and every mode, and a wall-clock timeout there
does not lose the sequence forecast alone, it loses the two counted forecasts
with it. Structural payoffs are also not a thing that should change per
deploy: they are fitted to the archive's own history, so refitting them every
boot would make each container's numbers a slightly different model for no
gain.

So this writes models/game-<region>.json and the boot reads it, exactly as
models/intensity.json works. A region with no artifact simply gets no
sequence forecast, and says so.

Read-only against the graph; takes no write lock.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import packs  # noqa: E402
from core import settings as settings_module  # noqa: E402
from core.games import estimate, transition  # noqa: E402
from core.games import family as family_module  # noqa: E402
from core.games import state as state_module  # noqa: E402
from core.graph import kuzu_store  # noqa: E402
from core.models import panel as panel_module  # noqa: E402
from core.models import registry  # noqa: E402
from core.wire import corpus  # noqa: E402

#: A kernel under this share of measured cells cannot carry an equilibrium
#: worth fitting — the same bar the freeze applies before it will speak.
MIN_KERNEL_COVERAGE = 0.5


#: Where the deep tier's raw files live when they have been fetched — the
#: COW alliance list is the widest source of DECLARED alliances the fit can
#: select ally pairs by, and it is a public, versioned dataset (v4.1). The
#: packs' own `relations` are the other source and are always present.
_RAW_DIR = Path(
    os.getenv("GEOGRAPH_RAW_DIR") or Path(__file__).resolve().parent.parent / "data" / "raw"
)


def ally_windows(region: str) -> tuple[dict[str, list[tuple[int, int]]], list[str]]:
    """dyad → the YEAR WINDOWS in which the archive declares it allied, and
    where the declarations came from.

    An ally pair is one the archive DECLARES allied — the family classifier's
    own standing rule — read offline from the pack's `relations` and, when the
    COW alliance file is on disk, from COW's directed alliance list restricted
    to the pack's roster. WINDOWS, NOT PAIRS: a first pass took every pair
    ever allied since 1905, which put the United States and China (1942) and
    the United States and Iran (1958-79) in the ALLY sample with their whole
    wire-era record — a rivalry's record fitted as an alliance's. A quarter
    enters the fit only inside a window; the game itself is solved on the
    pair's CURRENT standing. An open window is (start, 9999).
    """
    from core.classifier import escalation

    pack = packs.load(region)
    roster = {str(a["id"]) for a in pack.actors}
    ccode_to_id = {
        int(a["cow_ccode"]): str(a["id"]) for a in pack.actors if a.get("cow_ccode")
    }
    windows: dict[str, list[tuple[int, int]]] = {}
    sources: list[str] = []

    def _year(value: Any, default: int) -> int:
        text = str(value or "").strip()
        return int(text[:4]) if text[:4].isdigit() else default

    for relation in pack.relations:
        if relation.get("relation_type") in ("alliance", "membership"):
            dyad = escalation.dyad_id(str(relation["a"]), str(relation["b"]))
            windows.setdefault(dyad, []).append(
                (_year(relation.get("valid_from"), 1905), _year(relation.get("valid_to"), 9999))
            )
    if windows:
        sources.append("packs")
    cow_file = _RAW_DIR / "alliance_v4.1_by_directed.csv"
    if cow_file.exists():
        import csv

        with open(cow_file, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    a = ccode_to_id.get(int(row["ccode1"]))
                    b = ccode_to_id.get(int(row["ccode2"]))
                except (TypeError, ValueError):
                    continue
                if not a or not b or a == b or a not in roster or b not in roster:
                    continue
                start = _year(row.get("dyad_st_year"), 1905)
                end = _year(row.get("dyad_end_year"), 9999)
                if end < 1905:
                    continue
                windows.setdefault(escalation.dyad_id(a, b), []).append((start, end))
        sources.append("cow:alliance_v4.1")
    return windows, sources


def _in_windows(year: int, windows: list[tuple[int, int]]) -> bool:
    return any(start <= year <= end for start, end in windows)


def prepare_region(
    db_path: Path, region: str, *,
    space: family_module.ActionSpace = family_module.ADVERSARY,
) -> tuple[list[dict[str, Any]], dict[Any, Any], dict[str, Any]] | None:
    """(table, joint, pair_note) for one region in a family's space — the
    rows and joint actions the fit is entitled to fit on. None when there is
    nothing to fit."""
    # THE CORPUS FIRST, THE GRAPH AS FALLBACK. Fitting is offline and its
    # output is committed, so it must be a pure function of the repository —
    # the artifacts are in git and the parser, the crosswalks and Head B are
    # all deterministic, which makes the fit reproducible by anyone holding
    # this commit. Reading the graph instead made it a function of whatever
    # happened to be loaded on the machine, and loading it first cost ~45
    # minutes per pack against 5 seconds here.
    #
    # The graph path is kept for a lens with no shipped artifact, where the
    # graph genuinely is the only source.
    if corpus.artifacts_for(region):
        panel_view, events = corpus.views(region)
        table = panel_module.build(panel_view, region_pack=region)
    else:
        conn = kuzu_store.connect(db_path, read_only=True)
        try:
            table = panel_module.build(
                panel_module.dyad_event_rows(conn), region_pack=region
            )
            events = transition.event_rows(conn)
        finally:
            kuzu_store.close(conn)

    if not table:
        print(f"{region}: no modelable dyad")
        return None

    joint = transition.joint_actions(
        events, quarter_of=panel_module.quarter_index, space=space
    )
    pair_note: dict[str, Any] = {}
    if space.family == "ally":
        # THE ALLY GAME IS FITTED ON ALLY PAIRS — declared allied, and read
        # as such by the same coercive-share cut the classifier applies. The
        # kernel is counted over the same pairs, in the ally reading, so the
        # transitions the payoffs have to explain are the alliances' own.
        from core.games import opening as opening_module

        declared, sources = ally_windows(region)
        # Rows INSIDE an alliance window only, then the behavioural cut on
        # what remains: a pair whose in-window record is coercive above the
        # adversary bar is not an ally in the sense the game means.
        windowed = [
            row for row in table
            if str(row["dyad_id"]) in declared
            and _in_windows(int(str(row["date"])[:4]), declared[str(row["dyad_id"])])
        ]
        by_dyad: dict[str, list[dict[str, Any]]] = {}
        for row in windowed:
            by_dyad.setdefault(str(row["dyad_id"]), []).append(row)
        chosen = {
            dyad for dyad, rows in by_dyad.items()
            if (opening_module.posture(rows).get("share") or 0.0) < family_module.ADVERSARY_SHARE
        }
        table = [row for row in windowed if str(row["dyad_id"]) in chosen]
        kept_cells = {(str(r["dyad_id"]), int(r["q"])) for r in table}
        joint = {k: v for k, v in joint.items() if k in kept_cells}
        pair_note = {"pairs": sorted(chosen), "pair_sources": sources,
                     "declared": len(declared), "windowed": True}
        print(f"{region}: {len(chosen)} ally pairs of {len(declared)} declared, "
              f"{len(table):,} in-window rows ({', '.join(sources) or 'no source'})")
        if not table:
            print(f"{region}: no ally pair with a series — skipping")
            return None
    return table, joint, pair_note


def fit_prepared(
    label: str, table: list[dict[str, Any]], joint: dict[Any, Any], *,
    evaluations: int, space: family_module.ActionSpace, pair_note: dict[str, Any],
) -> dict[str, Any] | None:
    """Count the kernel, fit the payoffs, report — over rows already chosen."""
    region = label
    kernel, observed = transition.kernel(transition.count(table, joint, space))
    coverage = transition.coverage(observed)
    print(f"{region}: panel {len(table):,} rows, kernel "
          f"{coverage['share_measured']:.0%} measured "
          f"({coverage['observations']:,} transitions)")
    if coverage["share_measured"] < MIN_KERNEL_COVERAGE:
        print(f"{region}: kernel too sparse to fit — skipping")
        return None

    frequencies = estimate.observed_frequencies(table, joint, space)
    result = estimate.fit(kernel, frequencies, max_evaluations=evaluations, space=space)
    print(f"{region}: distance {result['distance']} "
          f"({result['evaluations']} evaluations, converged={result['converged']})")
    print(f"{region}: payoffs {json.dumps(result['payoffs'])}")

    # The BOUNDARY CAVEAT, computed rather than remembered. A parameter that
    # settles on its own clip bound means the optimiser wanted to go further
    # and the game cannot reproduce the archive's action mix — the value is a
    # direction, not an estimate, and anything reading this artifact should
    # be able to see that without re-deriving it.
    bounds = {
        "discount": (0.5, 0.99),
        "cost_resolute": (0.05, 3.0),
        "cost_irresolute": (0.05, 6.0),
        "stake": (0.1, 3.0),
        "audience": (0.0, 2.0),
    }
    at_bound = [
        name for name, (low, high) in bounds.items()
        if abs(result["payoffs"][name] - low) < 1e-6
        or abs(result["payoffs"][name] - high) < 1e-6
    ]
    if at_bound:
        print(f"{region}: AT BOUNDS {at_bound} — payoff magnitudes are a "
              "direction, not an estimate")

    return {
        "region": region,
        "family": space.family,
        "actions": list(space.actions),
        **pair_note,
        "observed_frequencies": result["observed_frequencies"],
        "simulated_frequencies": result["simulated_frequencies"],
        "payoffs": result["payoffs"],
        "distance": result["distance"],
        "converged": result["converged"],
        "evaluations": result["evaluations"],
        "seed": result["seed"],
        "at_bounds": at_bound,
        "kernel": coverage,
        "panel_rows": len(table),
        "bands": list(state_module.INTENSITY_EDGES),
        "identification": result["identification"],
        "method": result["method"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None)
    parser.add_argument("--region", default=None, help="default: every installed pack")
    parser.add_argument("--evaluations", type=int, default=150)
    parser.add_argument("--family", default="adversary", choices=sorted(family_module.SPACES),
                        help="which family's game to fit; 'ally' fits the burden-sharing "
                             "game on the region's declared ally pairs and writes "
                             "models/game-ally-<region>.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    space = family_module.space_for(args.family)

    db_path = Path(args.db) if args.db else settings_module.load().kuzu_db_path
    # A graph is only required for a lens with NO shipped corpus. Demanding one
    # unconditionally would put a 45-minute load in front of a fit that reads
    # the artifacts in five seconds — and would make a committed artifact
    # depend on the state of one machine's volume.
    if not corpus.installed() and not Path(db_path).exists():
        print(f"no corpus artifacts and no graph at {db_path}")
        return 1

    regions = [args.region] if args.region else packs.available()
    written = 0

    if space.family == "ally":
        # THE ALLY GAME IS FITTED POOLED ACROSS REGIONS. An alliance's
        # burden-sharing parameters — the value of the shared good against a
        # partner's private cost of carrying it — are not a property of a
        # region, and per region the in-window ally sample is thin: china's
        # eleven pairs count a kernel 46% measured, under the bar. Pooled the
        # three regions hold ~30,000 in-window ally rows. One fit, written
        # under every region's name so `context.fitted_payoffs` finds it the
        # same way it finds the adversary fit; `pooled_over` says so.
        pooled_table: list[dict[str, Any]] = []
        pooled_joint: dict[Any, Any] = {}
        pooled_pairs: set[str] = set()
        sources: set[str] = set()
        seen_cells: set[tuple[str, int]] = set()
        for region in regions:
            prepared = prepare_region(Path(db_path), region, space=space)
            if prepared is None:
                continue
            table, joint, note = prepared
            for row in table:
                cell = (str(row["dyad_id"]), int(row["q"]))
                # A shared-roster dyad ships in more than one lens: once.
                if cell in seen_cells:
                    continue
                seen_cells.add(cell)
                pooled_table.append(row)
            pooled_joint.update(joint)
            pooled_pairs.update(note.get("pairs", []))
            sources.update(note.get("pair_sources", []))
        note = {"pairs": sorted(pooled_pairs), "pair_sources": sorted(sources),
                "windowed": True, "pooled_over": list(regions)}
        fitted = fit_prepared(
            "ally (pooled)", pooled_table, pooled_joint,
            evaluations=args.evaluations, space=space, pair_note=note,
        )
        if fitted is None or args.dry_run:
            if args.dry_run:
                print("\n--dry-run: nothing written")
            return 0 if (fitted is not None or args.dry_run) else 1
        for region in regions:
            target = registry.MODELS_DIR / f"game-ally-{region}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {**fitted, "region": region}
            payload["hash"] = registry.content_hash(payload)
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.write("\n")
            print(f"{region}: wrote {target}")
            written += 1
        return 0 if written else 1

    for region in regions:
        try:
            prepared = prepare_region(Path(db_path), region, space=space)
            fitted = None
            if prepared is not None:
                table, joint, note = prepared
                fitted = fit_prepared(
                    region, table, joint, evaluations=args.evaluations, space=space,
                    pair_note=note,
                )
        except Exception as exc:  # noqa: BLE001 - one region must not stop the rest
            print(f"{region}: fit failed — {exc}")
            continue
        if fitted is None or args.dry_run:
            continue
        target = registry.MODELS_DIR / f"game-{region}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        # Stamp the content hash so a hand edit or truncated write is caught
        # on load, matching the intensity model's integrity guarantee.
        fitted["hash"] = registry.content_hash(fitted)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(fitted, fh, indent=2)
            fh.write("\n")
        print(f"{region}: wrote {target}")
        written += 1
    if args.dry_run:
        print("\n--dry-run: nothing written")
    return 0 if (written or args.dry_run) else 1


if __name__ == "__main__":
    raise SystemExit(main())
