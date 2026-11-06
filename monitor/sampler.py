"""Samples the process table on a daemon thread into a ring buffer.

The persistent `_procs` cache is load-bearing. psutil reports cpu_percent
as the change since the previous call on the same Process object, so
rebuilding those objects each pass would report 0.0 forever and idle
detection would never fire.
"""

import json
import os
import threading
import time
from collections import deque

import psutil

from monitor import analysis
from monitor.constants import (
    BUFFER_DEPTH,
    DATA_DIR,
    SAMPLE_INTERVAL,
    SNAPSHOT_PATH,
    SNAPSHOT_PROCESS_ROWS,
)

_FIELDS = ["pid", "name", "memory_info", "cpu_percent", "create_time", "username"]


class Sampler:
    def __init__(self, interval=SAMPLE_INTERVAL, depth=BUFFER_DEPTH):
        self.interval = interval
        self._buffer = deque(maxlen=depth)
        self._procs = {}
        self._totals = {}
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
        seen = set()
        rows = []
        for proc in psutil.process_iter(_FIELDS):
            try:
                info = proc.info
                pid = info["pid"]
                seen.add(pid)
                cached = self._procs.get(pid)
                if cached is None or cached.create_time() != info["create_time"]:
                    cached = proc
                    self._procs[pid] = proc
                    cpu = 0.0
                else:
                    cpu = cached.cpu_percent(None)
                mem = info["memory_info"]
                rows.append(
                    {
                        "pid": pid,
                        "name": info["name"] or "",
                        "rss": mem.rss if mem else 0,
                        "cpu_percent": round(cpu, 1),
                        "create_time": info["create_time"],
                        "username": info["username"] or "",
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
            self._buffer.append(rows)
            self._totals = totals

        self._write_snapshot(rows, totals)
        return rows

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
        with self._lock:
            return list(self._buffer[-1]) if self._buffer else []

    def buffer_list(self):
        with self._lock:
            return [list(sample) for sample in self._buffer]

    def totals(self):
        with self._lock:
            return dict(self._totals)

    def facts(self):
        facts = analysis.build_facts(self.buffer_list(), self.totals(), self.interval)
        # members carry no information the UI needs and triple the payload
        for group in facts["groups"]:
            group.pop("members", None)
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
