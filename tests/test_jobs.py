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
