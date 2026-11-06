"""Samples the process table on a daemon thread into a ring buffer.

The persistent `_procs` cache must survive between passes. psutil reports
cpu_percent as the change since the previous call on the same Process
object, so rebuilding those objects each pass returns 0.0 every time and
idle detection never fires.
"""

import json
import os
import threading
import time
from collections import deque

import psutil

from monitor import analysis, winproc
from monitor.constants import (
    BUFFER_DEPTH,
    DATA_DIR,
    SAMPLE_INTERVAL,
    SNAPSHOT_PATH,
    SNAPSHOT_PROCESS_ROWS,
)

_FIELDS = ["pid", "name", "memory_info", "create_time"]
# What the fast path needs. Measured at 2.3 ms against 764 ms once
# memory_info is included, across 347 processes.
_CORES = psutil.cpu_count(logical=True) or 1
_CHEAP_FIELDS = ["pid", "name", "create_time"]


class Sampler:
    def __init__(self, interval=SAMPLE_INTERVAL, depth=BUFFER_DEPTH):
        self.interval = interval
        # History holds per-name aggregates, not raw rows. Keeping full
        # rows for all 450 samples cost 45 MB on a 346-process machine;
        # the aggregates cost 5.6. Only the newest sample needs pids and
        # create_times, and that is _latest.
        self._buffer = deque(maxlen=depth)
        self._latest = []
        self._procs = {}
        self._totals = {}
        self._cpu_prev = {}
        self._last_cpu_at = None
        self._facts = None  # computed once per sample, read by the UI
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self.sample()  # prime cpu_percent and give the first request data
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.sample()
            except Exception:
                # A sampling failure must not kill the thread. The next
                # pass will almost always succeed.
                continue

    def sample(self):
        """One pass over the process table.

        Identity comes from psutil, which is cheap for pid, name and
        create_time. Memory and CPU come from a single bulk syscall,
        because psutil opens a handle per process for those two and that
        cost 764 ms per pass, freezing the UI. See monitor/winproc.py for
        why identity must NOT move to the same source.
        """
        bulk = winproc.memory_and_cpu()
        now = time.monotonic()
        elapsed = now - (self._last_cpu_at or now)
        self._last_cpu_at = now

        seen = set()
        rows = []
        fields = _FIELDS if not bulk else _CHEAP_FIELDS
        for proc in psutil.process_iter(fields):
            try:
                info = proc.info
                pid = info["pid"]
                seen.add(pid)
                create_time = info["create_time"]

                if bulk:
                    stats = bulk.get(pid)
                    rss = stats["rss"] if stats else 0
                    cpu = self._cpu_percent(pid, create_time, stats, elapsed)
                else:
                    # Fallback path: the syscall is unavailable or failed.
                    mem = info.get("memory_info")
                    rss = mem.rss if mem else 0
                    cached = self._procs.get(pid)
                    if cached is None or cached.create_time() != create_time:
                        self._procs[pid] = proc
                        cpu = 0.0
                    else:
                        cpu = cached.cpu_percent(None)

                rows.append(
                    {
                        "pid": pid,
                        "name": info["name"] or "",
                        "rss": rss,
                        "cpu_percent": round(cpu, 1),
                        "create_time": create_time,
                        "username": "",
                        "access": True,
                    }
                )
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                rows.append(
                    {
                        "pid": proc.pid,
                        "name": _safe_name(proc),
                        "rss": 0,
                        "cpu_percent": 0.0,
                        "create_time": None,
                        "username": "",
                        "access": False,
                    }
                )
            except Exception:
                continue

        for pid in list(self._procs):
            if pid not in seen:
                del self._procs[pid]
        for pid in list(self._cpu_prev):
            if pid not in seen:
                del self._cpu_prev[pid]

        vm = psutil.virtual_memory()
        totals = {
            "ram_total": vm.total,
            "ram_used": vm.used,
            "ram_available": vm.available,
            "ram_percent": vm.percent,
            "cpu_percent": psutil.cpu_percent(None),
            "sampled_at": time.time(),
        }

        with self._lock:
            self._buffer.append(analysis.aggregate_sample(rows))
            self._latest = rows
            self._totals = totals
            self._facts = None  # stale the instant new data lands

        self._build_facts()
        self._write_snapshot(rows, totals)
        return rows


    def _cpu_percent(self, pid, create_time, stats, elapsed):
        """Percent of one core since the previous sample.

        psutil's cpu_percent does this internally off a per-process
        handle. The same arithmetic over bulk CPU times costs nothing.
        A process seen for the first time reports 0.0 rather than its
        lifetime average, which would read as a spike that never happened.
        """
        if not stats:
            return 0.0
        previous = self._cpu_prev.get(pid)
        # Record the baseline FIRST, always. Returning early on the very
        # first pass (where elapsed is 0) skipped this, so no baseline
        # existed and CPU stayed at zero until the third sample. Idle
        # detection reads cpu_percent, so everything looked idle forever.
        self._cpu_prev[pid] = (create_time, stats["cpu_ns"])
        if elapsed <= 0 or previous is None or previous[0] != create_time:
            return 0.0
        delta_ns = stats["cpu_ns"] - previous[1]
        if delta_ns <= 0:
            return 0.0
        return min((delta_ns / (elapsed * 1e9)) * 100.0, 100.0 * _CORES)

    def _write_snapshot(self, rows, totals):
        """Atomic write, so Claude never reads a half-written file.

        `processes` is here because a headless `claude -p` run has Read and
        Write only. Without per-process rows it cannot learn any
        create_time, and the advice contract requires one per target. The
        first live run copied a stale create_time from the previous advice
        file, which the PID reuse guard rejects the moment that process has
        restarted.

        Rows without a create_time are excluded: those are access-denied
        processes, which can never be targets anyway.
        """
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        targetable = [r for r in rows if r.get("create_time") is not None]
        targetable.sort(key=lambda r: r.get("rss", 0), reverse=True)
        payload = {
            "sampled_at": totals["sampled_at"],
            "totals": totals,
            "facts": self.facts(),
            "processes": [
                {
                    "pid": r["pid"],
                    "name": r["name"],
                    "rss": r["rss"],
                    "create_time": r["create_time"],
                    "cpu_percent": r["cpu_percent"],
                }
                for r in targetable[:SNAPSHOT_PROCESS_ROWS]
            ],
        }
        tmp = SNAPSHOT_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, SNAPSHOT_PATH)

    def latest(self):
        """The newest sample's full rows, with pids and create_times."""
        with self._lock:
            return list(self._latest)

    def history(self):
        """Per-name aggregates, oldest first. No per-process detail."""
        with self._lock:
            return list(self._buffer)

    def totals(self):
        with self._lock:
            return dict(self._totals)

    def facts(self):
        """The cached result of the last sample.

        Computed once per sample on the sampler thread. It used to be
        recomputed by the UI on every refresh as well, which cost the Tk
        main thread 55 ms of its 102 ms refresh for a second copy of
        numbers that already existed.
        """
        with self._lock:
            if self._facts is not None:
                return self._facts
        return self._build_facts()

    def _build_facts(self):
        facts = analysis.build_facts(
            self.history(), self.latest(), self.totals(), self.interval
        )
        # members carry no information the UI needs and triple the payload
        for group in facts["groups"]:
            group.pop("members", None)
        with self._lock:
            self._facts = facts
        return facts

    def own_context(self):
        """Our PID and every ancestor, for the self-protection check."""
        me = psutil.Process()
        ancestors = []
        for parent in me.parents():
            try:
                ancestors.append(parent.pid)
            except psutil.Error:
                continue
        return me.pid, ancestors


def _safe_name(proc):
    try:
        return proc.name()
    except psutil.Error:
        return f"pid {proc.pid}"
