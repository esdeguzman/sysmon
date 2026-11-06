import pytest

from monitor import analysis
from monitor.constants import SAMPLE_INTERVAL


def series(cpus=None, rsses=None, n=None):
    """Build a fake per-process series. cpus and rsses are parallel lists."""
    n = n or len(cpus or rsses)
    cpus = cpus if cpus is not None else [0.0] * n
    rsses = rsses if rsses is not None else [100 * 1024 * 1024] * n
    return [{"cpu_percent": c, "rss": r} for c, r in zip(cpus, rsses)]


class TestIdleSeconds:
    def test_all_idle_returns_full_window(self):
        s = series(cpus=[0.0] * 10)
        assert analysis.idle_seconds(s, 2.0) == 20.0

    def test_busy_at_end_returns_zero(self):
        s = series(cpus=[0.0] * 9 + [50.0])
        assert analysis.idle_seconds(s, 2.0) == 0.0

    def test_busy_in_middle_counts_from_there(self):
        # index 5 is the last busy sample, 4 samples follow it
        s = series(cpus=[0.0] * 5 + [50.0] + [0.0] * 4)
        assert analysis.idle_seconds(s, 2.0) == 8.0

    def test_at_threshold_is_still_idle(self):
        # threshold is "> 1.0", so exactly 1.0 counts as idle
        s = series(cpus=[1.0] * 5)
        assert analysis.idle_seconds(s, 2.0) == 10.0

    def test_empty_series_returns_zero(self):
        assert analysis.idle_seconds([], 2.0) == 0.0


class TestRssTrend:
    def test_flat_series_has_zero_trend(self):
        s = series(rsses=[100 * 1024 * 1024] * 10)
        assert analysis.rss_trend_mb_per_min(s, 2.0) == pytest.approx(0.0, abs=1e-6)

    def test_growing_series_has_positive_trend(self):
        # +1 MB per sample, 2s apart, is +30 MB/min
        mb = 1024 * 1024
        s = series(rsses=[(100 + i) * mb for i in range(10)])
        assert analysis.rss_trend_mb_per_min(s, 2.0) == pytest.approx(30.0, rel=1e-3)

    def test_shrinking_series_has_negative_trend(self):
        mb = 1024 * 1024
        s = series(rsses=[(100 - i) * mb for i in range(10)])
        assert analysis.rss_trend_mb_per_min(s, 2.0) < 0

    def test_single_sample_has_zero_trend(self):
        assert analysis.rss_trend_mb_per_min(series(n=1), 2.0) == 0.0

    def test_empty_series_has_zero_trend(self):
        assert analysis.rss_trend_mb_per_min([], 2.0) == 0.0


class TestIsGrowing:
    def test_short_window_never_flags(self):
        # 10 samples at 2s is 20s, far below GROWTH_MIN_SECONDS
        mb = 1024 * 1024
        s = series(rsses=[(100 + 10 * i) * mb for i in range(10)])
        assert analysis.is_growing(s, 2.0) is False

    def test_long_steady_growth_flags(self):
        # 200 samples at 2s is 400s, above the 300s floor, +0.5MB/sample = 15MB/min
        mb = 1024 * 1024
        s = series(rsses=[int((100 + 0.5 * i) * mb) for i in range(200)])
        assert analysis.is_growing(s, 2.0) is True

    def test_long_flat_series_does_not_flag(self):
        mb = 1024 * 1024
        s = series(rsses=[100 * mb] * 200)
        assert analysis.is_growing(s, 2.0) is False


class TestGroupProcesses:
    def test_groups_by_name_and_sums(self):
        sample = [
            {"pid": 1, "name": "chrome.exe", "rss": 100, "cpu_percent": 1.0},
            {"pid": 2, "name": "chrome.exe", "rss": 200, "cpu_percent": 2.0},
            {"pid": 3, "name": "code.exe", "rss": 50, "cpu_percent": 0.0},
        ]
        groups = analysis.group_processes(sample)
        assert len(groups) == 2
        chrome = groups[0]
        assert chrome["name"] == "chrome.exe"
        assert chrome["rss"] == 300
        assert chrome["cpu_percent"] == 3.0
        assert chrome["count"] == 2
        assert sorted(chrome["pids"]) == [1, 2]

    def test_sorted_by_rss_descending(self):
        sample = [
            {"pid": 1, "name": "small.exe", "rss": 10, "cpu_percent": 0.0},
            {"pid": 2, "name": "big.exe", "rss": 900, "cpu_percent": 0.0},
        ]
        names = [g["name"] for g in analysis.group_processes(sample)]
        assert names == ["big.exe", "small.exe"]

    def test_name_matching_is_case_insensitive(self):
        sample = [
            {"pid": 1, "name": "Chrome.exe", "rss": 10, "cpu_percent": 0.0},
            {"pid": 2, "name": "chrome.exe", "rss": 10, "cpu_percent": 0.0},
        ]
        assert len(analysis.group_processes(sample)) == 1

    def test_empty_sample_returns_empty(self):
        assert analysis.group_processes([]) == []


class TestBuildFacts:
    def test_empty_buffer_returns_empty_groups(self):
        facts = analysis.build_facts([], {"percent": 50.0}, SAMPLE_INTERVAL)
        assert facts["groups"] == []
        assert facts["sample_count"] == 0

    def test_facts_attach_idle_and_trend_per_group(self):
        s1 = [{"pid": 1, "name": "a.exe", "rss": 100, "cpu_percent": 0.0}]
        s2 = [{"pid": 1, "name": "a.exe", "rss": 200, "cpu_percent": 0.0}]
        facts = analysis.build_facts([s1, s2], {"percent": 50.0}, 2.0)
        group = facts["groups"][0]
        assert group["idle_seconds"] == 4.0
        assert "rss_trend" in group
        assert group["growing"] is False
        assert facts["sample_count"] == 2
        assert facts["window_seconds"] == 4.0

    def test_latest_sample_drives_group_membership(self):
        # a process present only in an older sample must not appear
        s1 = [{"pid": 1, "name": "gone.exe", "rss": 100, "cpu_percent": 0.0}]
        s2 = [{"pid": 2, "name": "here.exe", "rss": 100, "cpu_percent": 0.0}]
        facts = analysis.build_facts([s1, s2], {}, 2.0)
        assert [g["name"] for g in facts["groups"]] == ["here.exe"]


class TestBuildFactsScales:
    """build_facts was O(groups x samples x processes): 5.5s at full buffer.

    The window refreshes every 2s on the Tk main thread, so that froze the
    UI once the 15 minute buffer filled. These guard the complexity, not a
    wall-clock target, so the bound is deliberately loose.
    """

    def _buffer(self, depth, procs, groups):
        sample = [
            {
                "pid": i,
                "name": f"app{i % groups}.exe",
                "rss": 1000 + i,
                "cpu_percent": 0.0,
            }
            for i in range(procs)
        ]
        return [sample] * depth

    def test_full_buffer_is_fast(self):
        import time

        buf = self._buffer(depth=450, procs=345, groups=103)
        start = time.perf_counter()
        facts = analysis.build_facts(buf, {}, 2.0)
        elapsed = time.perf_counter() - start
        assert len(facts["groups"]) == 103
        assert elapsed < 1.0, f"build_facts took {elapsed:.2f}s at full buffer depth"

    def test_history_still_sums_across_a_groups_processes(self):
        # the fast path must not lose the per-group aggregation
        s1 = [
            {"pid": 1, "name": "a.exe", "rss": 100, "cpu_percent": 1.0},
            {"pid": 2, "name": "a.exe", "rss": 200, "cpu_percent": 2.0},
        ]
        facts = analysis.build_facts([s1, s1], {}, 2.0)
        assert facts["groups"][0]["rss"] == 300

    def test_a_group_absent_from_an_old_sample_reads_zero(self):
        s1 = [{"pid": 1, "name": "old.exe", "rss": 100, "cpu_percent": 0.0}]
        s2 = [{"pid": 2, "name": "new.exe", "rss": 500, "cpu_percent": 0.0}]
        facts = analysis.build_facts([s1, s2], {}, 2.0)
        group = facts["groups"][0]
        assert group["name"] == "new.exe"
        # rss went 0 -> 500 across the window, so the trend must be positive
        assert group["rss_trend"] > 0
