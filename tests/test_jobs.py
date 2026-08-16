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
