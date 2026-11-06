from monitor import local_analysis as la

MB = 1048576
GB = 1024 * MB


def group(name, rss_mb, idle=900.0, count=1, growing=False, trend=0.0, killable=True):
    return {
        "name": name,
        "rss": int(rss_mb * MB),
        "idle_seconds": idle,
        "count": count,
        "growing": growing,
        "rss_trend": trend,
        "killable": killable,
        "cpu_percent": 0.0,
        "pids": [1],
    }


FULL_WINDOW = 900.0


class TestCandidates:
    def test_idle_for_the_full_window_is_a_candidate(self):
        found = la.find_candidates([group("asana.exe", 1300)], FULL_WINDOW)
        assert [c["name"] for c in found] == ["asana.exe"]
        assert found[0]["suggest"] is True
        assert "idle" in found[0]["reason"]

    def test_a_blocked_group_is_never_a_candidate(self):
        found = la.find_candidates(
            [group("memcompression", 3300, growing=True, trend=31.0, killable=False)],
            FULL_WINDOW,
        )
        assert found == []

    def test_small_apps_are_ignored(self):
        # closing a 50 MB process is noise, not optimisation
        assert la.find_candidates([group("tiny.exe", 50)], FULL_WINDOW) == []

    def test_a_recently_used_app_is_not_a_candidate(self):
        assert la.find_candidates([group("busy.exe", 2000, idle=4.0)], FULL_WINDOW) == []

    def test_a_leaking_app_is_a_candidate_even_while_in_use(self):
        found = la.find_candidates(
            [group("leaky.exe", 1000, idle=0.0, growing=True, trend=12.0)], FULL_WINDOW
        )
        assert len(found) == 1
        assert "12.0 MB/min" in found[0]["reason"]

    def test_short_window_yields_nothing(self):
        # with 20s of history nothing has been idle long enough to judge
        assert la.find_candidates([group("a.exe", 2000, idle=20.0)], 20.0) == []


class TestCautionClass:
    def test_an_editor_is_found_but_not_suggested(self):
        found = la.find_candidates([group("trae.exe", 2800)], FULL_WINDOW)
        assert len(found) == 1
        assert found[0]["suggest"] is False
        assert "unsaved" in found[0]["caution"]

    def test_a_terminal_is_not_suggested(self):
        found = la.find_candidates([group("powershell.exe", 400)], FULL_WINDOW)
        assert found[0]["suggest"] is False

    def test_caution_matching_is_case_insensitive(self):
        found = la.find_candidates([group("Code.exe", 1200)], FULL_WINDOW)
        assert found[0]["suggest"] is False

    def test_a_large_process_tree_is_not_suggested(self):
        # 45 chrome processes is an open working set, not forgotten clutter
        found = la.find_candidates(
            [group("chrome.exe", 8200, idle=574.0, count=45, growing=True, trend=10.4)],
            FULL_WINDOW,
        )
        assert found[0]["suggest"] is False
        assert "processes" in found[0]["caution"]

    def test_a_small_tree_is_still_suggested(self):
        found = la.find_candidates([group("asana.exe", 1300, count=11)], FULL_WINDOW)
        assert found[0]["suggest"] is True


class TestSuggestedNames:
    def test_only_suggested_candidates_are_returned(self):
        groups = [
            group("asana.exe", 1300),
            group("trae.exe", 2800),
            group("chrome.exe", 8200, count=45),
        ]
        found = la.find_candidates(groups, FULL_WINDOW)
        assert la.suggested_names(found) == {"asana.exe"}

    def test_nothing_suggested_is_an_empty_set(self):
        found = la.find_candidates([group("trae.exe", 2800)], FULL_WINDOW)
        assert la.suggested_names(found) == set()


class TestBuildLocalAdvice:
    def test_shape_matches_the_advice_contract(self):
        advice = la.build_local_advice(
            [group("asana.exe", 1300)], FULL_WINDOW, {"ram_percent": 70.0}, uptime_days=2.0
        )
        assert advice["available"] is True
        assert advice["source"] == "local"
        assert advice["urgency"] == ""
        assert isinstance(advice["targets"], list)
        assert advice["summary"]

    def test_summary_names_the_reclaimable_total(self):
        advice = la.build_local_advice(
            [group("asana.exe", 1300)], FULL_WINDOW, {"ram_percent": 70.0}, uptime_days=2.0
        )
        assert "1.3 GB" in advice["summary"]

    def test_long_uptime_is_reported(self):
        advice = la.build_local_advice([], FULL_WINDOW, {"ram_percent": 70.0}, uptime_days=49.0)
        assert "49 days" in advice["notes"]

    def test_short_uptime_is_not_mentioned(self):
        advice = la.build_local_advice([], FULL_WINDOW, {"ram_percent": 70.0}, uptime_days=2.0)
        assert "uptime" not in advice["notes"].lower()

    def test_nothing_found_says_so_plainly(self):
        advice = la.build_local_advice([], FULL_WINDOW, {"ram_percent": 40.0}, uptime_days=1.0)
        assert "nothing" in advice["summary"].lower()
        assert advice["targets"] == []

    def test_cautioned_apps_are_named_in_the_notes(self):
        advice = la.build_local_advice(
            [group("trae.exe", 2800)], FULL_WINDOW, {"ram_percent": 70.0}, uptime_days=1.0
        )
        assert "trae.exe" in advice["notes"]

    def test_a_warming_up_window_says_it_is_still_watching(self):
        advice = la.build_local_advice([], 40.0, {"ram_percent": 70.0}, uptime_days=1.0)
        assert "watching" in advice["summary"].lower()

    def test_local_advice_never_carries_urgency(self):
        # urgency bypasses thresholds entirely, which a rule must not do
        advice = la.build_local_advice(
            [group("a.exe", 9000)], FULL_WINDOW, {"ram_percent": 99.0}, uptime_days=99.0
        )
        assert advice["urgency"] == ""


class TestRealWorldFalsePositives:
    """Each of these was pre-ticked by the first run against a real machine."""

    def test_start_menu_is_blocked_outright(self):
        from monitor.constants import BLOCKED_NAMES

        # respawns instantly, so killing it achieves nothing but a flicker
        assert "startmenuexperiencehost.exe" in BLOCKED_NAMES
        assert "searchhost.exe" in BLOCKED_NAMES
        assert "textinputhost.exe" in BLOCKED_NAMES
        assert "runtimebroker.exe" in BLOCKED_NAMES

    def test_vpn_is_never_pre_ticked(self):
        # closing this drops the network, possibly mid-meeting
        found = la.find_candidates([group("cloudflare warp.exe", 211)], FULL_WINDOW)
        assert found[0]["suggest"] is False
        assert "network" in found[0]["caution"]

    def test_a_test_runner_is_never_pre_ticked(self):
        found = la.find_candidates([group("cypress.exe", 825)], FULL_WINDOW)
        assert found[0]["suggest"] is False

    def test_node_is_never_pre_ticked(self):
        # could be a dev server or a running suite
        found = la.find_candidates([group("node.exe", 354)], FULL_WINDOW)
        assert found[0]["suggest"] is False

    def test_java_is_never_pre_ticked(self):
        found = la.find_candidates([group("java.exe", 250)], FULL_WINDOW)
        assert found[0]["suggest"] is False

    def test_embedded_webview_is_never_pre_ticked(self):
        # hosts another app's UI, so killing it breaks that app
        found = la.find_candidates([group("msedgewebview2.exe", 419)], FULL_WINDOW)
        assert found[0]["suggest"] is False

    def test_an_ordinary_app_is_still_pre_ticked(self):
        found = la.find_candidates([group("asana.exe", 1369, count=11)], FULL_WINDOW)
        assert found[0]["suggest"] is True
