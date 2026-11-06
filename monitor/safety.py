"""Kill authorization. Pure, and the unit that matters most.

The sidebar is a view with no authority. Every kill request is revalidated
here against the live snapshot, so a stale window and a hand-edited
advice.json both hit the same four checks.
"""

from monitor.constants import BLOCKED_NAMES, BLOCKED_PIDS


def protected_pids(own_pid, ancestor_pids):
    """PIDs that must never be killed, whatever anyone asks.

    The ancestor chain is what stops Optimize killing the terminal the user
    is talking to Claude through, since the monitor is started from it.
    """
    return frozenset({own_pid, *ancestor_pids, *BLOCKED_PIDS})


def check_target(target, snapshot, protected):
    """Return (allowed, reason) for one kill target."""
    pid = target.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return False, "pid is not an integer"

    if pid in protected:
        return False, f"pid {pid} is protected, it runs this tool or its session"

    create_time = target.get("create_time")
    if create_time is None:
        return False, "target has no create_time, refusing on identity grounds"

    match = None
    for proc in snapshot:
        if proc.get("pid") == pid:
            match = proc
            break
    if match is None:
        return False, f"pid {pid} is not in the current snapshot"

    if match.get("name", "").lower() in BLOCKED_NAMES:
        return False, f"{match.get('name')} is system-critical and cannot be killed"

    if match.get("create_time") != create_time:
        return False, f"pid {pid} changed since the page loaded, refresh and retry"

    return True, "ok"


def filter_targets(targets, snapshot, protected):
    """Split targets into (allowed snapshot processes, rejected with reasons)."""
    allowed = []
    rejected = []
    for target in targets:
        ok, reason = check_target(target, snapshot, protected)
        if not ok:
            rejected.append({**target, "reason": reason})
            continue
        for proc in snapshot:
            if proc.get("pid") == target.get("pid"):
                allowed.append(proc)
                break
    return allowed, rejected
