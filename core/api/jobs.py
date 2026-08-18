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
#: Reclaim rebuildable caches below this much free memory, and stop starting
#: jobs below this much. Measured against the CGROUP, never the host: the
#: container is 8 GB, the corpus is ~1.3 GB in two representations, Kuzu's
#: page cache is another 1.6 GB, and a job's working set on top of that is
#: what reached the ceiling on 2026-08-16.
#: Consecutive signal deaths of a CHILD job before it switches itself off. A
#: child that segfaults is survivable — that is why the study runs in one —
#: but each attempt still costs the graph-dark slice, so a write that always
#: crashes should stop being attempted rather than stop the site's freshness
#: forever at a cost.
SIGNAL_DEATHS_BEFORE_GIVING_UP = int(os.getenv("GEOGRAPH_SIGNAL_GIVE_UP", "3"))

MEMORY_RECLAIM_BELOW = float(os.getenv("GEOGRAPH_MEMORY_RECLAIM_BELOW", "0.30"))
MEMORY_PAUSE_BELOW = float(os.getenv("GEOGRAPH_MEMORY_PAUSE_BELOW", "0.12"))

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
    #: Consecutive times a CHILD job's process was killed by a signal — a
    #: segfault leaves no exception to count, only a negative returncode.
    signal_deaths: int = 0
    next_due: float = 0.0

    def payload(self, now: float) -> dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "signal_deaths": self.signal_deaths,
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
    #: A CHILD job's `run` returns a PLAN — {"argv": [...]} — instead of doing
    #: the work. The scheduler then closes the API's graph, runs that argv to
    #: completion, and reopens. Reserved for the one writer that cannot live
    #: in this process: see `work.study`.
    child: bool = False
    state: JobState = field(init=False)

    def __post_init__(self) -> None:
        self.state = JobState(name=self.name, every=self.every, enabled=self.enabled)


def _forget_region_contexts() -> None:
    """Drop the per-region game contexts and the caches built from them.

    THE ONE THAT GROWS WITH SUCCESS, which is why it was found last. A region's
    context holds `pricing.measured_effects` — EVERY measured event-market
    effect for that region, as a dict per row — so it is ~900,000 dicts today
    across three regions and larger every time the study writes. The container
    climbed from 5.17 GB to 6.93 GB over half an hour with six reclaims that
    recovered nothing, because none of them touched this.

    All of it is rebuildable: the games job re-solves a region from scratch,
    and the routers' caches are read-through.
    """
    from core.api.routers import dyads as dyads_router
    from core.api.routers import games as games_router
    from core.games import context as context_module
    from core.reasoning import calibration, impact

    context_module.CACHE.clear()
    games_router._BASELINE_CACHE.clear()
    dyads_router._CACHE.clear()
    impact._COVERAGE_CACHE.clear()
    calibration.CACHE.clear()


def memory_is_tight(floor: float = MEMORY_PAUSE_BELOW) -> bool:
    """Is the container close enough to its limit that a long job should stop?

    The scheduler checks headroom BEFORE each job, which cannot help a job
    that allocates gigabytes inside a single run — and on 2026-08-16 that is
    what happened: memory reached 7.67 GB of a 7.45 GiB limit and the kernel
    killed the container mid-job, twice. A job with a natural stopping point
    (the wire's line boundary, the study's event boundary) calls this there
    and gives up its slice instead of the process.

    Unknown limits mean "not containerised", which is never tight.
    """
    from core.graph import kuzu_store

    limit = kuzu_store.container_memory_bytes()
    used = kuzu_store.memory_in_use_bytes()
    if not limit or used is None:
        return False
    return (1.0 - used / limit) < floor


def _return_free_arenas() -> None:
    """Hand glibc's freed arenas back to the kernel.

    `gc.collect()` frees Python objects; it does not shrink the process. glibc
    keeps freed arenas for reuse, so RSS stays at the high-water mark of the
    heaviest job that has ever run — and RSS is what the cgroup kills on. The
    forecasts job peaks well above its steady state, so after one pass the
    container looks nearly full while most of that memory is available to
    malloc and to nobody else.

    `malloc_trim(0)` is the glibc call for exactly this. Absent on musl and on
    Windows, so it is best-effort and never fatal.

    DISABLE WITH `GEOGRAPH_MALLOC_TRIM=0`. It is the only thing in this loop
    that reaches into the C allocator of a process holding a large C++
    extension's buffers, which makes it the first thing to rule out if the
    container starts dying without a traceback. argtypes are declared because
    ctypes otherwise passes the argument as a C int, and this takes a size_t.
    """
    if os.getenv("GEOGRAPH_MALLOC_TRIM", "1") != "1":
        return
    import ctypes
    import ctypes.util

    try:
        name = ctypes.util.find_library("c") or "libc.so.6"
        libc = ctypes.CDLL(name)
        libc.malloc_trim.argtypes = [ctypes.c_size_t]
        libc.malloc_trim.restype = ctypes.c_int
        libc.malloc_trim(0)
    except (OSError, AttributeError):
        pass


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
        self.paused_for_memory = False
        self.memory_reclaims = 0

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

    def _headroom(self) -> float | None:
        """Fraction of the container's memory still free, or None if unknown."""
        from core.graph import kuzu_store

        limit = kuzu_store.container_memory_bytes()
        used = kuzu_store.memory_in_use_bytes()
        if not limit or used is None:
            return None
        return max(0.0, 1.0 - used / limit)

    def _memory_allows(self, job: Job) -> bool:
        """Reclaim, then decide whether to start this job at all."""
        headroom = self._headroom()
        if headroom is None:
            return True
        if headroom < MEMORY_RECLAIM_BELOW:
            self._reclaim()
            self.memory_reclaims += 1
            headroom = self._headroom() or 1.0
        # PAUSE RATHER THAN BE KILLED. Every job here re-derives something
        # already persisted; none is worth the container. A tick skipped under
        # pressure costs minutes of freshness, and the alternative cost a
        # database.
        if headroom < MEMORY_PAUSE_BELOW:
            self.paused_for_memory = True
            job.state.last_result = {
                "skipped": "paused for memory",
                "headroom": round(headroom, 3),
            }
            return False
        self.paused_for_memory = False
        return True

    def _reclaim(self) -> None:
        """Give memory back before it becomes a kill.

        An OOM kill is not an exception this process can catch — the kernel
        takes it mid-write, and on 2026-08-16 that left the graph's write-ahead
        log unreplayable and every graph endpoint on 503. So the loop watches
        the cgroup it is actually held to and drops the caches it can rebuild:
        the parsed corpus (~450 MB a pack, re-parsed in ~5s) and whatever the
        last job left behind.
        """
        import gc

        from core.api import work
        from core.wire import corpus

        corpus.evict()
        # The study's archive cache: lean event rows plus their parsed dates,
        # held between ticks so a tick does not re-scan the graph.
        work.forget_archive()
        # Roster-dyad once-per-process flag; a reclaim must not leave the next
        # wire tick thinking it already merged them.
        work.forget_wire_ids()
        _forget_region_contexts()
        gc.collect()
        _return_free_arenas()

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
                # CHECKED BEFORE EACH JOB, not once per pass. Measured in
                # production on 2026-08-16: headroom fell to 0.05 while a
                # single job ran, and the loop — blocked inside that job —
                # recorded no reclaim at all, because the once-a-pass check
                # cannot fire while the pass is inside a job.
                if not self._memory_allows(job):
                    continue
                self._run(job)
                now = time.monotonic()
                if self._stop.is_set():
                    return

    def _run_child(self, job: Job, plan: dict[str, Any]) -> dict[str, Any]:
        """Hand the single-writer lock to a child, then take it back.

        The API closes its connection first — Kuzu is one writer per PROCESS,
        so the child cannot take the lock while this process holds it — and
        graph endpoints answer 503 with a reason for the duration. The child
        stops on its OWN budget rather than being killed, because a write
        killed mid-commit is the one way this loop could damage the volume.
        """
        import subprocess

        from core.graph import kuzu_store

        argv = list(plan["argv"])
        budget = float(plan.get("budget_seconds") or job.slice_seconds)
        graph = getattr(self.app.state, "graph", None)
        # ORDER MATTERS. Clearing the state first makes every new request 503
        # WITHOUT touching the lock; taking the write lock second drains the
        # statements already in flight (readers go first — the queue is FIFO)
        # so nothing is mid-query when the handle closes. The lock is released
        # before the child starts: holding it across 90s would turn a fast 503
        # into a 90s hang for anyone who captured the handle a microsecond
        # before it was cleared.
        self.app.state.graph = None
        self.conn = None
        self.app.state.graph_error = (
            f"measuring the archive ({job.name}) — the graph reopens in about "
            f"{int(budget)}s; corpus-fed pages are unaffected"
        )
        with kuzu_store.ACCESS.write():
            kuzu_store.close(graph)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=budget + 60.0, check=False,
            )
            tail = (proc.stdout or "").strip().splitlines()[-3:]
            outcome: dict[str, Any] = {
                **{k: v for k, v in plan.items() if k != "argv"},
                "seconds": round(time.monotonic() - started, 1),
                "ok": proc.returncode == 0,
                "tail": tail,
            }
            if proc.returncode != 0:
                outcome["error"] = (proc.stderr or "").strip().splitlines()[-3:]
            # KILLED BY A SIGNAL is a different thing from exiting non-zero: a
            # negative returncode means the child died the way this job used
            # to kill the API — a segfault, with no Python evidence. Running
            # it in a child is what made that survivable; running it forever
            # against a write that always crashes would pay the graph-dark
            # slice for nothing, so it gives up and says so.
            if proc.returncode is not None and proc.returncode < 0:
                state = job.state
                state.signal_deaths += 1
                outcome["killed_by_signal"] = -proc.returncode
                outcome["consecutive"] = state.signal_deaths
                if state.signal_deaths >= SIGNAL_DEATHS_BEFORE_GIVING_UP:
                    job.enabled = False
                    outcome["disabled"] = (
                        f"the child was killed by a signal "
                        f"{state.signal_deaths} times in a row; the study is "
                        "off until a deploy re-enables it, because each "
                        "attempt costs a graph-dark slice and writes nothing"
                    )
            else:
                job.state.signal_deaths = 0
            return outcome
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "child overran its budget and was killed",
                    "seconds": round(time.monotonic() - started, 1)}
        finally:
            from core.api.app import _open_graph  # late: avoids an import cycle

            _open_graph(self.app, self.settings)
            # Re-point THIS scheduler at the new handle: the remaining jobs in
            # this pass run against it, and they were handed the old one.
            self._open()

    def _run(self, job: Job) -> None:
        state = job.state
        state.last_started = time.monotonic()
        self.current = job.name
        # A BREADCRUMB THAT SURVIVES THE PROCESS, because on 2026-08-16 the
        # container died silently inside the first job pass — no traceback, no
        # memory pressure, nothing in `/api/jobs` (the endpoint went with it).
        # A segfault in the C++ layer leaves no Python evidence at all, so the
        # only way to name the job is to have said its name before it ran.
        print(f"job: {job.name} starting", flush=True)
        try:
            result = job.run(self.conn, state.last_started + job.slice_seconds)
            if job.child and isinstance(result, dict) and result.get("argv"):
                result = self._run_child(job, result)
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
            # THE OTHER HALF OF THE BREADCRUMB, and it was missing when it was
            # needed: only "starting" ever printed, so a crash could be blamed
            # on the last job to START when it had in fact finished and the
            # process died somewhere after. Both lines, or the trail lies.
            print(
                f"job: {job.name} finished in {state.last_seconds}s",
                flush=True,
            )
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
            # REPORTED, because a loop that quietly stops looks identical to a
            # loop with nothing to do — and this one stops on purpose.
            "memory": self._memory_payload(),
            "jobs": [job.state.payload(now) for job in self.jobs],
        }

    def _memory_payload(self) -> dict[str, Any]:
        from core.graph import kuzu_store

        limit = kuzu_store.container_memory_bytes()
        used = kuzu_store.memory_in_use_bytes()
        headroom = self._headroom()
        from core import settings as settings_module

        disk = kuzu_store.disk_usage(settings_module.load().kuzu_db_path)
        raw = kuzu_store.memory_raw_bytes()
        return {
            "limit_gb": round(limit / 2**30, 2) if limit else None,
            "used_gb": round(used / 2**30, 2) if used is not None else None,
            # The cgroup's raw number and the file cache it includes — the
            # difference is what `used_gb` measures (see memory_in_use_bytes).
            "raw_gb": round(raw / 2**30, 2) if raw is not None else None,
            "file_cache_gb": round(kuzu_store.memory_file_cache_bytes() / 2**30, 2),
            "headroom": round(headroom, 3) if headroom is not None else None,
            "buffer_pool_gb": round(kuzu_store.buffer_pool_bytes() / 2**30, 2),
            "paused_for_memory": self.paused_for_memory,
            "reclaims": self.memory_reclaims,
            # THE VOLUME, because a full one is a crash the code cannot catch:
            # 2026-08-16, 5 GB, "No space left on device" on every write.
            "disk_free_gb": round(disk["free"] / 2**30, 2) if disk else None,
            "disk_total_gb": round(disk["total"] / 2**30, 2) if disk else None,
        }
