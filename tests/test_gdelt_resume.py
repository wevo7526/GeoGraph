"""The GDELT boot load must be RESUMABLE and its completeness CHECKABLE.

Both properties were bought with a production incident (2026-08-12): the
Eurasia artifact's 95,070 events hit the boot step's 900s ceiling partway
through, and the "already loaded?" test was `LIMIT 1` — so the partial load
satisfied it and every later boot would have skipped the remainder, leaving
the lens silently short forever.

These are unit tests on purpose. The property that matters is arithmetic —
which lines get skipped, how batches divide, what count means "complete" —
and pinning that takes milliseconds where loading 95,070 events takes
minutes.
"""

from __future__ import annotations

import gzip
import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relative: str):
    """Import a script by path — scripts/ is not an importable package."""
    spec = importlib.util.spec_from_file_location(name, _ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backfill = _load("backfill_gdelt", "scripts/backfill_gdelt.py")
boot = _load("boot", "scripts/boot.py")


def _line(event_id: str) -> str:
    """An export line only needs field 0 for the freshness filter."""
    fields = [""] * 35
    fields[0] = event_id
    return "\t".join(fields) + "\n"


# ── the resume filter ────────────────────────────────────────────────────────


def test_lines_already_in_the_graph_are_skipped():
    lines = [_line("1"), _line("2"), _line("3")]
    fresh = list(backfill._fresh_lines(lines, {"event:gdelt-1", "event:gdelt-3"}))
    assert len(fresh) == 1
    assert fresh[0].split("\t")[0] == "2"


def test_an_empty_graph_takes_every_line():
    lines = [_line("1"), _line("2")]
    assert list(backfill._fresh_lines(lines, set())) == lines


def test_a_fully_loaded_graph_takes_nothing():
    # The steady state: every later boot does no work at all.
    lines = [_line("1"), _line("2")]
    existing = {"event:gdelt-1", "event:gdelt-2"}
    assert list(backfill._fresh_lines(lines, existing)) == []


def test_the_id_built_here_matches_the_one_the_writer_builds():
    # If these two ever disagree the resume filter silently skips nothing
    # (re-merging everything) or skips everything (loading nothing). Both
    # look like success from the outside, so the agreement is pinned.
    from core.ingestion import gdelt

    roster = {"USA": {"node_id": "actor:cow-2", "name": "United States"},
              "IRN": {"node_id": "actor:cow-630", "name": "Iran"}}
    fields = [""] * 35
    fields[gdelt._GLOBALEVENTID] = "777"
    fields[gdelt._SQLDATE] = "20250101"
    fields[gdelt._A1_COUNTRY] = "USA"
    fields[gdelt._A2_COUNTRY] = "IRN"
    fields[gdelt._IS_ROOT] = "1"
    fields[gdelt._EVENT_CODE] = "190"
    fields[gdelt._QUAD] = "4"
    fields[gdelt._GOLDSTEIN] = "-10.0"
    fields[gdelt._MENTIONS] = "40"
    line = "\t".join(fields)

    events, _, _ = gdelt.parse_lines(
        [line], actors_by_iso3=roster, region_pack="mena",
        external_powers=frozenset(),
    )
    written_id = events[0]["node_id"]
    # The filter must now consider that same line stale.
    assert list(backfill._fresh_lines([line], {written_id})) == []


# ── batching ─────────────────────────────────────────────────────────────────


def test_batches_divide_evenly_and_keep_the_remainder():
    batches = list(backfill._batched([str(i) for i in range(25)], 10))
    assert [len(b) for b in batches] == [10, 10, 5]


def test_an_empty_stream_yields_no_batch():
    assert list(backfill._batched([], 10)) == []


def test_the_batch_size_bounds_what_an_interrupted_load_can_lose():
    # A killed process keeps every batch it committed, so the batch size IS
    # the worst-case loss. Guard against it drifting back toward "all of it".
    assert 0 < backfill._BATCH_LINES <= 25_000


# ── the completeness check ───────────────────────────────────────────────────


def test_the_artifact_count_is_its_line_count(tmp_path):
    artifact = tmp_path / "gdelt-test-1979-2005.tsv.gz"
    with gzip.open(artifact, "wt", encoding="latin-1") as fh:
        for i in range(2_500):
            fh.write(_line(str(i)))
    assert boot._artifact_events(artifact) == 2_500


def test_the_shipped_artifacts_report_their_real_size():
    # The counts the boot compares against are the ones the loads produced:
    # china 66,669 and eurasia 95,070 were the totals those runs reported.
    #
    # Named explicitly rather than globbed-and-last: the modern-era harvest
    # adds one artifact per year beside this span, and 'the last match'
    # silently became gdelt-china-2013 the moment it did. Same assumption
    # that made boot.py load one year and call the lens complete.
    derived = _ROOT / "data" / "derived"
    for name, expected in (("china", 66_669), ("eurasia", 95_070)):
        span = derived / f"gdelt-{name}-1979-2005.tsv.gz"
        if not span.exists():
            continue
        assert boot._artifact_events(span) == expected


def test_the_expected_count_sums_every_artifact_not_just_the_last(tmp_path):
    # The modern-era backfill ships one artifact PER YEAR, because the daily
    # era is thousands of downloads and a harvest that cannot checkpoint
    # between years cannot finish. Taking artifacts[-1] would compare the
    # graph's whole count against one year's — the completeness check would
    # pass on the first boot and the other twenty years would never load,
    # which is the same silent-shortfall this module exists to prevent.
    derived = tmp_path / "derived"
    derived.mkdir()
    sizes = {"1979-2005": 1_000, "2006": 40, "2007": 60, "2026": 25}
    for suffix, count in sizes.items():
        with gzip.open(derived / f"gdelt-mena-{suffix}.tsv.gz", "wt", encoding="latin-1") as fh:
            for i in range(count):
                fh.write(_line(f"{suffix}-{i}"))
    artifacts = sorted(derived.glob("gdelt-mena-*.tsv.gz"))
    assert len(artifacts) == 4
    # Sorting puts the per-year files after the span, so the naive read would
    # have expected 25 and declared a 1,000-event graph complete.
    assert boot._artifact_events(artifacts[-1]) == 25
    assert sum(boot._artifact_events(a) for a in artifacts) == sum(sizes.values())


def test_the_boot_defers_the_rescore_and_runs_it_once():
    # Head B folds escalation per dyad in time order across the WHOLE archive,
    # so a rescore run between artifacts computes every baseline from a partial
    # archive and is then overwritten by the next one. With twenty-one
    # artifacts a lens it is also the difference between a boot that finishes
    # and one that dies on its healthcheck: the rescore rewrites every event.
    source = (_ROOT / "scripts" / "boot.py").read_text(encoding="utf-8")
    assert "--skip-rescore" in source, "per-artifact loads must defer the rescore"
    assert "--rescore-only" in source, "the boot must run the rescore itself, once"
    # And the loader has to support both halves of that contract.
    loader = (_ROOT / "scripts" / "backfill_gdelt.py").read_text(encoding="utf-8")
    assert '"--skip-rescore"' in loader
    assert '"--rescore-only"' in loader


def test_gdelt_gets_a_ceiling_of_its_own_well_above_the_price_fetch():
    # The incident: the load borrowed _LOAD_TIMEOUT_SECONDS (900s, sized for
    # yfinance) and died partway through ~100k merges.
    assert boot._GDELT_TIMEOUT_SECONDS > boot._LOAD_TIMEOUT_SECONDS
    assert boot._GDELT_TIMEOUT_SECONDS >= 2_400


def test_expected_counts_distinct_ids_not_lines(tmp_path):
    # GDELT's own ids recur across year-boundary files, so summing artifact
    # line counts OVERCOUNTS — and the overcount is fatal rather than
    # cosmetic. Measured on the real mena artifacts: 454,539 lines for 454,531
    # distinct events. Against a line total the graph is permanently eight
    # short, the completeness check never passes, every boot re-attempts a
    # finished load, and the rescore that waits on completeness never runs.
    derived = tmp_path / "derived"
    derived.mkdir()
    with gzip.open(derived / "gdelt-x-2014.tsv.gz", "wt", encoding="latin-1") as fh:
        for i in range(10):
            fh.write(_line(str(i)))
    with gzip.open(derived / "gdelt-x-2015.tsv.gz", "wt", encoding="latin-1") as fh:
        for i in range(8, 15):          # 8 and 9 recur, as at a year boundary
            fh.write(_line(str(i)))
    artifacts = sorted(derived.glob("gdelt-x-*.tsv.gz"))
    assert sum(boot._artifact_events(a) for a in artifacts) == 17
    assert boot._expected_events(artifacts) == 15


def test_the_rescore_skips_when_no_events_were_added(monkeypatch):
    # THE STEP THAT KEEPS A ROUTINE DEPLOY FAST. A frontend or model push adds
    # no events, so there is nothing for Head B to recompute — and the rescore
    # is hours on a large archive and cannot be resumed.
    monkeypatch.setattr(boot, "_EVENTS_BEFORE", 1_000)
    monkeypatch.setattr(boot, "_archive_events", lambda: 1_000)
    result = boot._rescore_if_new_events(["mena"])
    assert result["ok"] and "no new events" in result["skipped"]


def test_the_deep_tier_defers_its_rescore_to_the_boot():
    # It used to rescore unconditionally on EVERY boot — 643s against a 267k
    # archive, and past its own timeout on a 1.5M one, for no benefit.
    source = (_ROOT / "scripts" / "boot.py").read_text(encoding="utf-8")
    assert '_DEEP_TIER_SCRIPT), "--skip-rescore"' in source
    loader = (_ROOT / "scripts" / "load_deep_tier.py").read_text(encoding="utf-8")
    assert '"--skip-rescore"' in loader
