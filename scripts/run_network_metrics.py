"""Compute NetworkMetric windows: decades plus regime spans (build-spec §12).

  python scripts/run_network_metrics.py            # every standard window
  python scripts/run_network_metrics.py --window 2021-01-01 2026-12-31

Deterministic: same graph in, same numbers out, and every metric node's id
embeds its window so re-running MERGEs onto itself. Stop the API first —
writing NetworkMetric needs the Kuzu write lock, and Kuzu is single-writer.

The standard windows are the DECADES the explorer's slider walks plus the
regime spans analogy conditions on — the two framings build-spec §12 names.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from core import settings as settings_module
from core.graph import analytics
from core.reasoning import regimes

_ARCHIVE_START = 1905


def standard_windows(today: dt.date | None = None) -> list[analytics.Window]:
    now = today or dt.date.today()
    windows = [
        analytics.Window(f"{year}-01-01", f"{year + 9}-12-31")
        for year in range(_ARCHIVE_START, now.year + 1, 10)
    ]
    for entries in regimes.segmentation().values():
        for entry in entries:
            # The open regime's window grows with the calendar year — that is
            # the correct semantics, not drift: the regime is still running.
            end = entry["end"] or f"{now.year}-12-31"
            windows.append(analytics.Window(entry["start"], end))
    return windows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", nargs=2, metavar=("START", "END"),
                        help="one explicit window instead of the standard set")
    args = parser.parse_args()

    settings = settings_module.load()
    if not settings.kuzu_db_path.exists():
        sys.exit(f"no graph at {settings.kuzu_db_path} — seed first")

    windows = (
        [analytics.Window(*args.window)] if args.window else standard_windows()
    )
    total = 0
    for window in windows:
        written = analytics.compute_metrics(settings.kuzu_db_path, window)
        total += written
        print(f"{window.start}..{window.end}: {written} metric(s)")
    print(f"total: {total}")


if __name__ == "__main__":
    main()
