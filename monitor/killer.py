"""Closes processes, waits, and reports honestly on what refused.

The graceful path shells to `taskkill` without /F on purpose.
psutil's terminate() calls TerminateProcess on Windows, which is immediate
and unconditional, so it would destroy unsaved work with no prompt.
taskkill without /F posts WM_CLOSE, which lets an app show its own save
dialog and refuse to die. That refusal is a feature here, not an error.
"""

import subprocess
import time

import psutil

from monitor.constants import GRACEFUL_WAIT_SECONDS

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run_taskkill(pid, force):
    cmd = ["taskkill", "/PID", str(int(pid))]
    if force:
        cmd.insert(1, "/F")
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_NO_WINDOW,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode == 0, output.strip()


def _gone(pid, create_time):
    """True when that exact process is no longer running.

    The create_time comparison matters: Windows recycles PIDs, so
    pid_exists alone can report a brand new process as the old one.
    """
    try:
        proc = psutil.Process(pid)
        if create_time is not None and proc.create_time() != create_time:
            return True
        return proc.status() == psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return True


def kill_targets(procs, force=False, wait=GRACEFUL_WAIT_SECONDS):
    """Close each process, then report what actually happened."""
    results = []
    issued = []

    for proc in procs:
        pid = proc.get("pid")
        create_time = proc.get("create_time")
        if _gone(pid, create_time):
            results.append(_result(proc, "closed", proc.get("rss", 0), "already gone"))
            continue
        ok, detail = _run_taskkill(pid, force)
        issued.append((proc, ok, detail))

    deadline = time.monotonic() + wait
    pending = list(issued)
    while pending and time.monotonic() < deadline:
        still = []
        for entry in pending:
            proc, ok, detail = entry
            if _gone(proc.get("pid"), proc.get("create_time")):
                results.append(
                    _result(proc, "closed", proc.get("rss", 0), "closed on request")
                )
            else:
                still.append(entry)
        pending = still
        if pending:
            time.sleep(0.25)

    for proc, ok, detail in pending:
        status = "refused" if ok else "failed"
        note = (
            "still running, it may be asking you to save"
            if ok
            else detail or "taskkill reported an error"
        )
        results.append(_result(proc, status, 0, note))

    return results


def _result(proc, status, freed, detail):
    return {
        "pid": proc.get("pid"),
        "name": proc.get("name", ""),
        "status": status,
        "freed_bytes": freed,
        "detail": detail,
    }
