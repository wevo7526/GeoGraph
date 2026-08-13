"""Equalise a lens's per-year artifacts so coverage stops masquerading as conflict.

  python scripts/balance_artifacts.py --dir data/derived --report
  python scripts/balance_artifacts.py --dir data/derived --cap 12000 --apply

THE PROBLEM THIS EXISTS FOR. A fixed --min-mentions is not a fixed standard
across time. GDELT's corpus grows: at --min-mentions 50 the 2006 harvest kept
492 MENA events and the 2019 harvest kept 25,553, a 52-fold difference that is
overwhelmingly about how much media the world digitised, not about how much
the Middle East escalated. Load that as-is and every model learns the growth
of the internet — which is precisely the failure docs/ml-spec.md section 2.4
measured, where the decade positive rate tracked wire coverage and a model
trained before 1996 scored worse than knowing nothing.

THE FIX. Take each year's TOP events by mention count, capped at the same
number every year. A cap makes the years comparable by construction: whatever
the corpus size, each year contributes its most-reported events and no more.
It also bounds the load — the cap times the years is the event budget.

Operating on the ARTIFACTS rather than the harvest keeps the expensive half
untouched: the archives are already streamed and gone, the artifacts are a
few megabytes on disk, and re-deciding the cap costs a re-read rather than
another 52 GB across the wire. Harvest wide, load balanced.

The trimmed lines are not lost — `--apply` writes `<name>.full.tsv.gz`
beside the trimmed artifact, so a later, larger cap needs no new downloads.
"""

from __future__ import annotations

import argparse
import gzip
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ingestion import gdelt  # noqa: E402

#: Artifact names this understands: gdelt-<pack>-<year>.tsv.gz. The span
#: artifact (gdelt-mena-1979-2005) is deliberately NOT matched — the yearly
#: era is one file for 27 years and cannot be capped per year without being
#: re-harvested.
_PER_YEAR = re.compile(r"^gdelt-(?P<pack>[a-z0-9_]+)-(?P<year>\d{4})\.tsv\.gz$")


def _mentions(line: str) -> int:
    fields = line.split("\t", gdelt._MENTIONS + 1)
    if len(fields) <= gdelt._MENTIONS:
        return 0
    try:
        return int(fields[gdelt._MENTIONS])
    except ValueError:
        return 0


def survey(directory: Path) -> dict[str, dict[int, int]]:
    """pack → year → events currently held."""
    counts: dict[str, dict[int, int]] = defaultdict(dict)
    for path in sorted(directory.glob("gdelt-*.tsv.gz")):
        match = _PER_YEAR.match(path.name)
        if not match:
            continue
        with gzip.open(path, "rt", encoding="latin-1") as fh:
            counts[match["pack"]][int(match["year"])] = sum(1 for _ in fh)
    return counts


def trim(path: Path, cap: int, *, keep_full: bool = True) -> tuple[int, int]:
    """Keep this artifact's `cap` best-reported events. Returns (before, after).

    Ties at the cap boundary are broken by the event id, so two runs over the
    same artifact produce the same file — a trim that shuffled its own output
    would make the graph's contents depend on when it was run.
    """
    with gzip.open(path, "rt", encoding="latin-1") as fh:
        lines = fh.readlines()
    before = len(lines)
    if before <= cap:
        return before, before
    if keep_full:
        full = path.with_suffix("").with_suffix(".full.tsv.gz")
        if not full.exists():
            shutil.copyfile(path, full)
    ranked = sorted(lines, key=lambda line: (-_mentions(line), line.split("\t", 1)[0]))
    kept = ranked[:cap]
    # Written back in the archive's own order, not ranked order: the loader
    # feeds the escalation classifier, which is relational and expects time
    # order — handing it a mention-ranked stream would scramble every dyad's
    # baseline.
    kept_set = set(kept)
    partial = path.with_suffix(".gz.partial")
    with gzip.open(partial, "wt", encoding="latin-1") as out:
        for line in lines:
            if line in kept_set:
                out.write(line)
                kept_set.discard(line)
    partial.rename(path)
    return before, cap


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="data/derived")
    parser.add_argument("--cap", type=int, help="events to keep per pack-year")
    parser.add_argument("--apply", action="store_true", help="rewrite the artifacts")
    parser.add_argument("--report", action="store_true", help="show the skew and exit")
    args = parser.parse_args()

    directory = Path(args.dir)
    counts = survey(directory)
    if not counts:
        print(f"no per-year artifacts in {directory}")
        return 1

    print(f"{'pack':<10} {'years':>6} {'total':>10} {'min/yr':>8} {'max/yr':>8} {'skew':>7}")
    for pack, years in sorted(counts.items()):
        values = [v for v in years.values() if v]
        if not values:
            continue
        low, high = min(values), max(values)
        print(f"{pack:<10} {len(years):>6} {sum(years.values()):>10,} "
              f"{low:>8,} {high:>8,} {high / max(low, 1):>6.1f}x")

    if args.report or not args.cap:
        print("\nper-year detail:")
        for pack, years in sorted(counts.items()):
            row = "  ".join(f"{y}:{n:,}" for y, n in sorted(years.items()))
            print(f"  {pack}: {row}")
        if not args.cap:
            print("\nno --cap given; nothing changed")
        return 0

    if not args.apply:
        print(f"\n--cap {args.cap} would keep:")
        for pack, years in sorted(counts.items()):
            after = sum(min(n, args.cap) for n in years.values())
            print(f"  {pack:<10} {sum(years.values()):>9,} -> {after:>9,}")
        print("\nre-run with --apply to rewrite")
        return 0

    total_before = total_after = 0
    for path in sorted(directory.glob("gdelt-*.tsv.gz")):
        if not _PER_YEAR.match(path.name):
            continue
        before, after = trim(path, args.cap)
        total_before += before
        total_after += after
        if before != after:
            print(f"  {path.name}: {before:,} -> {after:,}")
    print(f"\n{total_before:,} -> {total_after:,} events across the per-year artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
