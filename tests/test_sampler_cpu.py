"""CPU must be reported from the second sample, not the third.

The sampler computes cpu_percent from deltas in bulk CPU times rather
than psutil's per-process handle, because that handle cost 764 ms per
pass and froze the UI. The first version returned early when elapsed was
0 on the very first pass, which skipped recording the baseline, so no
process reported CPU until the third sample. Everything read as idle,
and idle is what the whole suggestion engine runs on.
"""

import time

from monitor.sampler import Sampler


def busy_pids(rows):
    return [r for r in rows if r["cpu_percent"] > 0]


class TestCpuPercent:
    def test_cpu_appears_by_the_second_sample(self):
        sampler = Sampler()
        sampler.sample()
        # burn a little CPU so there is definitely something to report
        deadline = time.monotonic() + 1.0
        total = 0
        while time.monotonic() < deadline:
            total += 1
        rows = sampler.sample()
        assert busy_pids(rows), "no process reported CPU on the second sample"

    def test_first_sample_reports_no_cpu(self):
        # a process seen once has no interval to measure across, and
        # reporting its lifetime average would be a spike that never was
        rows = Sampler().sample()
        assert not busy_pids(rows)

    def test_this_process_shows_its_own_burn(self):
        import os

        sampler = Sampler()
        sampler.sample()
        deadline = time.monotonic() + 1.0
        total = 0
        while time.monotonic() < deadline:
            total += 1
        rows = sampler.sample()
        mine = [r for r in rows if r["pid"] == os.getpid()]
        assert mine, "this process is missing from the sample"
        assert mine[0]["cpu_percent"] > 0, "a 1s busy loop reported no CPU"

    def test_cpu_never_exceeds_the_core_count(self):
        sampler = Sampler()
        sampler.sample()
        time.sleep(0.3)
        rows = sampler.sample()
        import psutil

        ceiling = 100.0 * (psutil.cpu_count(logical=True) or 1)
        assert all(r["cpu_percent"] <= ceiling for r in rows)


class TestRssMatchesPsutil:
    def test_totals_agree_with_psutil_within_one_percent(self):
        import psutil

        rows = Sampler().sample()
        fast = sum(r["rss"] for r in rows)
        slow = 0
        for proc in psutil.process_iter(["memory_info"]):
            try:
                info = proc.info["memory_info"]
                if info:
                    slow += info.rss
            except psutil.Error:
                continue
        assert abs(fast - slow) / max(slow, 1) < 0.05

    def test_every_process_has_a_memory_figure(self):
        rows = Sampler().sample()
        accessible = [r for r in rows if r["access"]]
        assert accessible
        assert sum(1 for r in accessible if r["rss"] > 0) > len(accessible) * 0.9
