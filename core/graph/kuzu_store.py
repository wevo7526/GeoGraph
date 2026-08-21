"""Kuzu writers and the provenance backstop — the MarketGraph store pattern.

EVERY WRITE GOES THROUGH `merge_nodes` / `merge_edges`, which call the
validators derived from the LinkML schema. Nothing writes an edge by another
path; that discipline is what makes `check_provenance` a backstop instead of
the only line of defense.

KUZU IS SINGLE-WRITER. One process holds an exclusive lock on the graph
directory. Batch ingestion and transmission jobs therefore write ONE AT A TIME
(build-spec section 6); `connect` detects the lock error and says so rather
than blaming the path.

FIVE KUZU BEHAVIOURS FAIL SILENTLY — inherited knowledge from MarketGraph,
same engine, same traps. Do not "simplify" the workarounds:
  - `count(DISTINCT x)` and `sum(y)` in one RETURN → the sum is NULL.
  - `sum(CASE WHEN ...)` → NULL. Use arithmetic identities.
  - `MATCH (n:A|B)` → unsupported. UNION ALL per label.
  - `RETURN n` across a UNION → NODE types differ per table; return scalars.
  - `sum(x)` over INT64 → a Decimal, which FastAPI serialises as a JSON
    STRING. `_plain` normalises every row at this boundary — never per-query.
Also: `when` is a reserved word, properties bind per label, and the parser
rejects `--` comments.
"""

from __future__ import annotations

import contextlib
import decimal
import math
import os
import threading
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import kuzu

from core.ontology import kuzu_schema as ontology

__all__ = [
    "GraphUnavailable",
    "apply_schema",
    "check_provenance",
    "close",
    "connect",
    "merge_edges",
    "merge_nodes",
    "query",
    "write_edges",
]


class _ReadWriteLock:
    """FIFO fairness between many readers and one writer — PROCESS-WIDE.

    WHY A LOCK AT ALL (2026-08-15, learned in production). Kuzu CHECKPOINTS
    after a write, and a checkpoint requires that no transaction is active
    anywhere in the process. That is fine in a batch job with nothing else
    running — the design until recurring work moved inside the API. It is not
    fine when request threads read continuously: there is always an active
    transaction, the checkpoint waits, and the write dies. Reproduced as
    "Timeout waiting for active transactions to leave the system before
    checkpointing"; in production it surfaced as an internal assertion in the
    rel-table storage (csr_node_group.cpp KU_UNREACHABLE) on the study job's
    first AFFECTED merge.

    WHY FIFO AND NOT A PREFERENCE, which took two more measurements to get
    right. Readers-only-wait-for-an-active-writer starves the writer: three
    reader threads in a loop hand the lock to each other, the reader count
    never reaches zero, and a job hangs forever. Flipping to writer preference
    fixed that and starved the READERS instead — a job writing in a tight loop
    always has a request in, so new readers are blocked for the whole job
    slice. Measured on a 20,000-edge write: reads went from a 3ms median to a
    10.2s median, and a case study that answered in 1.3s timed out at 30s.

    So the queue is strictly first-come-first-served, with consecutive readers
    granted together. A reader arriving mid-write waits for the statement in
    flight and then goes AHEAD of the writer's next one; a writer waits out
    the readers already queued and then gets its turn. Neither side can be
    starved by the other's traffic pattern, which is the only property that
    makes a converging archive and a live API coexist in one process.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._readers = 0
        self._writer = False
        self._queue: deque[dict[str, Any]] = deque()

    def _dispatch(self) -> None:
        """Grant from the head of the queue. Called with the condition held."""
        granted = False
        while self._queue:
            head = self._queue[0]
            if head["kind"] == "w":
                if self._readers or self._writer:
                    break
                self._writer = True
                head["ready"] = True
                self._queue.popleft()
                granted = True
                break
            if self._writer:
                break
            self._readers += 1
            head["ready"] = True
            self._queue.popleft()
            granted = True
        if granted:
            self._condition.notify_all()

    def _acquire(self, kind: str) -> None:
        waiter = {"kind": kind, "ready": False}
        with self._condition:
            self._queue.append(waiter)
            self._dispatch()
            while not waiter["ready"]:
                self._condition.wait()

    @contextlib.contextmanager
    def read(self) -> Iterator[None]:
        self._acquire("r")
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                self._dispatch()
                self._condition.notify_all()

    @contextlib.contextmanager
    def write(self) -> Iterator[None]:
        self._acquire("w")
        try:
            yield
        finally:
            with self._condition:
                self._writer = False
                self._dispatch()
                self._condition.notify_all()


#: The one lock every graph access in this process passes through. Exported so
#: a caller doing several statements as a unit (a job's batch) can hold it.
ACCESS = _ReadWriteLock()


class GraphUnavailable(RuntimeError):
    """The graph cannot be opened. The message names the likely fix."""


#: Where a Linux container states the limit it is actually held to — v2 then
#: v1. A module constant so a test can point it at a fixture.
CGROUP_LIMIT_FILES = (
    Path("/sys/fs/cgroup/memory.max"),
    Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
)

#: The matching current-usage files, same v2-then-v1 order.
CGROUP_USAGE_FILES = (
    Path("/sys/fs/cgroup/memory.current"),
    Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
)


def container_memory_bytes() -> int | None:
    """The CGROUP's memory limit, which is NOT what the kernel reports.

    `sysconf(_SC_PHYS_PAGES)` — what Kuzu sizes its buffer pool from — returns
    the HOST machine's RAM. Inside a container that number is meaningless and
    dangerous: on a big host it is tens of gigabytes, and a pool sized at 80%
    of it will keep filling long after the cgroup's own limit is reached, at
    which point the kernel kills the process. Read the limit the process is
    actually held to instead.
    """
    for candidate in CGROUP_LIMIT_FILES:
        try:
            raw = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw == "max":
            return None
        try:
            value = int(raw)
        except ValueError:
            continue
        # v1 reports an absurd sentinel when unlimited.
        if 0 < value < (1 << 62):
            return value
    return None


#: Share of the container's memory the graph's page cache may hold.
#:
#: THE REST IS NOT SPARE. This process also carries the wire corpus (1.33M
#: parsed events, cached for its lifetime by design), the jobs' working sets
#: and CPython itself — measured together at 2-3 GB. Kuzu's own default is
#: 80% of what it believes the machine has, which on Railway was the HOST's
#: memory; the pool grew past the 8 GB cgroup limit and the kernel killed the
#: process mid-write on 2026-08-16. That kill is what broke the database: the
#: WAL replay on the next open hit a duplicated primary key and every graph
#: endpoint served 503 until the recovery below ran.
#:
#: 0.24 — and the path there is the point, because BOTH directions have a
#: failure mode and they are not equally bad. 0.35 was Kuzu's own default
#: against the host's RAM and the kernel killed the container mid-write, which
#: corrupted the WAL. 0.20 held. 0.16 went too far the other way and the study
#: died on "Buffer manager exception: the buffer pool is full and no memory
#: could be freed" — a caught, backed-off, non-destructive failure, but a
#: stalled archive. 0.24 sits between them, with the per-job memory guard and
#: `malloc_trim` doing the work that a smaller pool was being asked to do.
#: Sized, not guessed. The wire corpus is
#: 1.3 GB resident in two representations, the jobs' working sets are another
#: 1-2 GB while one runs, and 0.35 still touched the 8 GB ceiling. The pool is
#: a CACHE: cutting it costs page faults against a memory-mapped file, and
#: that is the cheapest thing in this budget to give up.
BUFFER_POOL_SHARE = float(os.getenv("GEOGRAPH_BUFFER_POOL_SHARE", "0.24"))


#: The cgroup's memory breakdown, v2 then v1.
CGROUP_STAT_FILES = (
    Path("/sys/fs/cgroup/memory.stat"),
    Path("/sys/fs/cgroup/memory/memory.stat"),
)


def memory_raw_bytes() -> int | None:
    """`memory.current` as the cgroup reports it — anon + kernel + FILE CACHE."""
    for candidate in CGROUP_USAGE_FILES:
        try:
            return int(candidate.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
    return None


def memory_file_cache_bytes() -> int:
    """The file cache the kernel can drop CHEAPLY, out of what the cgroup
    counts in `memory.current` (v2 `file`, v1 `cache`). Zero when unreadable.

    DIRTY AND WRITEBACK PAGES ARE NOT CHEAP, and treating them as free is how
    the guard was still reporting headroom while the container died. Reclaiming
    a dirty page means writing it out first; under a job that is merging edges
    into a 2.9 GB database, pages are dirtied faster than the kernel retires
    them, so that part of the cache stands between the process and the limit
    exactly the way anonymous memory does. On 2026-08-17 the container reached
    10.4 GB against an 8 GB limit and was killed mid-write — with the study job
    running and the loop's own headroom reading comfortable.

    Subtracting them is deliberately conservative: the guard now under-states
    free memory a little, pauses a job sooner, and the cost of that is minutes
    of freshness against the cost of the alternative, which was the database.
    """
    wanted = {"file", "cache"}
    charged = {"file_dirty", "dirty", "file_writeback", "writeback"}
    for candidate in CGROUP_STAT_FILES:
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        cache = 0
        pinned = 0
        found = False
        for line in text.splitlines():
            key, _, value = line.partition(" ")
            value = value.strip()
            if not value.isdigit():
                continue
            if key in wanted and not found:
                cache = int(value)
                found = True
            elif key in charged:
                pinned += int(value)
        if found:
            return max(0, cache - pinned)
    return 0


def memory_in_use_bytes() -> int | None:
    """What this container is holding RIGHT NOW that the kernel cannot
    reclaim — `memory.current` LESS the file page cache.

    THE FILE CACHE IS NOT PRESSURE. `memory.current` counts every page the
    kernel caches for the graph file, its WAL and shadow, and the artifacts
    read at warm; under steady I/O it sits pinned just under the limit and
    the kernel reclaims it lazily. On 2026-08-16 the raw number read 7.0 of
    7.45 GB, the loop paused every job for "memory", 586 reclaims freed
    nothing, and no OOM kill came — because ~1.7 GB of that was cache. The
    kill line is about anonymous memory; that is what the guard reads now,
    which is also what `docker stats` reports as usage.

    The scheduler reads this before starting a job. Nothing else in the
    process knows how close the kernel's kill line is, and the kill is not
    catchable — by the time a MemoryError would be raised the container is
    already gone, mid-write.
    """
    raw = memory_raw_bytes()
    if raw is None:
        return None
    return max(0, raw - memory_file_cache_bytes())


#: Free bytes the volume must keep. Kuzu writes a shadow file and a WAL beside
#: the database, and a write that meets a full disk fails mid-transaction —
#: on 2026-08-16 the AFFECTED refill filled a 5 GB volume, every write step
#: died on `No space left on device`, and the container restart-looped.
DISK_FLOOR_BYTES = int(os.getenv("GEOGRAPH_DISK_FLOOR_BYTES", str(400 << 20)))


def disk_usage(path: Path) -> dict[str, int] | None:
    """(total, used, free) bytes for the filesystem holding `path` — the
    volume, in production. None when unreadable."""
    import shutil

    try:
        probe = path if path.exists() else path.parent
        usage = shutil.disk_usage(str(probe))
    except OSError:
        return None
    return {"total": int(usage.total), "used": int(usage.used), "free": int(usage.free)}


def disk_is_tight(path: Path, floor: int = DISK_FLOOR_BYTES) -> bool:
    """Is the volume too full for another write? Unknown means not tight."""
    usage = disk_usage(path)
    return usage is not None and usage["free"] < floor


def reclaim_non_data(path: Path) -> dict[str, Any]:
    """Delete quarantined WAL tails and tmp files beside the database.

    THE DEADLOCK THIS BREAKS. A full volume cannot DROP a table: Kuzu needs
    room for a twelve-byte WAL record to do anything. Quarantined
    `*.wal.broken-*` tails (~20 MB each) and leftover `*.tmp` files are
    evidence, not data — deleting them is the only reclaim that happens
    outside the database. Dropping a table does not shrink the file on disk,
    and this function never drops Dyad nodes or AFFECTED edges: those ARE
    the knowledge graph the 5 GB volume is for.
    """
    freed = 0
    removed: list[str] = []
    parent = path.parent
    for stale in sorted(parent.glob(f"{path.name}.wal.broken-*")):
        with contextlib.suppress(OSError):
            size = stale.stat().st_size
            stale.unlink()
            freed += size
            removed.append(stale.name)
    for stale in sorted(parent.glob("*.tmp")):
        with contextlib.suppress(OSError):
            size = stale.stat().st_size
            stale.unlink()
            freed += size
            removed.append(stale.name)
    return {
        "freed_bytes": freed,
        "removed": removed,
        "disk": disk_usage(path),
    }


def buffer_pool_bytes() -> int:
    """How much page cache to allow — sized to the CONTAINER, never the host."""
    override = os.getenv("GEOGRAPH_BUFFER_POOL_BYTES")
    if override:
        return int(override)
    limit = container_memory_bytes()
    if limit is None:
        return 0  # not containerised: let Kuzu use its own default
    return max(256 << 20, int(limit * BUFFER_POOL_SHARE))


#: Open failures that mean "the WAL cannot be replayed", not "the data is
#: gone". Every one of these has been produced by a process killed mid-commit.
_WAL_REPLAY_MARKERS = (
    "violates the uniqueness constraint",
    "duplicated primary key",
    "failed to replay",
    "wal",
)


def _quarantine_wal(db_path: Path) -> Path | None:
    """Move a WAL that cannot be replayed aside, so the database can open.

    THE DATA IS NOT IN THE WAL, IT IS IN THE DATABASE. Kuzu checkpoints as it
    goes; the WAL holds only the tail since the last checkpoint. When a process
    is killed mid-commit that tail can describe a write the checkpoint already
    contains, and the replay then fails on the primary key it is re-inserting —
    which takes the whole database down for writes it had already durably made.

    So the tail is renamed rather than deleted (it is evidence, and it is
    small), the database opens at its last checkpoint, and the jobs re-measure
    whatever the tail held: every writer here is watermarked and idempotent,
    which is exactly what makes discarding a tail safe.
    """
    wal = Path(str(db_path) + ".wal")
    if not wal.exists():
        return None
    stamp = f"{wal}.broken-{int(wal.stat().st_mtime)}"
    try:
        wal.rename(stamp)
    except OSError:
        return None
    shadow = Path(str(db_path) + ".shadow")
    if shadow.exists():
        with contextlib.suppress(OSError):
            shadow.unlink()
    return Path(stamp)


def _opening_marker(db_path: Path) -> Path:
    return Path(str(db_path) + ".opening")


def _open_crashed_last_time(db_path: Path) -> bool:
    """Did the previous open of this database take its process down?

    THE REPLAY DOES NOT ALWAYS RAISE — SOMETIMES IT SEGFAULTS, and that is the
    hole this closes. `_quarantine_wal` below is reached from an `except
    RuntimeError`, so it only ever helped when Kuzu managed to report the
    problem. On 2026-08-17 an OOM kill landed mid-write (the container reached
    10.4 GB against an 8 GB limit during the study job) and the WAL it left
    behind killed the C++ extension on replay instead: every boot step that
    opened the graph exited by SIGNAL with no output, the API died the same way
    a few seconds later, and Railway's three restarts were spent re-crashing.
    Nothing in Python ran, so nothing recovered — the site was down until a
    human noticed.

    A file beside the database is the only state that survives a signal. It is
    written before the open and removed after it, so finding one means the last
    process to try this never came back.
    """
    return _opening_marker(db_path).exists()


def _clear_opening_marker(db_path: Path, *, read_only: bool = False) -> None:
    if read_only:
        return
    with contextlib.suppress(OSError):
        _opening_marker(db_path).unlink(missing_ok=True)


def connect(db_path: Path, *, read_only: bool = False) -> kuzu.Connection:
    """Open the embedded graph. Diagnoses the single-writer lock explicitly."""
    # A READER NEVER MOVES THE VOLUME'S FILES. Recovery is a writer's job, and
    # a read-only open is often a diagnostic run by someone looking at exactly
    # this failure.
    crashed = not read_only and _open_crashed_last_time(db_path)
    if crashed:
        moved = _quarantine_wal(db_path)
        if moved is not None:
            print(
                f"graph: the last attempt to open {db_path.name} did not come "
                f"back — its process was killed or crashed during the write-"
                f"ahead replay. Moved the tail to {moved.name} and opening at "
                "the last checkpoint; the watermarked jobs re-measure it.",
                flush=True,
            )
        else:
            # No tail to blame. Say so loudly rather than quietly trying the
            # same thing again: if this open crashes too, the storage itself
            # needs `scripts/rebuild_affected.py` or a GEOGRAPH_RESET_GRAPH
            # rebuild, and the operator should not have to infer that from a
            # restart loop.
            print(
                f"graph: the last attempt to open {db_path.name} did not come "
                "back, and there is no write-ahead tail to quarantine. If this "
                "open crashes as well the storage is damaged: re-project "
                "AFFECTED (scripts/rebuild_affected.py) or rebuild the graph "
                "with GEOGRAPH_RESET_GRAPH=<token>.",
                flush=True,
            )
    if not read_only:
        with contextlib.suppress(OSError):
            _opening_marker(db_path).parent.mkdir(parents=True, exist_ok=True)
            _opening_marker(db_path).write_text("opening", encoding="utf-8")
    try:
        db = kuzu.Database(str(db_path), read_only=read_only,
                           buffer_pool_size=buffer_pool_bytes())
        conn = kuzu.Connection(db)
    except RuntimeError as exc:  # kuzu raises RuntimeError for IO/lock errors
        message = str(exc)
        if "lock" in message.lower():
            raise GraphUnavailable(
                f"Another process holds the write lock on {db_path}. Kuzu is "
                "single-writer: stop the other writer (the API process, or a "
                "running ingest/transmission job) or open read-only."
            ) from exc
        if "virtualalloc" in message.lower() or "buffer manager" in message.lower():
            raise GraphUnavailable(
                f"Cannot open {db_path}: the process is out of virtual address "
                "space for graphs. EACH open Kuzu database reserves an 8 TiB "
                "virtual allocation, so ONE PROCESS CAN HOLD ONLY ~15 AT ONCE — "
                "call kuzu_store.close() on graphs you are done with rather than "
                "dropping the reference. Original: " + message
            ) from exc
        lowered = message.lower()
        if not read_only and any(m in lowered for m in _WAL_REPLAY_MARKERS):
            # A write-ahead tail that cannot be replayed. Recoverable, and the
            # alternative is a permanently unopenable database — which is what
            # production had for 25 minutes on 2026-08-16 after an OOM kill.
            moved = _quarantine_wal(db_path)
            if moved is not None:
                print(
                    f"graph: the write-ahead log could not be replayed "
                    f"({message.strip()}). Moved it to {moved.name} and opened "
                    "at the last checkpoint; the watermarked jobs re-measure "
                    "the tail.",
                    flush=True,
                )
                db = kuzu.Database(str(db_path), read_only=read_only,
                                   buffer_pool_size=buffer_pool_bytes())
                conn = kuzu.Connection(db)
                conn._geograph_db = db  # type: ignore[attr-defined]
                _clear_opening_marker(db_path, read_only=read_only)
                return conn
        _clear_opening_marker(db_path, read_only=read_only)
        raise GraphUnavailable(f"Cannot open graph at {db_path}: {message}") from exc
    # Keep the Database reachable from the Connection so `close` can shut both.
    # Dropping the Python reference does NOT reliably release the write lock or
    # the reservation; only closing does.
    conn._geograph_db = db  # type: ignore[attr-defined]
    # THE OPEN CAME BACK — clear the crash marker. It is cleared after a
    # RuntimeError too: an exception means Python is still running, which is
    # the case the markers above already handle by message.
    _clear_opening_marker(db_path, read_only=read_only)
    return conn


def sibling(conn: kuzu.Connection) -> kuzu.Connection:
    """A SECOND connection on an already-open database.

    Kuzu's single-writer rule is per PROCESS, not per connection: the process
    holding the database may open as many connections to it as it likes. That
    distinction is what lets the API run background work (core/api/jobs.py)
    while it serves — the alternative was doing every heavy job inside a boot,
    where it costs the container's whole downtime.

    Never `connect()` a second time for this: that opens a second
    `kuzu.Database`, which reserves another 8 TiB of virtual address space and
    then fails on the lock the same process is already holding.
    """
    database = getattr(conn, "_geograph_db", None)
    if database is None:
        raise GraphUnavailable(
            "this connection was not opened by kuzu_store.connect, so its "
            "database is unreachable — a sibling connection needs it"
        )
    twin = kuzu.Connection(database)
    # Deliberately NOT stamped with _geograph_db: `close` shuts the database
    # down, and a sibling closing the API's graph would be the worst kind of
    # helpful. Siblings are closed by dropping them; the owner closes the db.
    return twin


def close(conn: kuzu.Connection | None) -> None:
    """Release a graph: close the connection AND its database.

    WHY THIS IS NOT OPTIONAL HYGIENE. Two separate limits bite without it.
    Kuzu is single-writer, so a graph left open blocks the next writer — the
    API cannot take a lock a finished batch job still holds. And each open
    database reserves an 8 TiB virtual allocation, so a process that opens
    graphs in a loop dies at roughly the fifteenth with a buffer-manager error
    that names memory rather than the real cause.
    """
    if conn is None:
        return
    database = getattr(conn, "_geograph_db", None)
    for target in (conn, database):
        if target is None:
            continue
        with contextlib.suppress(Exception):
            target.close()


def apply_schema(conn: kuzu.Connection) -> None:
    """Create every table the ontology declares, then ADD any property an
    existing table is missing. Idempotent.

    The second half is THE MIGRATION PATH. `CREATE ... IF NOT EXISTS` skips a
    table that already exists, so a graph created before the model gained a
    column (a Railway volume survives deploys — that is its job) would refuse
    the next seed with a binder error naming the property. Additive columns
    are the only migration the ontology can imply; a REMOVED or retyped
    property still needs a rebuild, which for this graph is a delete-and-
    reseed — every fact reloads from the packs and crosswalks by design.
    """
    # DDL is a write, so it takes the exclusive side — but NOT around the
    # `query` calls below, which take the shared side: this lock is not
    # reentrant across modes and nesting them would deadlock. Schema work
    # happens before the API publishes its connection, so nothing is reading.
    with ACCESS.write():
        for statement in ontology.ddl():
            conn.execute(statement)

    tables = [(spec.table, spec.props) for spec in ontology.nodes().values()]
    tables += [(spec.rel, spec.props) for spec in ontology.edges().values()]
    for table, props in tables:
        live = {row["name"] for row in query(conn, f"CALL table_info('{table}') RETURN *")}
        missing = [p for p in props if p.name not in live]
        if not missing:
            continue
        with ACCESS.write():
            for prop in missing:
                conn.execute(f"ALTER TABLE {table} ADD {prop.name} {prop.kuzu_type}")


def _plain(value: Any) -> Any:
    """Normalise driver values at the boundary — THE Decimal fix.

    Kuzu returns Decimal for INT64 aggregates; FastAPI serialises Decimal as a
    JSON string, and JavaScript coerces number-shaped strings in arithmetic so
    nothing downstream ever throws — formatting just quietly breaks. Fixed
    here, once, never per-query.
    """
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    # NaN/Inf round-trip out of Kuzu as real floats, and Starlette's JSON
    # renderer runs with allow_nan=False — one non-finite value 500s the whole
    # response. Not-a-measurement is None at this boundary, matching the panel
    # store's _finite rule. (Edges written before write_effects sanitised its
    # inputs still carry NaN on the volume; this covers reading them back.)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def query(
    conn: kuzu.Connection, cypher: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Run one Cypher statement, returning plain dict rows.

    Holds the shared side of `ACCESS` for the WHOLE materialisation, not just
    the execute: a Kuzu transaction lives until its result is consumed, and a
    half-read result is exactly the "active transaction" that makes a
    concurrent write's checkpoint time out.
    """
    with ACCESS.read():
        result = conn.execute(cypher, parameters=params or {})
        if isinstance(result, list):  # kuzu returns a list only for `;`-chains
            result = result[-1]
        columns = result.get_column_names()
        rows: list[dict[str, Any]] = []
        while result.has_next():
            rows.append({
                col: _plain(val)
                for col, val in zip(columns, result.get_next(), strict=True)
            })
        return rows


def query_id_set(
    conn: kuzu.Connection, cypher: str, params: dict[str, Any] | None = None
) -> set[str]:
    """The first column of a result as a set of strings, WITHOUT building a row
    for each one.

    `query` above materialises every row as a dict — the right shape for an API
    response and the wrong one for "which of these hundreds of thousands of ids
    do I already hold". The wire job asked exactly that: one dict per gdelt
    event in a pack, hundreds of thousands of them, built and then thrown away
    to leave a set of ids behind. That transient was a large part of what put
    the container over its 8 GB limit on 2026-08-17 — the kill landed while the
    job that runs this was starting, twice.

    Same locking rule as `query`: the shared side is held for the whole
    materialisation, because a half-read result is an open transaction and an
    open transaction is what makes a concurrent write's checkpoint time out.
    """
    out: set[str] = set()
    with ACCESS.read():
        result = conn.execute(cypher, parameters=params or {})
        if isinstance(result, list):
            result = result[-1]
        while result.has_next():
            row: Any = result.get_next()
            value = row[0]
            if value is not None:
                out.add(str(value))
    return out


#: UNWIND batch size. One MERGE per row was the deep tier's 25-minute IGO
#: load; batched it is under a second per ten thousand — same statements,
#: same semantics, three hundred times fewer round trips.
#:
#: MUTABLE, because the right size depends on who is writing. A batch job
#: wants throughput and has no readers to keep waiting. THE SERVING PROCESS
#: WANTS LATENCY: a reader waits at most one statement (the lock is FIFO), so
#: statement size IS the p95 read latency under a converging archive —
#: measured at ~2.4s for 1,000 AFFECTED rows, which is what turned a 1.3s case
#: study into a 30s timeout. `core/api/app.py` sets the serving value.
BATCH_ROWS = int(os.getenv("GEOGRAPH_MERGE_BATCH", "1000"))


def _batches(rows: list[dict[str, Any]], signature: Any) -> list[list[dict[str, Any]]]:
    """Group rows by their column signature (UNWIND structs need uniform
    fields), then chunk. Deterministic order — grouped in first-seen order.

    None-valued keys are DROPPED first: a struct field that is None in every
    row of a chunk has no inferable type, and "absent" merges as
    leave-unset — which for idempotent loaders re-merging the same facts is
    the behavior that cannot destroy a value another writer set."""
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        cleaned = {k: v for k, v in row.items() if v is not None}
        grouped.setdefault(signature(cleaned), []).append(cleaned)
    out: list[list[dict[str, Any]]] = []
    for group in grouped.values():
        size = max(1, BATCH_ROWS)
        out.extend(group[i : i + size] for i in range(0, len(group), size))
    return out


def merge_nodes(conn: kuzu.Connection, table: str, rows: list[dict[str, Any]]) -> int:
    """Upsert nodes by node_id. Validates every row against the ontology."""
    spec = ontology.nodes().get(table)
    if spec is None:
        raise ontology.OntologyError(f"{table!r} is not a node table.")
    prop_names = [p.name for p in spec.props]
    for row in rows:
        ontology.validate_node(table, row)
    written = 0
    # EXCLUSIVE PER BATCH, not per call — see `_ReadWriteLock` for why that is
    # both safe and necessary.
    for batch in _batches(rows, lambda r: tuple(n for n in prop_names if n in r)):
        present = [n for n in prop_names if n in batch[0]]
        sets = ", ".join(f"n.{name} = row.{name}" for name in present)
        cypher = f"UNWIND $rows AS row MERGE (n:{table} {{node_id: row.node_id}})"
        if sets:
            cypher += f" ON CREATE SET {sets} ON MATCH SET {sets}"
        payload = [
            {"node_id": row["node_id"], **{n: row[n] for n in present}}
            for row in batch
        ]
        with ACCESS.write():
            conn.execute(cypher, parameters={"rows": payload})
        written += len(batch)
    return written


def merge_edges(conn: kuzu.Connection, rel: str, rows: list[dict[str, Any]]) -> int:
    """Upsert edges. THE ONLY EDGE-WRITE PATH for ordinary callers.

    Each row: {"src": node_id, "dst": node_id, **props}. Key slots — read from
    the ontology, never from a hardcoded rel-name test — identify the edge so
    two key values are two edges; the rest are SET.

    Implementation: delegates to `write_edges`. A Cypher `MERGE` on a rel
    forces Kuzu to scan the destination's CSR adjacency inside a write
    transaction, and that scan dies with `csr_node_group.cpp KU_UNREACHABLE`
    once Actor/Market adjacency lists grow — measured on AFFECTED (2026-08-16)
    and again on RELATES_TO during every pack seed + deep-tier load on
    production (2026-08-21). `write_edges` keeps the same upsert contract with
    an ordinary forward-adjacency read + CREATE/SET instead.
    """
    return write_edges(conn, rel, rows, check_existing=True)


def write_edges(
    conn: kuzu.Connection, rel: str, rows: list[dict[str, Any]], *,
    check_existing: bool = True,
) -> int:
    """Upsert edges WITHOUT asking MERGE to walk an adjacency list.

    `check_existing=False` skips the existence read and CREATEs every row — for
    a caller that KNOWS the rows are new (a loader writing events it has just
    confirmed absent, a refill past its resume marker). The read is a scan of
    the source table's forward adjacency per batch; over a million rows it is
    most of the write's cost, and it exists to keep the upsert an upsert, not
    to double-check a fact the caller already holds. Duplicated rel edges are
    silent in Kuzu (no unique constraint), so the caller's knowledge must be
    real: the loaders here derive it from a set of ids read off the graph or a
    marker saved after every chunk.

    Same contract as `merge_edges` — same validation, same key_slots identity,
    same provenance — but the existence check is an ordinary read instead of a
    MERGE pattern match.

    WHY IT EXISTS. `MERGE (a)-[r:AFFECTED {window}]->(b)` has to scan b's
    adjacency list to decide whether the edge is already there, and for
    AFFECTED that list is one of twenty: 756,025 edges between them, so every
    Market node's CSR group is enormous. Production died in exactly that scan
    — `csr_node_group.cpp KU_UNREACHABLE`, which is the `default:` arm of
    `CSRNodeGroup::scan()` — on a sibling connection, on the API's own
    connection, after the lock was made fair, and again from a child process.
    Four topologies, one statement.

    The read this uses instead is the SAME table the effects endpoints and
    /api/stats scan without trouble; only the scan nested inside a write
    transaction fails. So: read the keys that exist, CREATE the ones that do
    not, SET the ones that do. Two ordinary statements in place of one that
    cannot run.
    """
    spec = ontology.edges().get(rel)
    if spec is None:
        raise ontology.OntologyError(f"{rel!r} is not an edge table.")
    for row in rows:
        ontology.validate_edge(rel, {k: v for k, v in row.items() if k not in ("src", "dst")})
    if not rows:
        return 0

    written = 0
    for batch in _batches(
        rows, lambda r: tuple(sorted(k for k in r if k not in ("src", "dst")))
    ):
        props = [k for k in batch[0] if k not in ("src", "dst")]
        keys = [k for k in spec.key_slots if k in props]
        rest = [k for k in props if k not in keys]

        # 1. WHICH ALREADY EXIST. Scoped to this batch's source nodes, so the
        #    read walks the FORWARD adjacency (an Event has a handful of
        #    AFFECTED edges) rather than the backward one that is the problem.
        existing: set[tuple[Any, ...]] = set()
        if check_existing:
            returned = ", ".join(
                ["a.node_id AS src", "b.node_id AS dst"]
                + [f"r.{k} AS {k}" for k in keys]
            )
            found = query(
                conn,
                f"MATCH (a:{spec.src})-[r:{rel}]->(b:{spec.dst}) "
                f"WHERE a.node_id IN $srcs RETURN {returned}",
                {"srcs": sorted({str(r["src"]) for r in batch})},
            )
            existing = {
                (record["src"], record["dst"], *(record[k] for k in keys))
                for record in found
            }

        def _identity(row: dict[str, Any], keys: list[str] = keys) -> tuple[Any, ...]:
            return (row["src"], row["dst"], *(row[k] for k in keys))

        fresh = [r for r in batch if _identity(r) not in existing]
        stale = [r for r in batch if _identity(r) in existing]

        if fresh:
            sets = ", ".join(f"r.{k} = row.{k}" for k in props)
            cypher = (
                f"UNWIND $rows AS row "
                f"MATCH (a:{spec.src} {{node_id: row.src}}), "
                f"(b:{spec.dst} {{node_id: row.dst}}) "
                f"CREATE (a)-[r:{rel}]->(b)"
            )
            if sets:
                cypher += f" SET {sets}"
            with ACCESS.write():
                conn.execute(cypher, parameters={"rows": fresh})
        if stale and rest:
            # An update DOES match the pattern, but only for edges known to
            # exist — no CREATE branch, so no decision that needs the scan.
            key_pattern = " {" + ", ".join(f"{k}: row.{k}" for k in keys) + "}"
            sets = ", ".join(f"r.{k} = row.{k}" for k in rest)
            with ACCESS.write():
                conn.execute(
                    f"UNWIND $rows AS row "
                    f"MATCH (a:{spec.src} {{node_id: row.src}})"
                    f"-[r:{rel}{key_pattern}]->"
                    f"(b:{spec.dst} {{node_id: row.dst}}) "
                    f"SET {sets}",
                    parameters={"rows": stale},
                )
        written += len(batch)
    return written


def delete_edges(conn: kuzu.Connection, rel: str, rows: list[dict[str, Any]]) -> int:
    """Delete specific edges by identity — {"src", "dst", **key_slots}.

    THE ONLY EDGE-DELETE PATH, for the same reason `merge_edges` is the only
    write path: every statement takes the process-wide lock here. Deletion is
    by the ontology's key_slots for the rel (a keyless rel deletes every edge
    between the pair), through a MATCH on the source's forward adjacency — never
    a scan of the destination's, which is the shape that dies on AFFECTED.
    Callers: the repair probe (`scripts/rebuild_affected.py`), which removes
    the one edge it created to prove the CREATE path.
    """
    spec = ontology.edges().get(rel)
    if spec is None:
        raise ontology.OntologyError(f"{rel!r} is not an edge table.")
    if not rows:
        return 0
    deleted = 0
    for batch in _batches(rows, lambda r: tuple(sorted(k for k in r if k not in ("src", "dst")))):
        keys = [k for k in spec.key_slots if k in batch[0]]
        key_pattern = (
            (" {" + ", ".join(f"{k}: row.{k}" for k in keys) + "}") if keys else ""
        )
        payload = [
            {"src": r["src"], "dst": r["dst"], **{k: r[k] for k in keys}} for r in batch
        ]
        with ACCESS.write():
            conn.execute(
                f"UNWIND $rows AS row "
                f"MATCH (a:{spec.src} {{node_id: row.src}})"
                f"-[r:{rel}{key_pattern}]->"
                f"(b:{spec.dst} {{node_id: row.dst}}) DELETE r",
                parameters={"rows": payload},
            )
        deleted += len(batch)
    return deleted


def delete_nodes(conn: kuzu.Connection, table: str, node_ids: list[str]) -> int:
    """DETACH DELETE nodes by id, in batches — every edge touching them goes too.

    THE ONLY NODE-DELETE PATH. Destructive, and the caller is expected to
    know why it is safe for the nodes it names: `cow.prune_off_roster_actors`
    removes the actors no pack names, with the estimates, metrics, relations
    and deep-tier events that hang off them. Batched so a reader waits at most
    one statement (the lock is FIFO; see BATCH_ROWS).
    """
    spec = ontology.nodes().get(table)
    if spec is None:
        raise ontology.OntologyError(f"{table!r} is not a node table.")
    if not node_ids:
        return 0
    deleted = 0
    size = max(1, BATCH_ROWS)
    for start in range(0, len(node_ids), size):
        chunk = list(node_ids[start:start + size])
        with ACCESS.write():
            conn.execute(
                f"UNWIND $ids AS id MATCH (n:{table} {{node_id: id}}) DETACH DELETE n",
                parameters={"ids": chunk},
            )
        deleted += len(chunk)
    return deleted


def recreate_edge_table(conn: kuzu.Connection, rel: str) -> None:
    """DROP and CREATE one rel table, from the ONTOLOGY's own DDL.

    Lives here because every statement against the graph does — the
    process-wide lock is only enforceable while `kuzu_store` is the single
    door, and `test_every_graph_write_in_the_codebase_takes_the_lock` refuses
    a `conn.execute` anywhere else.

    DESTRUCTIVE, and the caller is expected to know why it is safe for the
    table it names. `scripts/rebuild_affected.py` is the one caller: AFFECTED
    holds measurements that are a deterministic function of the panel and the
    event-study code, with the watermark kept outside the graph.
    """
    spec = ontology.edges().get(rel)
    if spec is None:
        raise ontology.OntologyError(f"{rel!r} is not an edge table.")
    # `DROP TABLE`, not `DROP REL TABLE`: Kuzu 0.11 has one DROP for node and
    # rel tables and rejects the qualified form with a parser error — which is
    # how the first version of this function would have died on its first
    # statement in production. Caught by `test_rebuild.py` on a fixture graph.
    with ACCESS.write():
        conn.execute(f"DROP TABLE {rel}")
    with ACCESS.write():
        conn.execute(spec.ddl())


def check_provenance(conn: kuzu.Connection) -> list[str]:
    """THE BACKSTOP (build-spec section 17): every sourced edge's source_id
    resolves to a Source that exists. Returns violations; ingest fails on any.

    The validator makes violations unwritable through `merge_edges`; this
    catches any path that bypassed it, and a source_id that points nowhere.
    """
    problems: list[str] = []
    for rel in ontology.sourced_edges():
        rows = query(
            conn,
            f"MATCH ()-[r:{rel}]->() WHERE r.source_id IS NULL OR r.source_id = '' "
            "RETURN count(*) AS n",
        )
        missing = rows[0]["n"] if rows else 0
        if missing:
            problems.append(f"{rel}: {missing} edge(s) with no source_id")

        cited = {
            row["sid"]
            for row in query(
                conn,
                f"MATCH ()-[r:{rel}]->() WHERE r.source_id IS NOT NULL AND r.source_id <> '' "
                "RETURN DISTINCT r.source_id AS sid",
            )
        }
        if cited:
            known = {
                row["node_id"]
                for row in query(
                    conn,
                    "MATCH (s:Source) WHERE s.node_id IN $ids RETURN s.node_id AS node_id",
                    {"ids": sorted(cited)},
                )
            }
            for orphan in sorted(cited - known):
                problems.append(f"{rel}: source_id {orphan!r} resolves to no Source node")
    return problems
