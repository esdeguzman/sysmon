"""When the tool has earned the right to interrupt you.

Pure, and deliberately hard to satisfy. A monitor that flashes on every
refresh teaches you to ignore it, so the bar is both pressure and payoff:
a full machine with nothing worth closing is not actionable, and spare
capacity on a quiet machine is not urgent. Critical pressure is the one
exception and fires on its own.
"""

from monitor.constants import (
    ALERT_COOLDOWN_SECONDS,
    ALERT_RAM_PERCENT,
    ALERT_RECLAIM_BYTES,
    CRITICAL_RAM_PERCENT,
    LOCAL_ALERT_RAM_PERCENT,
    LOCAL_ALERT_RECLAIM_BYTES,
)
from monitor.viewmodel import format_bytes


def reclaimable_bytes(groups, target_names):
    """Live RSS of the groups the advice targets.

    Read from the current sample rather than the advice's own est_mb, which
    goes stale the moment an app grows or the user closes something.
    """
    total = 0
    for group in groups:
        if group.get("name") in target_names and group.get("killable", True):
            total += group.get("rss", 0)
    return total


def should_alert(advice, totals, reclaimable, expired=False, local=False):
    """Return (fire, reason) for the current conditions.

    `expired` advice cannot raise an alert. Its numbers describe a machine
    that has moved on and its targets may have exited hours ago, so acting
    on it would spend the user's attention on a stale claim.

    `local` findings come from rules rather than judgement, so they clear a
    higher bar and can never use the urgency override.

    Critical RAM is exempt from both: that is measured now, not claimed.
    """
    ram = totals.get("ram_percent") or 0.0
    has_advice = bool(advice and advice.get("available")) and not expired

    if has_advice and not local and str(advice.get("urgency") or "").lower() == "high":
        return True, f"Claude flagged this as urgent. {format_bytes(reclaimable)} reclaimable"

    if ram >= CRITICAL_RAM_PERCENT:
        return True, f"RAM {ram:.0f} percent, critical"

    if local:
        if ram >= LOCAL_ALERT_RAM_PERCENT and reclaimable >= LOCAL_ALERT_RECLAIM_BYTES:
            return (
                True,
                f"RAM {ram:.0f} percent. Built-in rules see "
                f"{format_bytes(reclaimable)} reclaimable",
            )
        return False, ""

    if has_advice and ram >= ALERT_RAM_PERCENT and reclaimable >= ALERT_RECLAIM_BYTES:
        return True, f"RAM {ram:.0f} percent. {format_bytes(reclaimable)} reclaimable"

    return False, ""


def alert_key(advice):
    """Identity of the advice an alert belongs to.

    New advice is a new reason to look, so it re-arms the alert even while
    an earlier one is still inside its cooldown.
    """
    if not advice or not advice.get("available"):
        return "none"
    return f"{advice.get('written_at', '')}|{len(advice.get('targets') or [])}"


class AlertState:
    """Edge-triggered, so a standing condition does not flash every 2 seconds.

    Fires when the condition becomes true, when the advice behind it
    changes, and once per cooldown while it persists. Focusing the window
    acknowledges it and silences that condition until one of those happens.
    """

    def __init__(self, cooldown=ALERT_COOLDOWN_SECONDS):
        self.cooldown = cooldown
        self._key = None
        self._last_fired = None

    def update(self, active, key, now):
        if not active:
            # Clearing resets the edge, so the next onset flashes again.
            self._key = None
            self._last_fired = None
            return False

        if key != self._key:
            self._key = key
            self._last_fired = now
            return True

        if self._last_fired is None or now - self._last_fired >= self.cooldown:
            self._last_fired = now
            return True

        return False

    def acknowledge(self, now):
        """The user looked. Stop nagging about this particular condition."""
        self._last_fired = now


def title_for(alerting, reclaimable):
    """Window title, which is also what a taskbar hover shows."""
    if not alerting:
        return "sysmon"
    if reclaimable <= 0:
        return "sysmon - memory pressure"
    return f"sysmon - {format_bytes(reclaimable)} reclaimable"
