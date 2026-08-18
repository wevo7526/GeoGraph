"""The harvest job — the only thing in the loop that learns a new fact.

Twelve jobs kept the platform current and none of them could fetch a new
EVENT: the study measures the corpus, so without a harvest the archive was
frozen at the last commit. These tests pin the two properties that make the
fix safe rather than the download, which is GDELT's business and not testable
offline.

THE FAILURE MODE WORTH THE MOST HERE IS SILENT. If harvested days were
appended to `data/derived`, they would be destroyed by the next deploy — the
image replaces that directory — and nothing would look broken: the files would
still be present, still parse, and simply be missing every day learned since
the build. So the overlay lives on the volume and `corpus.artifacts_for` reads
both, and that is asserted rather than assumed.
"""

from __future__ import annotations

import datetime as dt
import gzip

from core.ingestion import harvest
from core.wire import corpus

# ── which days a tick should fetch ───────────────────────────────────────────


def test_a_cold_volume_starts_after_the_committed_artifacts():
    """The image already holds the history; the harvest owns only what came
    after it. Starting from DAILY_FROM would re-download thirteen years."""
    days = harvest.days_to_harvest(
        through=None, committed_through=dt.date(2026, 8, 12),
        today=dt.date(2026, 8, 17), limit=10,
    )
    assert days == [dt.date(2026, 8, 13), dt.date(2026, 8, 14),
                    dt.date(2026, 8, 15), dt.date(2026, 8, 16)]


def test_it_stops_at_yesterday():
    """GDELT publishes a day's export the FOLLOWING day, so asking for today
    is a guaranteed 404 — and a 404 that really means "not yet" must never be
    recorded as harvested, or that day is skipped forever."""
    days = harvest.days_to_harvest(
        through=dt.date(2026, 8, 14), committed_through=None,
        today=dt.date(2026, 8, 17), limit=10,
    )
    assert days == [dt.date(2026, 8, 15), dt.date(2026, 8, 16)]
    assert dt.date(2026, 8, 17) not in days


def test_the_marker_wins_when_it_is_ahead_of_the_image():
    """The steady state: the volume has been running for weeks past the build."""
    days = harvest.days_to_harvest(
        through=dt.date(2026, 9, 30), committed_through=dt.date(2026, 8, 12),
        today=dt.date(2026, 10, 2), limit=10,
    )
    assert days == [dt.date(2026, 10, 1)]


def test_a_current_archive_asks_for_nothing():
    assert harvest.days_to_harvest(
        through=dt.date(2026, 8, 16), committed_through=dt.date(2026, 8, 12),
        today=dt.date(2026, 8, 17), limit=10,
    ) == []


def test_the_tick_is_bounded():
    """A volume that has been off for a month has thirty days to fetch and
    must not spend one slice on all of them."""
    days = harvest.days_to_harvest(
        through=dt.date(2026, 1, 1), committed_through=None,
        today=dt.date(2026, 8, 17), limit=6,
    )
    assert len(days) == 6
    assert days[0] == dt.date(2026, 1, 2)


# ── the marker ───────────────────────────────────────────────────────────────


def test_the_marker_round_trips(tmp_path):
    assert harvest.harvested_through(tmp_path) is None
    harvest.mark_harvested(tmp_path, dt.date(2026, 8, 15))
    assert harvest.harvested_through(tmp_path) == dt.date(2026, 8, 15)


def test_an_unreadable_marker_is_not_a_permanent_stop(tmp_path):
    """Corrupt means "start from the image's floor", not "never harvest
    again" — the alternative is a one-byte file that silently freezes the
    archive for good."""
    (tmp_path / "harvested-through.txt").write_text("not a date", encoding="utf-8")
    assert harvest.harvested_through(tmp_path) is None


# ── the overlay ──────────────────────────────────────────────────────────────


def test_the_overlay_is_read_beside_the_committed_artifacts(tmp_path, monkeypatch):
    shipped, volume = tmp_path / "derived", tmp_path / "harvest"
    shipped.mkdir()
    volume.mkdir()
    (shipped / "gdelt-mena-2025.tsv.gz").write_bytes(gzip.compress(b""))
    harvest.artifact_for(volume, "mena", 2026).write_bytes(gzip.compress(b""))
    monkeypatch.setenv("GEOGRAPH_DERIVED_DIR", str(shipped))
    monkeypatch.setenv("GEOGRAPH_HARVEST_DIR", str(volume))

    found = [p.name for p in corpus.artifacts_for("mena")]
    assert found == ["gdelt-mena-2025.tsv.gz", "gdelt-mena-2026.harvest.tsv.gz"]


def test_without_a_harvest_dir_nothing_changes(tmp_path, monkeypatch):
    shipped = tmp_path / "derived"
    shipped.mkdir()
    (shipped / "gdelt-mena-2025.tsv.gz").write_bytes(gzip.compress(b""))
    monkeypatch.setenv("GEOGRAPH_DERIVED_DIR", str(shipped))
    monkeypatch.delenv("GEOGRAPH_HARVEST_DIR", raising=False)
    assert [p.name for p in corpus.artifacts_for("mena")] == ["gdelt-mena-2025.tsv.gz"]


def test_the_overlay_name_cannot_be_mistaken_for_a_committed_artifact(tmp_path):
    """`corpus` globs `gdelt-<pack>-*.tsv.gz` in the shipped directory. If a
    harvest file were ever copied in there, that glob must still be able to
    tell them apart — otherwise every harvested row is counted twice."""
    name = harvest.artifact_for(tmp_path, "mena", 2026).name
    assert ".harvest." in name
    assert name.endswith(".tsv.gz")


def test_appending_writes_a_second_gzip_member_that_reads_back(tmp_path):
    """The property the daily append depends on: gzip files concatenate, so a
    day costs a write of its own rows rather than a rewrite of the year."""
    target = harvest.artifact_for(tmp_path, "mena", 2026)
    with gzip.open(target, "at", encoding=harvest.ENCODING) as fh:
        fh.write("first\n")
    with gzip.open(target, "at", encoding=harvest.ENCODING) as fh:
        fh.write("second\n")
    with gzip.open(target, "rt", encoding=harvest.ENCODING) as fh:
        assert fh.read().splitlines() == ["first", "second"]


# ── the job ──────────────────────────────────────────────────────────────────


def test_the_harvest_bar_matches_the_era_it_extends():
    """50, not `corpus.MIN_MENTIONS`. The two answer different questions.

    `corpus.MIN_MENTIONS` (10) is the floor the PARSER applies when re-reading
    an artifact that was already filtered when written, so it never binds.
    This is the bar that decides what a NEW day contains, and every committed
    artifact from 2006 on was built with `--min-mentions 50` — verified: the
    minimum NumMentions in the 2015 and 2026 artifacts is exactly 50.

    Shipped wrong once, at 10, which ran new days ~3x denser than the ones
    before them. Uneven density is this archive's defining hazard; a step
    change on the day the harvest switched on is precisely its shape.
    """
    assert harvest.MIN_MENTIONS == 50
    assert harvest.MIN_MENTIONS > corpus.MIN_MENTIONS


def test_the_job_is_off_unless_a_directory_is_named(monkeypatch):
    from core.api import work

    monkeypatch.setenv("GEOGRAPH_SNAPSHOT_FROZEN", "0")
    monkeypatch.delenv("GEOGRAPH_HARVEST_DIR", raising=False)
    assert "skipped" in work.harvest(None, 0.0)


def test_the_job_takes_no_graph_connection(monkeypatch, tmp_path):
    """It downloads, screens and appends files — nothing else. Passing None
    for the connection is the assertion: if it ever reached for the graph it
    would raise here, and a job that touches Kuzu belongs behind the write
    lock with the others."""
    from core.api import work

    monkeypatch.setenv("GEOGRAPH_SNAPSHOT_FROZEN", "0")
    monkeypatch.setenv("GEOGRAPH_HARVEST_DIR", str(tmp_path))
    harvest.mark_harvested(tmp_path, dt.date.today())
    out = work.harvest(None, 0.0)
    assert "note" in out or "skipped" in out
