"""The convergence loop: bounded, resumable, and unable to take the API down.

The scheduler exists because every heavy job used to run in a boot, and a boot
is downtime here (one volume, one instance, stop-then-start). Moving that work
inside the running API is only safe if three properties hold, so they are the
three things pinned: a tick is BOUNDED, a failure BACKS OFF instead of
retrying hot, and a job's connection cannot close the database the API is
serving from.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from core.api import jobs as jobs_module


class _App:
    """Just enough of FastAPI's app.state for the scheduler."""

    class state:  # noqa: N801 - mirrors app.state's shape
        graph = object()


def _job(name: str, run: Any, every: float = 0.0) -> jobs_module.Job:
    return jobs_module.Job(name=name, every=every, run=run)


def test_a_job_records_what_it_did_and_when_it_is_next_due():
    calls: list[float] = []

    def run(conn: Any, deadline: float) -> dict[str, Any]:
        calls.append(deadline)
        return {"measured": 7}

    scheduler = jobs_module.Scheduler(_App(), None, [_job("probe", run, every=900.0)])
    scheduler.conn = object()
    job = scheduler.jobs[0]
    scheduler._run(job)

    assert job.state.runs == 1 and job.state.failures == 0
    assert job.state.last_result == {"measured": 7}
    # A DEADLINE, always: the scheduler hands one in rather than trusting a job
    # to stop, because a job that runs "until done" holds the writer while the
    # API serves requests off the same database.
    assert calls[0] > time.monotonic()
    assert calls[0] <= time.monotonic() + job.slice_seconds + 1
    # And the cadence is honoured, not busy-looped.
    assert job.state.next_due > time.monotonic() + 800

    status = scheduler.status()["jobs"][0]
    assert status["name"] == "probe" and status["runs"] == 1
    assert status["last_result"] == {"measured": 7}


def test_a_failing_job_backs_off_rather_than_retrying_hot():
    def run(conn: Any, deadline: float) -> dict[str, Any]:
        raise RuntimeError("postgres went away")

    scheduler = jobs_module.Scheduler(_App(), None, [_job("probe", run, every=1.0)])
    scheduler.conn = object()
    job = scheduler.jobs[0]
    scheduler._run(job)

    assert job.state.failures == 1 and job.state.runs == 0
    assert "postgres went away" in (job.state.last_error or "")
    # Ten minutes, not one second: a job failing against the volume or the
    # panel must not become a busy loop behind a live API.
    assert job.state.next_due > time.monotonic() + 500
    # The API is unharmed and says so.
    assert scheduler.status()["running"] is False  # never started, only stepped


def test_a_disabled_job_is_visible_rather_than_absent():
    scheduler = jobs_module.Scheduler(_App(), None, [
        jobs_module.Job(name="off", every=60.0, run=lambda c, d: {}, enabled=False),
    ])
    row = scheduler.status()["jobs"][0]
    assert row["enabled"] is False and row["runs"] == 0


def test_a_job_connection_cannot_close_the_apis_database(tmp_path):
    # `close()` shuts the connection AND its database. A sibling is deliberately
    # not stamped with the database, so a job dropping or closing its own
    # connection can never take the graph out from under the API.
    from core.graph import kuzu_store

    owner = kuzu_store.connect(tmp_path / "probe.kuzu")
    try:
        twin = kuzu_store.sibling(owner)
        assert getattr(twin, "_geograph_db", None) is None
        kuzu_store.close(twin)  # a no-op on the database
        # The owner still works.
        kuzu_store.query(owner, "RETURN 1 AS n")
    finally:
        kuzu_store.close(owner)


def test_the_measure_loop_stops_at_its_deadline_and_says_what_is_left():
    # The bound that makes background measuring safe. `runner.measure` is the
    # same function the CLI calls; the only difference in a job is that a
    # deadline is passed, and the per-event watermark makes stopping free.
    from core.transmission import runner

    class _Pack:
        name = "probe"
        markets: list[dict[str, Any]] = []

    chosen = [{"id": f"event:{i}", "date": "2020-01-01", "name": "x"} for i in range(5)]
    import datetime as dt

    all_dates = {e["id"]: dt.date(2020, 1, 1) for e in chosen}
    outcome = runner.measure(
        None, None, _Pack(), chosen, all_dates=all_dates, dry_run=True,
        deadline=time.monotonic() - 1.0,  # already past
    )
    assert outcome["events"] == 0
    assert outcome["stopped_early"] is True
    assert outcome["remaining"] == 5


# ── the lock that makes writing-while-serving possible ──────────────────────


def test_a_write_excludes_readers_and_is_never_starved_by_them():
    """THE FAILURE THAT TOOK THE STUDY JOB DOWN IN PRODUCTION.

    Kuzu checkpoints after a write, and a checkpoint needs no transaction
    active anywhere in the process. Request threads read continuously, so the
    checkpoint waited and the write failed — as "Timeout waiting for active
    transactions to leave the system" in a local reproduction and as an
    internal assertion in the rel-table storage (csr_node_group.cpp
    KU_UNREACHABLE) in production, on the first AFFECTED merge.

    Two properties fix it, and the second was learned the hard way: readers
    must exclude writers, AND a waiting writer must exclude new readers.
    Without the second, three reader threads in a loop hand the lock to each
    other, the reader count never reaches zero, and the writer hangs forever —
    which is exactly what the API's traffic looks like.
    """
    import threading

    from core.graph import kuzu_store

    lock = kuzu_store._ReadWriteLock()
    observed: list[str] = []
    stop = threading.Event()
    started = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            with lock.read():
                observed.append("r")
                started.set()
                time.sleep(0.001)

    threads = [threading.Thread(target=reader, daemon=True) for _ in range(3)]
    for thread in threads:
        thread.start()
    assert started.wait(timeout=5), "readers never started"

    # The writer must get in despite continuous reader traffic.
    acquired = threading.Event()

    def writer() -> None:
        with lock.write():
            observed.append("W-start")
            time.sleep(0.05)
            observed.append("W-end")
            acquired.set()

    write_thread = threading.Thread(target=writer, daemon=True)
    write_thread.start()
    assert acquired.wait(timeout=5), "the writer was starved by continuous readers"
    stop.set()
    for thread in threads:
        thread.join(timeout=2)
    write_thread.join(timeout=2)

    # And nothing read while it wrote: the whole point of the exclusion.
    start = observed.index("W-start")
    end = observed.index("W-end")
    assert end == start + 1, f"a read landed inside the write: {observed[start:end + 1]}"


def test_every_graph_write_in_the_codebase_takes_the_lock():
    # The lock only works because EVERY access goes through kuzu_store's three
    # functions — the repo's "one write path" discipline is what makes a
    # process-wide lock enforceable at all. A direct conn.execute anywhere else
    # would be a hole in it.
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in list((root / "core").rglob("*.py")) + list((root / "scripts").rglob("*.py")):
        if path.name == "kuzu_store.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ".execute(" in line and "cur.execute" not in line and "cursor" not in line:
                offenders.append(f"{path.relative_to(root)}:{number}")
    assert not offenders, f"graph statements outside kuzu_store: {offenders}"


def test_neither_side_of_the_graph_lock_can_starve_the_other():
    """FIFO, not a preference — and both directions were measured wrong first.

    Readers-only-wait-for-an-active-writer starves the writer (looping readers
    hand the lock to each other and the reader count never reaches zero).
    Writer preference starves the readers (a job writing in a tight loop
    always has a request queued, so new readers wait out the whole slice: a
    20,000-edge write took the median read from 3ms to 10.2s and timed out a
    case study at 30s). The queue is therefore strictly first-come-first-served
    with consecutive readers batched.
    """
    import threading

    from core.graph import kuzu_store

    lock = kuzu_store._ReadWriteLock()
    order: list[str] = []

    held = threading.Event()
    release = threading.Event()

    def first_writer() -> None:
        with lock.write():
            order.append("W1")
            held.set()
            release.wait(timeout=5)

    def late_reader() -> None:
        with lock.read():
            order.append("R")

    def late_writer() -> None:
        with lock.write():
            order.append("W2")

    threads = [threading.Thread(target=first_writer, daemon=True)]
    threads[0].start()
    assert held.wait(timeout=5)
    # Both queue BEHIND the writer in flight, the reader first.
    reader = threading.Thread(target=late_reader, daemon=True)
    reader.start()
    time.sleep(0.05)
    writer = threading.Thread(target=late_writer, daemon=True)
    writer.start()
    time.sleep(0.05)
    release.set()
    for thread in (*threads, reader, writer):
        thread.join(timeout=5)

    # The reader arrived first, so it goes first — a writer in a loop cannot
    # keep jumping the queue, which is what starved every page.
    assert order == ["W1", "R", "W2"], order


def test_every_recurring_job_runs_inside_the_api_rather_than_a_boot():
    """The whole point of the loop: nothing that keeps the platform current
    should need a deploy, because a deploy here is downtime (one volume, one
    instance, stop-then-start).

    Each of these was a boot step. Each needed a connection-taking seam,
    because the API process holds Kuzu's write lock and a second
    `kuzu.Database` inside it fails on that lock.
    """
    import inspect

    from core.api import work

    for name in ("study", "games", "wire", "rescore", "forecasts", "scores",
                 "metrics", "backtest", "calibration"):
        job = getattr(work, name, None)
        assert callable(job), f"{name} is not a job"
        params = list(inspect.signature(job).parameters)
        assert params[:2] == ["conn", "deadline"], (
            f"{name} must take the API's connection and a deadline: {params}"
        )


def test_the_study_is_a_child_and_stops_on_its_own_budget(monkeypatch):
    """THE ONE JOB THAT COULD NOT STAY IN-PROCESS.

    Writing AFFECTED from the API process died inside Kuzu's rel storage
    (`csr_node_group.cpp KU_UNREACHABLE`) on a sibling connection, then on the
    API's own connection, then again after the lock was made fair — three
    production failures, while every other writer in this loop ran for hours.
    The difference is AFFECTED's SHAPE: ~756k edges onto ~20 Market nodes, so
    a merge rewrites a very large CSR group.

    So it runs where those 756k edges were actually written: a child process.
    Two properties make that safe and both are pinned here — the scheduler
    RELEASES the graph around it (Kuzu is one writer per PROCESS, so the child
    cannot take the lock otherwise), and the child gets a BUDGET rather than a
    kill, because a write killed mid-commit is the one way this loop could
    damage the volume.
    """
    import sys
    from pathlib import Path

    from core.api import app as app_module
    from core.api import work
    from core.graph import kuzu_store

    # The API registers it as child-CAPABLE. It runs in-process by default —
    # the child costs ~90s of graph-dark per slice and the in-process path
    # costs nothing — and switches for the life of the process only if Kuzu's
    # storage assertion returns.
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    assert 'name="study"' in source and "child=True" in source
    assert work._STORAGE_ASSERTION == "KU_UNREACHABLE"
    assert not work._PREFER_CHILD, "the child must be the fallback, not the default"

    # The CLI it drives accepts a budget, and the job passes one.
    cli = (Path(app_module.__file__).resolve().parents[2]
           / "scripts" / "run_event_study.py").read_text(encoding="utf-8")
    assert "--budget-seconds" in cli
    assert "--budget-seconds" in Path(work.__file__).read_text(encoding="utf-8")

    class _Graph:
        closed = False

    app = _App()
    app.state.graph = _Graph()
    reopened: list[str] = []
    monkeypatch.setattr(kuzu_store, "close", lambda g: setattr(g, "closed", True))
    def _reopen(a: Any, s: Any) -> None:
        reopened.append("yes")
        a.state.graph = _Graph()

    monkeypatch.setattr(app_module, "_open_graph", _reopen)

    scheduler = jobs_module.Scheduler(app, None, [])
    plan = {"argv": [sys.executable, "-c", "print('measured 3 events')"],
            "budget_seconds": 30.0, "pack": "mena"}
    job = jobs_module.Job(name="study", every=1.0, run=lambda c, d: plan, child=True)
    outcome = scheduler._run_child(job, plan)

    assert outcome["ok"], outcome
    assert "measured 3 events" in " ".join(outcome["tail"])
    assert reopened == ["yes"], "the graph must reopen after the child"
    assert app.state.graph is not None


def test_the_loop_pauses_rather_than_letting_the_container_be_killed(monkeypatch):
    """An OOM kill is not an exception this process gets to catch.

    On 2026-08-16 the kernel took it mid-write and the graph's write-ahead log
    could not be replayed afterwards — every graph endpoint served 503 until
    the recovery landed. Every job in this loop re-derives something already
    persisted, so none of them is worth that. Below a headroom floor the loop
    first drops the caches it can rebuild, and below a lower one it stops
    starting jobs at all — visibly, in `status()`, because a loop that quietly
    stops looks exactly like a loop with nothing to do.
    """
    from core.graph import kuzu_store

    scheduler = jobs_module.Scheduler(_App(), None, [])

    monkeypatch.setattr(kuzu_store, "container_memory_bytes", lambda: 8 << 30)
    monkeypatch.setattr(kuzu_store, "memory_in_use_bytes", lambda: 4 << 30)
    relaxed = scheduler._headroom()
    assert relaxed == 0.5
    assert relaxed > jobs_module.MEMORY_RECLAIM_BELOW

    monkeypatch.setattr(kuzu_store, "memory_in_use_bytes", lambda: int(7.5 * 2**30))
    headroom = scheduler._headroom()
    assert headroom is not None and headroom < jobs_module.MEMORY_PAUSE_BELOW, headroom

    # Reclaim drops the corpus cache — the biggest thing here that rebuilds.
    dropped: list[str] = []
    from core.wire import corpus
    monkeypatch.setattr(corpus, "evict", lambda: dropped.append("corpus"))
    scheduler._reclaim()
    assert dropped == ["corpus"]

    # Unknown limits must not pause the loop: not every host is a container.
    monkeypatch.setattr(kuzu_store, "container_memory_bytes", lambda: None)
    assert scheduler._headroom() is None

    payload = scheduler.status()["memory"]
    assert set(payload) >= {"limit_gb", "used_gb", "headroom",
                            "buffer_pool_gb", "paused_for_memory"}


def test_the_memory_check_runs_before_each_job_not_once_a_pass(monkeypatch):
    """MEASURED IN PRODUCTION, 2026-08-16. Headroom fell to 0.05 while a
    single job ran and the loop recorded no reclaim at all — because a
    once-a-pass check cannot fire while the pass is blocked inside a job, and
    one job's working set is what actually reaches the ceiling.

    So the check is per job. A job it refuses says so in its own last_result
    rather than looking like it simply had nothing to do.
    """
    from core.graph import kuzu_store
    from core.wire import corpus

    monkeypatch.setattr(kuzu_store, "container_memory_bytes", lambda: 8 << 30)
    monkeypatch.setattr(kuzu_store, "memory_in_use_bytes", lambda: int(7.7 * 2**30))
    monkeypatch.setattr(corpus, "evict", lambda: None)

    ran: list[str] = []

    def _heavy(conn: Any, deadline: float) -> dict[str, Any]:
        ran.append("ran")
        return {}

    job = _job("heavy", _heavy)
    scheduler = jobs_module.Scheduler(_App(), None, [job])

    assert not scheduler._memory_allows(job)
    assert scheduler.paused_for_memory
    assert not ran, "a job must not start with the container nearly full"
    assert (job.state.last_result or {})["skipped"] == "paused for memory"
    assert scheduler.memory_reclaims == 1, "it should try reclaiming first"

    monkeypatch.setattr(kuzu_store, "memory_in_use_bytes", lambda: 2 << 30)
    assert scheduler._memory_allows(job)
    assert not scheduler.paused_for_memory


def test_reclaim_drops_the_archive_cache_too():
    """The corpus is not the only rebuildable thing this process holds: the
    study caches ~1.07M lean event rows plus their parsed dates between ticks,
    and rebuilding that is one graph scan."""
    from core.api import work

    work._archive_cache.update({"count": 5, "events": [1, 2], "dates": {"a": 1}})
    work.forget_archive()
    assert work._archive_cache == {"count": None, "events": None, "dates": None}


def test_the_study_falls_back_to_a_child_only_on_the_storage_assertion(monkeypatch):
    """Four write topologies died in `csr_node_group.cpp KU_UNREACHABLE`, and
    the fourth — a child process — is what showed the topology was never the
    variable: the failing statement was the READ inside `MERGE`. AFFECTED now
    writes through `write_edges`, which never asks for that scan.

    So the study is back in-process, and the child is kept as a fallback
    rather than deleted, because it is the configuration that wrote the first
    632,000 edges. What must hold: the switch fires on THAT assertion and
    nothing else, and once flipped it stays flipped rather than failing every
    tick.
    """
    from core.api import work

    monkeypatch.setattr(work, "_PREFER_CHILD", False)

    def _boom(conn, deadline):
        raise RuntimeError(
            'Assertion failed in file ".../csr_node_group.cpp" on line 411: '
            "KU_UNREACHABLE"
        )

    monkeypatch.setattr(work, "_study_in_process", _boom)
    monkeypatch.setattr(work, "_study_child_plan", lambda d: {"argv": ["x"]})
    result = work.study(object(), time.monotonic() + 60)
    assert result["switched_to_child"], result
    assert work._PREFER_CHILD, "the switch must stick for the process's life"
    assert work.study(object(), time.monotonic() + 60) == {"argv": ["x"]}

    # Any OTHER RuntimeError is a real failure and must reach the scheduler's
    # backoff, not be silently converted into a topology change.
    monkeypatch.setattr(work, "_PREFER_CHILD", False)
    monkeypatch.setattr(
        work, "_study_in_process",
        lambda c, d: (_ for _ in ()).throw(RuntimeError("panel is unreachable")),
    )
    with pytest.raises(RuntimeError, match="panel is unreachable"):
        work.study(object(), time.monotonic() + 60)
    assert not work._PREFER_CHILD
