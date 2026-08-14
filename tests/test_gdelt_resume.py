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


def test_the_rescore_skips_when_every_event_is_scored(monkeypatch):
    # THE STEP THAT KEEPS A ROUTINE DEPLOY FAST. A frontend or model push adds
    # no events, so Head B has nothing outstanding — and the rescore is hours
    # on a large archive and cannot be resumed.
    monkeypatch.setenv("GEOGRAPH_RESCORE_ON_BOOT", "1")
    monkeypatch.setattr(boot, "_unscored_events", lambda: 0)
    result = boot._rescore_if_new_events(["mena"])
    assert result["ok"] and "every event is scored" in result["skipped"]


def test_an_interrupted_rescore_is_retried_rather_than_skipped_forever(monkeypatch):
    # THE REASON THE TRIGGER IS "UNSCORED EVENTS" AND NOT "DID THE ARCHIVE
    # GROW". A count-based gate skips any boot that loaded nothing — so a
    # rescore killed by its timeout would never run again, and every
    # backfilled event would stay permanently unscored. Asking the condition
    # directly retries until it converges.
    monkeypatch.setenv("GEOGRAPH_RESCORE_ON_BOOT", "1")
    # Opting in is not enough on its own: the rescore needs _RESCORE_MIN_SECONDS
    # to be worth starting, which no longer fits the default 1800s window. A
    # rescoring boot raises the window too, and this test is that boot.
    monkeypatch.setattr(boot, "_WINDOW_SECONDS", 14400)
    monkeypatch.setattr(boot, "_unscored_events", lambda: 350_000)
    seen: dict[str, bool] = {}

    def record(*_args: object, **_kwargs: object) -> dict[str, object]:
        seen["ran"] = True
        return {"ok": True}

    monkeypatch.setattr(boot, "_run_step", record)
    monkeypatch.setattr(boot, "_graph_gdelt_count", lambda name: 10)
    monkeypatch.setattr(boot, "_DERIVED_DIR", _ROOT / "nonexistent")
    boot._rescore_if_new_events(["mena"])
    assert seen.get("ran"), "an outstanding rescore must be attempted again"


def test_the_boot_window_matches_railway_json():
    # The two must not drift. boot.py budgets against _WINDOW_SECONDS; Railway
    # kills the container at healthcheckTimeout. If they disagree, every budget
    # in the file is computed against a deadline that is not the real one.
    import json

    window = json.loads((_ROOT / "railway.json").read_text(encoding="utf-8"))
    assert window["deploy"]["healthcheckTimeout"] == boot._WINDOW_SECONDS


def test_budgets_are_read_off_the_clock_not_predicted():
    # THREE deploys died on hand-arithmetic over fixed budgets — 138 attempts,
    # then 209. The second mistake was counting the study once when its ceiling
    # is paid PER PACK, so three regions spent 4,500s against a budget of
    # 1,500. The cure is not better arithmetic, it is not doing arithmetic: a
    # step asks the clock what is left. A fixed slice cannot be checked here
    # because it encodes a PREDICTION of what ran before it, and the prediction
    # is the thing that keeps being wrong.
    source = (_ROOT / "scripts" / "boot.py").read_text(encoding="utf-8")
    for step in ("_load_gdelt", "_run_study", "_rescore_if_new_events"):
        body = source[source.index(f"def {step}("):]
        body = body[: body.index(chr(10) + "def ", 1)]
        assert "_remaining(" in body, f"{step} budgets without reading the clock"
    # And the two that iterate packs must still stop when the shared pool is
    # spent, or a fourth lens breaks a boot that fits with three. The study's
    # guard is a FLOOR rather than `<= 0`: with a shared budget the last pack
    # inherits the rounding, and a one-second slice ran, timed out, and
    # reported the whole step failed. Both forms are a stop; neither is a
    # prediction of what earlier steps cost.
    for step, guard in (("_load_gdelt", "budget <= 0"),
                        ("_run_study", "budget < _STUDY_MIN_SECONDS")):
        body = source[source.index(f"def {step}("):]
        body = body[: body.index(chr(10) + "def ", 1)]
        assert guard in body, f"{step} does not stop when its budget is spent"


def test_remaining_never_promises_more_window_than_exists():
    # _remaining is the primitive every budget is built on, so its floor and
    # ceiling are the whole safety argument.
    assert boot._remaining(0) <= boot._WINDOW_SECONDS
    assert boot._remaining(boot._WINDOW_SECONDS * 2) == 0.0, "must floor at zero"
    # Reserving more leaves less. Stated because an inverted sign here would
    # hand the study MORE time the later it starts.
    assert boot._remaining(1000) < boot._remaining(0)


def test_the_expensive_steps_reserve_room_for_what_follows_them():
    # GDELT and the rescore run BEFORE the study, so they must leave it a
    # working window; the study runs last and takes the remainder. If these
    # reserves were equal, the study would routinely start with nothing.
    early = boot._TAIL_RESERVE_SECONDS + boot._STUDY_MIN_SECONDS
    assert early > boot._TAIL_RESERVE_SECONDS
    # The rescore cannot be resumed, so starting it without room to finish
    # wastes the whole pass — it must decline rather than be cut off.
    assert boot._RESCORE_MIN_SECONDS > boot._STUDY_MIN_SECONDS


def _fake_artifacts(tmp_path, pack: str, years: tuple[str, ...]):
    files = []
    for year in years:
        path = tmp_path / f"gdelt-{pack}-{year}.tsv.gz"
        path.write_bytes(b"")
        files.append(path)
    return sorted(files)


def test_completeness_is_per_artifact_not_a_count(tmp_path, monkeypatch):
    # THE 2026-08-14 BUG. Completeness compared the graph's per-lens event
    # count against the distinct ids in that lens's artifacts, which looks
    # airtight and is not: region_pack is a property of the NODE, and since
    # external_powers moved onto the pack every lens harvests the USA/RUS
    # dyads. The same wire events are written once and carry whichever lens
    # merged them first, so eurasia read 512,806/534,534 and mena
    # 432,803/454,531 — short by an IDENTICAL 21,728. Completeness was
    # unreachable for every lens but the alphabetically-first one, so the boot
    # re-merged a finished archive every time and the rescore NEVER RAN.
    monkeypatch.setattr(boot, "_loaded_dir", lambda: tmp_path / "marks")
    artifacts = _fake_artifacts(tmp_path, "mena", ("2024", "2025", "2026"))

    assert boot._pending_artifacts("mena", artifacts) == artifacts
    for artifact in artifacts:
        boot._mark_artifact_loaded("mena", artifact)
    # Complete — and note the graph count was never consulted to decide it.
    assert boot._pending_artifacts("mena", artifacts) == []


def test_a_failed_artifact_stays_pending(tmp_path, monkeypatch):
    # The resumability the count was supposed to provide and could not: a
    # timed-out artifact must be retried by the next boot, and only it.
    monkeypatch.setattr(boot, "_loaded_dir", lambda: tmp_path / "marks")
    artifacts = _fake_artifacts(tmp_path, "mena", ("2024", "2025", "2026"))
    boot._mark_artifact_loaded("mena", artifacts[0])
    boot._mark_artifact_loaded("mena", artifacts[2])
    assert boot._pending_artifacts("mena", artifacts) == [artifacts[1]]


def test_markers_do_not_survive_the_graph_they_describe(tmp_path, monkeypatch):
    # A marker is a claim ABOUT a graph. If the volume was rebuilt the graph is
    # empty and the claim is false, and trusting it would skip a load that has
    # to happen — the same silent-shortfall failure in a new costume.
    monkeypatch.setenv("GEOGRAPH_GDELT_ON_BOOT", "1")
    monkeypatch.setattr(boot, "_loaded_dir", lambda: tmp_path / "marks")
    monkeypatch.setattr(boot, "_DERIVED_DIR", tmp_path)
    monkeypatch.setattr(boot, "_graph_gdelt_count", lambda name: 0)
    monkeypatch.setattr(boot, "_expected_events", lambda artifacts: 100)
    artifacts = _fake_artifacts(tmp_path, "mena", ("2024", "2025"))
    for artifact in artifacts:
        boot._mark_artifact_loaded("mena", artifact)

    ran: list[str] = []

    def record(label, *_args, **_kwargs):
        ran.append(label)
        return {"ok": True, "step": label}

    monkeypatch.setattr(boot, "_run_step", record)
    boot._load_gdelt(["mena"])
    assert len(ran) == 2, "an empty graph must invalidate its markers and reload"


def test_the_rescore_waits_on_artifacts_not_on_a_count(tmp_path, monkeypatch):
    # The other half of the same bug: the rescore's completeness gate used the
    # same unreachable comparison, so it deferred on every boot forever.
    monkeypatch.setenv("GEOGRAPH_RESCORE_ON_BOOT", "1")
    monkeypatch.setattr(boot, "_WINDOW_SECONDS", 14400)
    monkeypatch.setattr(boot, "_loaded_dir", lambda: tmp_path / "marks")
    monkeypatch.setattr(boot, "_DERIVED_DIR", tmp_path)
    monkeypatch.setattr(boot, "_unscored_events", lambda: 350_000)
    # A count far short of the artifacts' ids — the condition that used to
    # block the rescore permanently.
    monkeypatch.setattr(boot, "_graph_gdelt_count", lambda name: 1)
    artifacts = _fake_artifacts(tmp_path, "mena", ("2024", "2025"))
    for artifact in artifacts:
        boot._mark_artifact_loaded("mena", artifact)

    ran: list[str] = []

    def record(label, *_args, **_kwargs):
        ran.append(label)
        return {"ok": True, "step": label}

    monkeypatch.setattr(boot, "_run_step", record)
    boot._rescore_if_new_events(["mena"])
    assert ran, "every artifact loaded — the rescore must run despite the count"


def test_the_graph_reset_is_opt_in_and_off_by_default(tmp_path, monkeypatch):
    # A destructive step that runs by accident throws away the archive on every
    # boot. Absence of the variable must mean "do nothing", and so must every
    # value that is not an explicit yes.
    monkeypatch.delenv("GEOGRAPH_RESET_GRAPH", raising=False)
    assert boot._reset_graph_if_asked() is None
    for value in ("", "0", "false", "no", "maybe"):
        monkeypatch.setenv("GEOGRAPH_RESET_GRAPH", value)
        assert boot._reset_graph_if_asked() is None, f"{value!r} must not delete"


def test_the_graph_reset_removes_the_database_and_its_markers(tmp_path, monkeypatch):
    # Kuzu has no VACUUM: space from rewritten rows is never reclaimed, so a
    # rebuild IS the compaction step. Safe because nothing on the volume is an
    # original — packs, deep tier and GDELT artifacts ship in the image, the
    # panel is in Postgres, and every measured or frozen node is computed from
    # those. The markers must go with it, or the next step skips the reload.
    db = tmp_path / "geograph.kuzu"
    db.mkdir()
    (db / "data.kz").write_bytes(b"x" * 4096)
    (tmp_path / "geograph.kuzu.wal").write_bytes(b"y" * 1024)

    class _Settings:
        kuzu_db_path = db

    monkeypatch.setenv("GEOGRAPH_RESET_GRAPH", "1")
    monkeypatch.setattr(boot, "_loaded_dir", lambda: tmp_path / "marks")
    monkeypatch.setattr(boot, "_pack_names", lambda: ["mena"])
    marker = boot._loaded_dir() / "mena-gdelt-mena-2024.tsv.done"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("x", encoding="utf-8")

    import core.settings as settings_module
    monkeypatch.setattr(settings_module, "load", lambda: _Settings())

    result = boot._reset_graph_if_asked()
    assert result is not None and result["ok"]
    assert not db.exists(), "the graph must be gone"
    assert not (tmp_path / "geograph.kuzu.wal").exists(), "the WAL must go too"
    assert not marker.exists(), "markers describe a graph that no longer exists"


def test_the_wire_load_is_off_by_default(monkeypatch):
    # THE FOUR-HOUR BOOT OF 2026-08-13. Merging 1.33M wire events into a
    # single-writer store happens with the API unable to bind, so it is not
    # slow startup, it is downtime. Loading is opt-in; serving is not.
    monkeypatch.delenv("GEOGRAPH_GDELT_ON_BOOT", raising=False)
    result = boot._load_gdelt(["mena"])
    assert result["ok"] and "GEOGRAPH_GDELT_ON_BOOT" in result["skipped"]


def test_the_archive_rescore_is_off_by_default(monkeypatch):
    # Same rule, stronger case: this one cannot be resumed, so it can neither
    # be metered nor safely cut short. A step that is all-or-nothing at the
    # scale of hours must never stand between a container and its health check.
    monkeypatch.delenv("GEOGRAPH_RESCORE_ON_BOOT", raising=False)
    result = boot._rescore_if_new_events(["mena"])
    assert result["ok"] and "GEOGRAPH_RESCORE_ON_BOOT" in result["skipped"]


def test_opting_into_the_rescore_still_declines_inside_the_serving_window(monkeypatch):
    # The two settings are COUPLED, and the coupling should be discovered here
    # rather than in production. _RESCORE_MIN_SECONDS is larger than the whole
    # 1800s serving window, so opting in without also raising the window
    # declines and SAYS SO — it never half-runs a pass that cannot be resumed.
    monkeypatch.setenv("GEOGRAPH_RESCORE_ON_BOOT", "1")
    monkeypatch.setattr(boot, "_unscored_events", lambda: 350_000)
    monkeypatch.setattr(boot, "_DERIVED_DIR", _ROOT / "nonexistent")
    monkeypatch.setattr(boot, "_run_step", _must_not_run)
    result = boot._rescore_if_new_events(["mena"])
    assert result["ok"] and result["skipped"] == "not enough window"


def _must_not_run(*_args: object, **_kwargs: object) -> dict[str, object]:
    raise AssertionError("an un-resumable step must not start without its window")


def test_a_deep_but_stale_panel_refreshes_its_recent_window(monkeypatch):
    # DEPTH IS ONLY HALF THE GUARD. On 2026-08-14 the panel reached 1871 so
    # the boot skipped loading forever, while its newest close stayed frozen
    # at the last full fetch — and the forward paper book, entering after that
    # date, had nothing to mark: every position skipped, the page read $0.
    # Freshness serves the BOOK the way depth serves the SPINE.
    import datetime as dt

    monkeypatch.setattr(boot, "_panel_first_observation", lambda: (True, "1871-01-01"))
    monkeypatch.setattr(boot, "_spine_needs_from", lambda names: "1937-06-14")
    stale = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    monkeypatch.setattr(boot, "_panel_latest_observation", lambda: (True, stale))

    ran: list[list[str]] = []

    def record(label, cmd, timeout=None, **_kwargs):
        ran.append(cmd)
        return {"ok": True, "step": label}

    monkeypatch.setattr(boot, "_run_step", record)
    result = boot._load_panel_if_shallow(["mena"])
    assert result is not None and result["ok"]
    assert ran, "a stale panel must refresh"
    assert "--start" in ran[0], "the refresh is windowed, not a full-history reload"

    # And a CURRENT panel must not refetch — a refresh that fires every boot
    # for no reason is the noise this guard's slack exists to prevent.
    fresh = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    monkeypatch.setattr(boot, "_panel_latest_observation", lambda: (True, fresh))
    ran.clear()
    result = boot._load_panel_if_shallow(["mena"])
    assert result is not None and "current to" in str(result.get("skipped"))
    assert not ran


def test_the_measuring_steps_are_opt_in_so_the_graph_opens_fast(monkeypatch):
    # THE 2026-08-14 OUTAGE. API-first binds the port in ~20s, but the graph
    # endpoints stay 503 until the background boot thread finishes — Kuzu is one
    # writer OR many readers across processes, so the API opens its connection
    # only after the last write-child exits. The study/forecasts/backtest are
    # write-children that re-derive data ALREADY on the volume, and the study
    # never converged inside its budget, so it burned ~600s of graph-dark time
    # on every single deploy. They are now opt-in: a routine boot skips them and
    # opens the graph seconds after the seed. Each must decline by DEFAULT,
    # naming the variable that turns it back on for a measuring deploy.
    for var, step in (
        ("GEOGRAPH_STUDY_ON_BOOT", lambda: boot._run_study(["mena"])),
        ("GEOGRAPH_FORECASTS_ON_BOOT", boot._freeze_forecasts),
        ("GEOGRAPH_BACKTEST_ON_BOOT", boot._run_backtest),
    ):
        monkeypatch.delenv(var, raising=False)
        result = step()
        assert result is not None
        assert result["ok"] and var in result["skipped"], (
            f"{var} must be opt-in (default off) so the graph opens fast"
        )


def test_the_paper_backtest_is_declinable_and_opt_in(monkeypatch):
    # The gate MOVED twice, on evidence: banished from the boot when each cutoff
    # cost a full 1.31M-row pass (the first corpus boot burned its whole 900s
    # ceiling), readmitted when AsofArchive made the ~425 cutoffs cost ~2s, then
    # made opt-in again on 2026-08-14 — not for its compute (that is cheap now)
    # but because it runs before the API opens the graph, so even ~40s lands in
    # the graph-dark window. An explicit opt-out is still honoured, and so is
    # the new default: off unless a measuring deploy asks for it.
    monkeypatch.setenv("GEOGRAPH_BACKTEST_ON_BOOT", "0")
    assert "GEOGRAPH_BACKTEST_ON_BOOT" in boot._run_backtest()["skipped"]
    monkeypatch.setenv("GEOGRAPH_BACKTEST_ON_BOOT", "1")
    ran: list[str] = []
    monkeypatch.setattr(boot, "_run_step",
                        lambda label, *a, **k: (ran.append(label), {"ok": True, "step": label})[1])
    boot._run_backtest()
    assert ran, "GEOGRAPH_BACKTEST_ON_BOOT=1 must run the walk"


def test_the_study_takes_a_bounded_slice_not_the_remainder(monkeypatch):
    # It used to take every second the window had left, which optimises for
    # progress per boot. The watermark makes progress fungible across boots and
    # downtime is not, so the budget — not the remainder — is what binds.
    # (Opt in explicitly: the study is off by default since 2026-08-14.)
    monkeypatch.setenv("GEOGRAPH_STUDY_ON_BOOT", "1")
    monkeypatch.setattr(boot, "_panel_is_empty", lambda: False)
    monkeypatch.setattr(boot, "_STUDY_BUDGET_SECONDS", 150)
    monkeypatch.setattr(boot, "_STUDY_TIMEOUT_SECONDS", 300)
    handed: list[int] = []

    def record(label, _cmd, timeout=None, **_kwargs):
        handed.append(timeout)
        return {"ok": True, "step": label}

    monkeypatch.setattr(boot, "_run_step", record)
    boot._run_study(["mena", "china", "eurasia"])
    assert handed, "the study must still run"
    assert max(handed) <= 150, "the total budget must bind before the per-pack ceiling"


def test_the_graph_reset_fires_once_per_value(tmp_path, monkeypatch):
    # THE STICKY-VARIABLE BUG. An env var survives every restart, so "set it for
    # one deploy and unset it after" is a note, not a safeguard — and with
    # restartPolicyMaxRetries an unrelated crash re-wipes a rebuilt archive.
    # The boot now records what it acted on, and a value it has seen is inert.
    db = tmp_path / "geograph.kuzu"
    db.mkdir()
    (db / "data.kz").write_bytes(b"x" * 4096)

    class _Settings:
        kuzu_db_path = db

    monkeypatch.setenv("GEOGRAPH_RESET_GRAPH", "1")
    monkeypatch.setattr(boot, "_loaded_dir", lambda: tmp_path / "marks")
    monkeypatch.setattr(boot, "_pack_names", lambda: ["mena"])

    import core.settings as settings_module
    monkeypatch.setattr(settings_module, "load", lambda: _Settings())

    first = boot._reset_graph_if_asked()
    assert first is not None and first["ok"], "the first request must be honoured"
    assert not db.exists(), "the graph must be gone"

    # The variable is STILL SET, exactly as it was left on 2026-08-13.
    db.mkdir()
    (db / "data.kz").write_bytes(b"z" * 4096)
    second = boot._reset_graph_if_asked()
    assert second is not None and second["skipped"] == "already honoured"
    assert db.exists(), "a value already acted on must never delete a second time"


def test_the_deep_tier_defers_its_rescore_to_the_boot():
    # It used to rescore unconditionally on EVERY boot — 643s against a 267k
    # archive, and past its own timeout on a 1.5M one, for no benefit.
    source = (_ROOT / "scripts" / "boot.py").read_text(encoding="utf-8")
    assert '_DEEP_TIER_SCRIPT), "--skip-rescore"' in source
    loader = (_ROOT / "scripts" / "load_deep_tier.py").read_text(encoding="utf-8")
    assert '"--skip-rescore"' in loader


def test_fingerprint_guards_skip_only_matching_complete_runs(monkeypatch, tmp_path):
    # The guard's contract: a matching fingerprint skips in ms; a run that
    # completes records the POST-run fingerprint (self-modifying steps like
    # the freeze would otherwise never match again); a partial run records
    # nothing so the next boot retries.
    monkeypatch.setenv("KUZU_DB_PATH", str(tmp_path / "g.kuzu"))
    monkeypatch.delenv("GEOGRAPH_SKIP_GUARDS", raising=False)
    calls = []

    def runner():
        calls.append(1)
        return {"ok": True}

    fp = {"value": "a"}
    first = boot._guarded("teststep", lambda: fp["value"], runner)
    assert first == {"ok": True} and len(calls) == 1
    second = boot._guarded("teststep", lambda: fp["value"], runner)
    assert second is not None and "skipped" in second and len(calls) == 1

    fp["value"] = "b"
    third = boot._guarded("teststep", lambda: fp["value"], runner)
    assert third == {"ok": True} and len(calls) == 2

    # An incomplete run must not record.
    fp["value"] = "c"
    partial = boot._guarded(
        "teststep", lambda: fp["value"],
        lambda: {"ok": False, "error": "boom"},
    )
    assert partial is not None and not partial["ok"]
    retried = boot._guarded("teststep", lambda: fp["value"], runner)
    assert retried == {"ok": True} and len(calls) == 3

    # GEOGRAPH_SKIP_GUARDS=0 disables the whole mechanism.
    monkeypatch.setenv("GEOGRAPH_SKIP_GUARDS", "0")
    boot._guarded("teststep", lambda: fp["value"], runner)
    assert len(calls) == 4
