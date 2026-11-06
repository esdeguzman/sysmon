"""Pure derived facts over sampled process data.

No I/O, no psutil, no globals. Everything here takes data and returns data,
which is what makes it testable without a running machine.
"""

from collections import defaultdict

from monitor.constants import (
    GROWTH_MB_PER_MIN,
    GROWTH_MIN_SECONDS,
    IDLE_CPU_THRESHOLD,
)

_BYTES_PER_MB = 1024 * 1024


def idle_seconds(series, interval):
    """Seconds since the last sample where CPU was above the idle threshold.

    A series that was never busy returns the full window, which is the
    honest answer: idle for at least as long as we have been watching.
    """
    if not series:
        return 0.0
    for offset, entry in enumerate(reversed(series)):
        if entry.get("cpu_percent", 0.0) > IDLE_CPU_THRESHOLD:
            return offset * interval
    return len(series) * interval


def rss_trend_mb_per_min(series, interval):
    """Least-squares slope of RSS, in MB per minute."""
    n = len(series)
    if n < 2:
        return 0.0

    mean_x = (n - 1) / 2.0
    ys = [entry.get("rss", 0) for entry in series]
    mean_y = sum(ys) / n

    numerator = sum((i - mean_x) * (y - mean_y) for i, y in enumerate(ys))
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    if denominator == 0:
        return 0.0

    bytes_per_sample = numerator / denominator
    return (bytes_per_sample / _BYTES_PER_MB) * (60.0 / interval)


def is_growing(series, interval):
    """True when RSS has climbed steadily for long enough to mean something.

    The duration floor matters. Any app allocates fast during its first few
    seconds, and flagging that as a leak would make the signal useless.
    """
    if len(series) * interval < GROWTH_MIN_SECONDS:
        return False
    return rss_trend_mb_per_min(series, interval) > GROWTH_MB_PER_MIN


def group_processes(sample):
    """One row per executable, summed. Chrome is a dozen processes."""
    buckets = defaultdict(
        lambda: {"rss": 0, "cpu_percent": 0.0, "pids": [], "members": []}
    )
    for proc in sample:
        key = proc.get("name", "").lower()
        bucket = buckets[key]
        bucket["rss"] += proc.get("rss", 0)
        bucket["cpu_percent"] += proc.get("cpu_percent", 0.0)
        bucket["pids"].append(proc.get("pid"))
        bucket["members"].append(proc)

    groups = []
    for name, bucket in buckets.items():
        groups.append(
            {
                "name": name,
                "rss": bucket["rss"],
                "cpu_percent": round(bucket["cpu_percent"], 1),
                "count": len(bucket["pids"]),
                "pids": bucket["pids"],
                "members": bucket["members"],
            }
        )
    groups.sort(key=lambda g: g["rss"], reverse=True)
    return groups


def build_facts(buffer, totals, interval):
    """Combine the newest sample with per-group history from the whole buffer."""
    if not buffer:
        return {
            "groups": [],
            "totals": totals,
            "sample_count": 0,
            "window_seconds": 0.0,
        }

    latest = buffer[-1]
    groups = group_processes(latest)

    # Each sample is aggregated by name ONCE, then every group indexes into
    # the result. Scanning the buffer per group instead is
    # O(groups x samples x processes): 5.5 seconds at a full 15 minute
    # buffer on 345 processes, which froze the 2 second UI refresh.
    per_sample = []
    for sample in buffer:
        totals_by_name = {}
        for proc in sample:
            key = proc.get("name", "").lower()
            entry = totals_by_name.get(key)
            if entry is None:
                totals_by_name[key] = [proc.get("rss", 0), proc.get("cpu_percent", 0.0)]
            else:
                entry[0] += proc.get("rss", 0)
                entry[1] += proc.get("cpu_percent", 0.0)
        per_sample.append(totals_by_name)

    absent = (0, 0.0)
    for group in groups:
        name = group["name"]
        history = []
        for totals_by_name in per_sample:
            rss, cpu = totals_by_name.get(name, absent)
            history.append({"rss": rss, "cpu_percent": cpu})
        group["idle_seconds"] = idle_seconds(history, interval)
        group["rss_trend"] = round(rss_trend_mb_per_min(history, interval), 2)
        group["growing"] = is_growing(history, interval)

    return {
        "groups": groups,
        "totals": totals,
        "sample_count": len(buffer),
        "window_seconds": len(buffer) * interval,
    }
