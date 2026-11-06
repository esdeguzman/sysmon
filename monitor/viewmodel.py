"""Presentation logic, kept out of the Tkinter widgets.

Everything here is pure, so the sidebar's behaviour can be tested without
opening a window. app.py does nothing but wire these results to widgets.
"""

from datetime import datetime

from monitor.constants import ADVICE_EXPIRED_SECONDS, ADVICE_STALE_SECONDS

_BYTES_PER_MB = 1048576
IDLE_REPORT_SECONDS = 300


def format_bytes(value):
    mb = value / _BYTES_PER_MB
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{round(mb):.0f} MB"


def format_idle(seconds):
    if seconds < 60:
        return f"{round(seconds)}s"
    return f"{round(seconds / 60)}m"


def process_state(group, window_seconds):
    """One word for what a group has been doing.

    `quiet` exists because the window starts at zero. For the first five
    minutes nothing can clear the idle threshold, and calling an untouched
    process `active` states the opposite of what was measured. `quiet`
    says the honest thing: no CPU since watching began, which is not yet
    long enough to call it idle.
    """
    if group.get("growing"):
        return "growing"

    idle = group.get("idle_seconds", 0)
    if idle >= IDLE_REPORT_SECONDS:
        return "idle"
    if window_seconds <= 0 or idle >= window_seconds:
        return "quiet"
    return "active"


def row_models(groups, checked_names, limit=40, window_seconds=0.0):
    """One model per sidebar row, ready to hand to a widget."""
    rows = []
    for group in groups[:limit]:
        name = group["name"]
        killable = group.get("killable", True)
        state = process_state(group, window_seconds)

        bits = []
        if group.get("count", 1) > 1:
            bits.append(f"{group['count']} procs")
        if not killable:
            bits.append("system-critical")
        elif state in ("idle", "quiet"):
            bits.append(f"{state} {format_idle(group.get('idle_seconds', 0))}")
        if group.get("growing"):
            bits.append(f"+{group.get('rss_trend', 0)} MB/min")

        rows.append(
            {
                "name": name,
                "detail": "   ".join(bits),
                "rss_text": format_bytes(group.get("rss", 0)),
                "rss": group.get("rss", 0),
                "killable": killable,
                "state": state,
                # A blocked row can never be ticked, whatever the advice says.
                "checked": killable and name in checked_names,
            }
        )
    return rows


def advice_checked_names(advice, snapshot):
    """Group names Claude's advice selects.

    Targets name a PID, but the sidebar acts on whole executables, so each
    target is resolved back to its group. A target whose process has since
    exited falls back to the name the advice recorded.
    """
    if not advice or not advice.get("available"):
        return set()

    by_pid = {proc.get("pid"): proc for proc in snapshot}
    names = set()
    for target in advice.get("targets") or []:
        match = by_pid.get(target.get("pid"))
        if match:
            names.add((match.get("name") or "").lower())
        elif target.get("name"):
            names.add(str(target["name"]).lower())
    return {n for n in names if n}


def selected_targets(snapshot, checked_names):
    """Expand ticked group names into (pid, create_time) kill targets."""
    targets = []
    for proc in snapshot:
        if (proc.get("name") or "").lower() not in checked_names:
            continue
        if proc.get("create_time") is None:
            continue
        targets.append({"pid": proc["pid"], "create_time": proc["create_time"]})
    return targets


def summarize_results(results, rejected):
    """Turn a kill run into display lines, a total, and the force offers."""
    freed = 0
    closed = 0
    refused = 0
    lines = []
    forceable = []

    for result in results:
        status = result.get("status")
        if status == "closed":
            freed += result.get("freed_bytes", 0)
            closed += 1
            lines.append(
                {
                    "status": "closed",
                    "text": f"{result.get('name')} closed",
                    "right": f"-{format_bytes(result.get('freed_bytes', 0))}",
                    "pid": result.get("pid"),
                }
            )
        else:
            if status == "refused":
                refused += 1
                forceable.append(result)
            lines.append(
                {
                    "status": status,
                    "text": f"{result.get('name')} {status}",
                    "right": "",
                    "pid": result.get("pid"),
                }
            )

    for entry in rejected:
        lines.append(
            {
                "status": "rejected",
                "text": f"{entry.get('pid')}: {entry.get('reason')}",
                "right": "",
                "pid": entry.get("pid"),
            }
        )

    return {
        "freed_bytes": freed,
        "freed_text": format_bytes(freed),
        "closed": closed,
        "refused": refused,
        "lines": lines,
        "forceable": forceable,
    }


def _parse_written_at(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def advice_age(advice, now=None):
    """How old the advice is, and whether it still counts.

    Advice is a snapshot of a moment. Shown without its age it reads as
    present tense forever, which is how a three day old file ends up
    describing RAM that has moved on and naming processes that have exited.
    Past the expiry it stops counting as advice at all, so a stale file
    cannot raise an alert.
    """
    written = _parse_written_at((advice or {}).get("written_at"))
    if written is None:
        return {"label": "age unknown", "seconds": None, "stale": True, "expired": True}

    reference = _parse_written_at(now) or datetime.now()
    seconds = (reference - written).total_seconds()

    # A future timestamp means a clock skew, not advice from the future.
    if seconds < 0:
        seconds = 0.0

    if seconds < 60:
        label = "just now"
    elif seconds < 3600:
        label = f"{int(seconds // 60)} min ago"
    elif seconds < 86400:
        hours = int(seconds // 3600)
        label = f"{hours} hour{'s' if hours != 1 else ''} ago"
    else:
        days = int(seconds // 86400)
        label = f"{days} day{'s' if days != 1 else ''} ago"

    return {
        "label": label,
        "seconds": seconds,
        "stale": seconds >= ADVICE_STALE_SECONDS,
        "expired": seconds >= ADVICE_EXPIRED_SECONDS,
    }


def dead_targets(advice, snapshot):
    """Names of advice targets whose process is no longer the one meant.

    A recycled PID counts as gone: the advice was written about a process
    that has since exited, whatever now holds that number.
    """
    if not advice:
        return []
    by_pid = {proc.get("pid"): proc for proc in snapshot}
    gone = []
    for target in advice.get("targets") or []:
        match = by_pid.get(target.get("pid"))
        name = target.get("name") or f"pid {target.get('pid')}"
        if match is None:
            gone.append(name)
        elif target.get("name") and (match.get("name") or "").lower() != str(
            target["name"]
        ).lower():
            gone.append(name)
    return gone
