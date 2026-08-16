"""The convergence loop: recurring work INSIDE the running API.

WHY THIS EXISTS. Every heavy job this platform does — measuring events against
markets, solving the region games, freezing forecasts — used to run in the
BOOT, because the boot was the only place that could hold Kuzu's write lock.
Kuzu is single-writer per process and a Railway volume mounts to one instance,
so a deploy is stop-then-start and boot time IS downtime. That made every unit
of archive work a downtime decision, and the arithmetic never closed: the study
takes a 600s slice per deploy against a hundred-thousand-event archive, so
production had measured ~10% of the wire and had walked as far as 2003 while
mena's flagship dyads held 351 graph events between them.

The API is already the process holding the write lock. So the work belongs
here, on the API's OWN connection, serialised against request reads by
`kuzu_store.ACCESS`, in bounded slices, forever. A deploy then costs a seed and an open; the archive
converges in the background while the site serves; and turning a job on stops
being a decision about an outage.

THREE RULES, and they are what keep this safe to run under live traffic:

  1. EVERY TICK IS BOUNDED. A job gets a deadline and stops at the next clean
     boundary, flushing what it has. Nothing here may run "until done".
  2. EVERY JOB IS RESUMABLE. The study has its per-event watermark, GDELT has
     its artifact markers, the solves are idempotent replacements. A tick that
     stops early costs the next tick nothing, which is exactly why a bound is
     affordable.
  3. NOTHING HERE ORIGINATES A NUMBER. These are the same functions the
     scripts call, on the same inputs, writing through the same one path
     (`transmission.effects.write_effects` for AFFECTED, and nothing else).

Status is served at `/api/jobs`, because a background process that cannot be
watched is a background process nobody trusts.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

#: Seconds between scheduler wake-ups. Jobs declare their own cadence; this is
#: only how often the loop asks "is anything due".
TICK_SECONDS = 15.0

#: How long a single job tick may hold the writer. Short by design: a request
#: thread reading the graph while a batch commits pays for that batch, so the
#: slice is sized to be invisible rather than efficient.
DEFAULT_SLICE_SECONDS = float(os.getenv("GEOGRAPH_JOB_SLICE", "45"))

#: A job that raises backs off rather than retrying hot — a failing job must
#: not become a busy loop against Postgres or the volume.
FAILURE_BACKOFF_SECONDS = 600.0

#: Rows per write statement WHILE SERVING. The graph lock is FIFO, so a reader
#: waits at most one statement — which makes statement size the p95 read
#: latency of every page while the archive converges. Measured against a
#: 20,000-edge write with a reader polling throughout:
#:
#:     1,000 rows  median 3,505ms  p95 9,260ms   write 110s
#:       200 rows  median 1,374ms  p95 1,909ms   write 112s
#:       100 rows  median   844ms  p95   988ms   write 176s
#:
#: Throughput is the thing being traded, and throughput is exactly what a
#: background job has to spare — it has all night. A reader does not.
SERVING_BATCH_ROWS = int(os.getenv("GEOGRAPH_JOB_MERGE_BATCH", "100"))


def _enabled(name: str, default: bool = True) -> bool:
    raw = os.getenv(f"GEOGRAPH_JOB_{name.upper()}", "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes"}


@dataclass
class JobState:
    """What a job has done — the whole of `/api/jobs`."""

    name: str
    every: float
    enabled: bool
    last_started: float | None = None
    last_finished: float | None = None
    last_seconds: float | None = None
    last_result: dict[str, Any] | None = None
    last_error: str | None = None
    runs: int = 0
    failures: int = 0
    next_due: float = 0.0

    def payload(self, now: float) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "every_seconds": self.every,
            "runs": self.runs,
            "failures": self.failures,
            "seconds_since_last_run": (
                round(now - self.last_finished, 1) if self.last_finished else None
            ),
            "last_seconds": self.last_seconds,
            "last_result": self.last_result,
            "last_error": self.last_error,
            "due_in_seconds": max(0.0, round(self.next_due - now, 1)),
        }


@dataclass
class Job:
    """One unit of recurring work.

    `run` receives the scheduler's graph connection and a deadline, and returns
    a small dict for `/api/jobs`. It must respect the deadline; the scheduler
    does not kill it (a killed write is worse than a late one).
    """

    name: str
    every: float
    run: Callable[[Any, float], dict[str, Any]]
    enabled: bool = True
    slice_seconds: float = DEFAULT_SLICE_SECONDS
    state: JobState = field(init=False)

    def __post_init__(self) -> None:
        self.state = JobState(name=self.name, every=self.every, enabled=self.enabled)


class Scheduler:
    """One thread, one connection, one job at a time.

    Serial on purpose: two concurrent writers inside the process would
    interleave transactions on the same database, and the value here is
    convergence, not throughput.
    """

    def __init__(self, app: Any, settings: Any, jobs: list[Job]) -> None:
        self.app = app
        self.settings = settings
        self.jobs = jobs
        self.conn: Any = None
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started_at: float | None = None
        self.current: str | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self.started_at = time.monotonic()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="geograph-jobs"
        )
        self._thread.start()

    def stop(self, timeout: float = 30.0) -> None:
        """Stop, and WAIT for the job in flight to finish its batch.

        This is the one place the design takes a risk the old child-process
        boot did not: a write killed mid-transaction can leave the database
        unable to take the next one (reproduced locally on 2026-08-15 — a
        `timeout` kill during a 5,000-row merge left every later write
        segfaulting, while the same batch wrote cleanly into a fresh graph).
        A deploy sends SIGTERM and waits, so draining the current batch is
        both possible and the whole mitigation. Batches are small by design
        so the wait is short; the bound stops a wedged job from blocking a
        shutdown forever.
        """
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    # ── the loop ───────────────────────────────────────────────────────────
    def _open(self) -> bool:
        """THE API'S OWN CONNECTION — not a second one.

        This started as a sibling connection (`kuzu_store.sibling`), which is
        legal and works on a fresh graph. In production it did not: the study
        job's first AFFECTED merge died inside Kuzu's rel storage
        (csr_node_group.cpp KU_UNREACHABLE), and the same write on the OWNER
        connection is the configuration this archive has been written by all
        along — the boot's child processes wrote 632k AFFECTED edges and 815k
        events that way. Reproduced locally on the owner connection at scale
        with no failure: 200,000 edges onto one market node, then 20,000
        re-merges through the ON MATCH SET path.

        Exclusion now comes from `kuzu_store.ACCESS` (readers share, writers
        exclude, writers are never starved), which is what makes sharing one
        connection between request threads and the job safe. `sibling` stays in
        the store for read-only helpers; nothing writes through one.
        """
        graph = getattr(self.app.state, "graph", None)
        if graph is None:
            return False
        self.conn = graph
        self.error = None
        return True

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            if not self._open():
                continue
            # The API's graph can be replaced (a reopen after a failure), so
            # the connection is re-read every pass rather than cached.
            now = time.monotonic()
            for job in self.jobs:
                if self._stop.is_set():
                    return
                if not job.enabled or now < job.state.next_due:
                    continue
                self._run(job)
                now = time.monotonic()
                if self._stop.is_set():
                    return

    def _run(self, job: Job) -> None:
        state = job.state
        state.last_started = time.monotonic()
        self.current = job.name
        try:
            result = job.run(self.conn, state.last_started + job.slice_seconds)
            state.last_result = result
            state.last_error = None
            state.runs += 1
            state.next_due = time.monotonic() + job.every
        except Exception as exc:  # noqa: BLE001 - report and back off
            state.failures += 1
            state.last_error = f"{type(exc).__name__}: {exc}"
            state.next_due = time.monotonic() + FAILURE_BACKOFF_SECONDS
            traceback.print_exc()
        finally:
            state.last_finished = time.monotonic()
            state.last_seconds = round(state.last_finished - state.last_started, 2)
            self.current = None

    # ── what a reader sees ─────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "running": self._thread is not None and self._thread.is_alive(),
            "current": self.current,
            "uptime_seconds": (
                round(now - self.started_at, 1) if self.started_at else None
            ),
            "error": self.error,
            "slice_seconds": DEFAULT_SLICE_SECONDS,
            "jobs": [job.state.payload(now) for job in self.jobs],
        }
