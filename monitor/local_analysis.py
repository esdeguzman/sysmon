"""Built-in rules, for when Claude is not in the loop.

Deliberately less confident than Claude's advice, because it is working
from strictly less information. It can see that an app has used no CPU
for fifteen minutes. It cannot see that the app is your IDE with an
unsaved file in it, or that forty-five Chrome processes are the tabs you
are in the middle of reading.

So the rules find candidates freely and suggest conservatively: anything
in CAUTION_NAMES, or with a large process tree, is reported but never
pre-ticked. The user can still tick it by hand. The rule just does not
get to make that call on their behalf.
"""

from monitor.constants import (
    CAUTION_NAMES,
    GROWTH_MIN_SECONDS,
    LOCAL_MANY_PROCESSES,
    LOCAL_MIN_CANDIDATE_BYTES,
    UPTIME_NAG_DAYS,
)
from monitor.viewmodel import format_bytes, format_idle


_NETWORK_HINT = ("vpn", "warp", "tailscale", "wireguard", "openvpn")
_DEV_HINT = ("node", "cypress", "java", "python", "dotnet", "docker", "gradle",
             "mysqld", "postgres")


def _caution(group):
    """Why this must not be pre-ticked, or empty if it may be."""
    name = (group.get("name") or "").lower()
    if name in CAUTION_NAMES:
        if any(h in name for h in _NETWORK_HINT):
            return "network or VPN, closing it drops connectivity"
        if any(h in name for h in _DEV_HINT):
            return "developer process, could be a running server or suite"
        if name == "msedgewebview2.exe":
            return "hosts another app's window"
        return "editor or terminal, may hold unsaved work"
    if group.get("count", 1) >= LOCAL_MANY_PROCESSES:
        return f"{group['count']} processes, likely an open working set"
    return ""


def find_candidates(groups, window_seconds):
    """Groups worth mentioning, each marked suggest or not.

    Nothing is judged until the window is long enough to mean something.
    Before that, every process looks idle because nothing has had time to
    do anything.
    """
    if window_seconds < GROWTH_MIN_SECONDS:
        return []

    candidates = []
    for group in groups:
        if not group.get("killable", True):
            continue
        if group.get("rss", 0) < LOCAL_MIN_CANDIDATE_BYTES:
            continue

        idle = group.get("idle_seconds", 0)
        growing = bool(group.get("growing"))
        fully_idle = idle >= window_seconds

        if not fully_idle and not growing:
            continue

        reasons = []
        if fully_idle:
            reasons.append(f"idle {format_idle(idle)}")
        if growing:
            reasons.append(f"growing {group.get('rss_trend', 0):.1f} MB/min")

        caution = _caution(group)
        candidates.append(
            {
                "name": group["name"],
                "rss": group.get("rss", 0),
                "count": group.get("count", 1),
                "reason": ", ".join(reasons),
                "caution": caution,
                "suggest": not caution,
            }
        )

    candidates.sort(key=lambda c: (not c["suggest"], -c["rss"]))
    return candidates


def suggested_names(candidates):
    """Only what the rules are willing to pre-tick."""
    return {c["name"] for c in candidates if c["suggest"]}


def build_local_advice(groups, window_seconds, totals, uptime_days):
    """Produce something shaped like Claude's advice, labelled as local.

    `urgency` is always empty. That field bypasses every threshold, which
    is authority a rule has not earned.
    """
    candidates = find_candidates(groups, window_seconds)
    suggested = [c for c in candidates if c["suggest"]]
    cautioned = [c for c in candidates if not c["suggest"]]
    reclaimable = sum(c["rss"] for c in suggested)

    if window_seconds < GROWTH_MIN_SECONDS:
        summary = (
            f"Still watching. {format_idle(window_seconds)} of history so far, "
            f"and it takes {format_idle(GROWTH_MIN_SECONDS)} before idle and "
            "growth mean anything."
        )
    elif suggested:
        names = ", ".join(c["name"] for c in suggested[:3])
        summary = (
            f"{format_bytes(reclaimable)} looks reclaimable: {names}"
            f"{' and more' if len(suggested) > 3 else ''}. "
            f"RAM is at {totals.get('ram_percent', 0):.0f} percent."
        )
    else:
        summary = (
            f"Found nothing clearly worth closing. RAM is at "
            f"{totals.get('ram_percent', 0):.0f} percent."
        )

    notes = []
    if cautioned:
        for entry in cautioned[:3]:
            notes.append(
                f"{entry['name']} ({format_bytes(entry['rss'])}) "
                f"{entry['reason']}, left unticked: {entry['caution']}."
            )
    if uptime_days >= UPTIME_NAG_DAYS:
        notes.append(
            f"Uptime is {uptime_days:.0f} days. A restart usually reclaims more "
            "than any of this."
        )
    notes.append("These are rules, not judgement. Ask Claude for a real look.")

    return {
        "available": True,
        "source": "local",
        "summary": summary,
        "notes": " ".join(notes),
        "targets": [],  # the app ticks by name; rules do not name PIDs
        "suggested_names": suggested_names(candidates),
        "candidates": candidates,
        "reclaimable": reclaimable,
        "urgency": "",
        "written_at": "",
        "error": None,
    }
