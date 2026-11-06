from monitor import viewmodel as vm


def group(name, rss, count=1, idle=0.0, killable=True, growing=False, trend=0.0):
    return {
        "name": name,
        "rss": rss,
        "count": count,
        "idle_seconds": idle,
        "killable": killable,
        "growing": growing,
        "rss_trend": trend,
        "cpu_percent": 0.0,
        "pids": [1],
    }


MB = 1048576


class TestFormatBytes:
    def test_megabytes_below_a_gig(self):
        assert vm.format_bytes(500 * MB) == "500 MB"

    def test_gigabytes_above_a_gig(self):
        assert vm.format_bytes(2560 * MB) == "2.5 GB"

    def test_zero(self):
        assert vm.format_bytes(0) == "0 MB"


class TestFormatIdle:
    def test_seconds_below_a_minute(self):
        assert vm.format_idle(38.0) == "38s"

    def test_minutes_above(self):
        assert vm.format_idle(2520.0) == "42m"


class TestRowModels:
    def test_checked_reflects_the_checked_set(self):
        groups = [group("postman.exe", 100 * MB), group("chrome.exe", 900 * MB)]
        rows = vm.row_models(groups, {"postman.exe"})
        by_name = {r["name"]: r for r in rows}
        assert by_name["postman.exe"]["checked"] is True
        assert by_name["chrome.exe"]["checked"] is False

    def test_blocked_group_is_never_checked(self):
        groups = [group("lsass.exe", 10 * MB, killable=False)]
        rows = vm.row_models(groups, {"lsass.exe"})
        assert rows[0]["checked"] is False
        assert rows[0]["killable"] is False

    def test_detail_names_the_block_reason(self):
        rows = vm.row_models([group("lsass.exe", 10 * MB, killable=False)], set())
        assert "system-critical" in rows[0]["detail"]

    def test_detail_shows_process_count_and_idle(self):
        rows = vm.row_models([group("chrome.exe", 10 * MB, count=44, idle=600.0)], set())
        assert "44 procs" in rows[0]["detail"]
        assert "idle 10m" in rows[0]["detail"]

    def test_short_idle_is_not_reported(self):
        rows = vm.row_models([group("a.exe", 10 * MB, idle=30.0)], set())
        assert "idle" not in rows[0]["detail"]

    def test_growing_is_reported(self):
        rows = vm.row_models([group("a.exe", 10 * MB, growing=True, trend=12.5)], set())
        assert "+12.5 MB/min" in rows[0]["detail"]

    def test_limit_caps_the_row_count(self):
        groups = [group(f"p{i}.exe", (100 - i) * MB) for i in range(60)]
        assert len(vm.row_models(groups, set(), limit=40)) == 40


class TestAdviceCheckedNames:
    def test_maps_target_pid_to_its_group_name(self):
        snapshot = [{"pid": 10, "name": "Postman.exe", "create_time": 1.0}]
        advice = {"available": True, "targets": [{"pid": 10}]}
        assert vm.advice_checked_names(advice, snapshot) == {"postman.exe"}

    def test_falls_back_to_the_target_name_when_the_pid_is_gone(self):
        advice = {"available": True, "targets": [{"pid": 999, "name": "Teams.exe"}]}
        assert vm.advice_checked_names(advice, []) == {"teams.exe"}

    def test_unavailable_advice_selects_nothing(self):
        assert vm.advice_checked_names({"available": False, "targets": []}, []) == set()

    def test_none_advice_selects_nothing(self):
        assert vm.advice_checked_names(None, []) == set()


class TestSelectedTargets:
    def test_expands_a_name_to_every_matching_pid(self):
        snapshot = [
            {"pid": 1, "name": "chrome.exe", "create_time": 1.0},
            {"pid": 2, "name": "chrome.exe", "create_time": 2.0},
            {"pid": 3, "name": "code.exe", "create_time": 3.0},
        ]
        targets = vm.selected_targets(snapshot, {"chrome.exe"})
        assert sorted(t["pid"] for t in targets) == [1, 2]
        assert all("create_time" in t for t in targets)

    def test_skips_processes_with_no_create_time(self):
        snapshot = [{"pid": 1, "name": "x.exe", "create_time": None}]
        assert vm.selected_targets(snapshot, {"x.exe"}) == []

    def test_empty_selection_yields_nothing(self):
        assert vm.selected_targets([{"pid": 1, "name": "a.exe", "create_time": 1.0}], set()) == []


class TestSummarizeResults:
    def test_totals_only_closed_processes(self):
        results = [
            {"pid": 1, "name": "a.exe", "status": "closed", "freed_bytes": 100 * MB},
            {"pid": 2, "name": "b.exe", "status": "refused", "freed_bytes": 0},
        ]
        summary = vm.summarize_results(results, [])
        assert summary["freed_bytes"] == 100 * MB
        assert summary["closed"] == 1
        assert summary["refused"] == 1

    def test_refused_entries_are_offered_for_force(self):
        results = [{"pid": 2, "name": "b.exe", "status": "refused", "freed_bytes": 0}]
        summary = vm.summarize_results(results, [])
        assert [r["pid"] for r in summary["forceable"]] == [2]

    def test_rejected_entries_become_lines(self):
        summary = vm.summarize_results([], [{"pid": 3, "reason": "system-critical"}])
        assert any("system-critical" in line["text"] for line in summary["lines"])

    def test_empty_input_is_handled(self):
        summary = vm.summarize_results([], [])
        assert summary["freed_bytes"] == 0
        assert summary["lines"] == []


class TestAnnotateKillable:
    """annotate_killable lives in app.py but is pure, so it is tested here."""

    def test_system_names_are_not_killable(self):
        from monitor.app import annotate_killable

        groups = annotate_killable(
            [group("lsass.exe", 10 * MB), group("chrome.exe", 10 * MB)]
        )
        by_name = {g["name"]: g for g in groups}
        assert by_name["lsass.exe"]["killable"] is False
        assert by_name["chrome.exe"]["killable"] is True

    def test_claude_is_not_killable(self):
        from monitor.app import annotate_killable

        assert annotate_killable([group("claude.exe", 10 * MB)])[0]["killable"] is False

    def test_a_group_holding_a_blocked_pid_is_not_killable(self):
        from monitor.app import annotate_killable

        g = group("weird.exe", 10 * MB)
        g["pids"] = [4]
        assert annotate_killable([g])[0]["killable"] is False


class TestProcessState:
    """The window starts at 0s, so short windows must not claim 'active'."""

    def test_growing_wins_over_everything(self):
        g = group("a.exe", MB, idle=9999.0, growing=True)
        assert vm.process_state(g, 9999.0) == "growing"

    def test_long_idle_is_idle(self):
        assert vm.process_state(group("a.exe", MB, idle=600.0), 900.0) == "idle"

    def test_never_busy_in_a_short_window_is_quiet_not_active(self):
        # the bug this replaces: 42s idle in a 42s window rendered "active"
        g = group("a.exe", MB, idle=42.0)
        assert vm.process_state(g, 42.0) == "quiet"

    def test_recently_busy_in_a_short_window_is_active(self):
        g = group("a.exe", MB, idle=4.0)
        assert vm.process_state(g, 42.0) == "active"

    def test_zero_window_is_quiet_rather_than_active(self):
        assert vm.process_state(group("a.exe", MB, idle=0.0), 0.0) == "quiet"


class TestRowDetailIdle:
    def test_quiet_is_reported_in_the_detail_line(self):
        rows = vm.row_models([group("a.exe", MB, idle=42.0)], set(), window_seconds=42.0)
        assert "quiet 42s" in rows[0]["detail"]

    def test_active_group_gets_no_idle_text(self):
        rows = vm.row_models([group("a.exe", MB, idle=4.0)], set(), window_seconds=42.0)
        assert "quiet" not in rows[0]["detail"]
        assert "idle" not in rows[0]["detail"]
