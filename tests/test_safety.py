from monitor import safety


def proc(pid, name, create_time=1000.0, rss=1024):
    return {
        "pid": pid,
        "name": name,
        "create_time": create_time,
        "rss": rss,
    }


SNAPSHOT = [
    proc(100, "chrome.exe"),
    proc(200, "Teams.exe"),
    proc(300, "lsass.exe"),
    proc(400, "csrss.exe"),
    proc(4, "System"),
    proc(500, "claude.exe"),
    proc(600, "explorer.exe"),
]

PROTECTED = safety.protected_pids(own_pid=9000, ancestor_pids=[500, 8000])


class TestProtectedPids:
    def test_includes_own_pid(self):
        assert 9000 in PROTECTED

    def test_includes_every_ancestor(self):
        assert 500 in PROTECTED
        assert 8000 in PROTECTED

    def test_includes_kernel_pids(self):
        assert 0 in PROTECTED
        assert 4 in PROTECTED


class TestBlocklist:
    def test_lsass_is_rejected(self):
        ok, reason = safety.check_target(
            {"pid": 300, "create_time": 1000.0}, SNAPSHOT, PROTECTED
        )
        assert ok is False
        assert "system-critical" in reason

    def test_csrss_is_rejected(self):
        ok, reason = safety.check_target(
            {"pid": 400, "create_time": 1000.0}, SNAPSHOT, PROTECTED
        )
        assert ok is False
        assert "system-critical" in reason

    def test_explorer_is_rejected(self):
        ok, _ = safety.check_target(
            {"pid": 600, "create_time": 1000.0}, SNAPSHOT, PROTECTED
        )
        assert ok is False

    def test_blocklist_is_case_insensitive(self):
        snapshot = [proc(777, "LSASS.EXE")]
        ok, reason = safety.check_target(
            {"pid": 777, "create_time": 1000.0}, snapshot, PROTECTED
        )
        assert ok is False
        assert "system-critical" in reason

    def test_claude_is_rejected_even_when_not_an_ancestor(self):
        # Launched from Explorer, run.cmd has no Claude session in its
        # ancestry, so the blocklist is the only thing protecting it.
        snapshot = [proc(1234, "claude.exe")]
        protected = safety.protected_pids(own_pid=9000, ancestor_pids=[])
        ok, reason = safety.check_target(
            {"pid": 1234, "create_time": 1000.0}, snapshot, protected
        )
        assert ok is False
        assert "system-critical" in reason

    def test_pid_4_is_rejected(self):
        ok, reason = safety.check_target(
            {"pid": 4, "create_time": 1000.0}, SNAPSHOT, PROTECTED
        )
        assert ok is False


class TestSelfProtection:
    def test_own_pid_is_rejected(self):
        snapshot = SNAPSHOT + [proc(9000, "python.exe")]
        ok, reason = safety.check_target(
            {"pid": 9000, "create_time": 1000.0}, snapshot, PROTECTED
        )
        assert ok is False
        assert "protected" in reason

    def test_ancestor_is_rejected(self):
        ok, reason = safety.check_target(
            {"pid": 500, "create_time": 1000.0}, SNAPSHOT, PROTECTED
        )
        assert ok is False
        assert "protected" in reason


class TestPidReuseGuard:
    def test_create_time_mismatch_is_rejected(self):
        ok, reason = safety.check_target(
            {"pid": 100, "create_time": 999.0}, SNAPSHOT, PROTECTED
        )
        assert ok is False
        assert "changed" in reason

    def test_missing_create_time_is_rejected(self):
        ok, reason = safety.check_target({"pid": 100}, SNAPSHOT, PROTECTED)
        assert ok is False

    def test_matching_create_time_is_allowed(self):
        ok, reason = safety.check_target(
            {"pid": 100, "create_time": 1000.0}, SNAPSHOT, PROTECTED
        )
        assert ok is True
        assert reason == "ok"


class TestPresence:
    def test_pid_absent_from_snapshot_is_rejected(self):
        ok, reason = safety.check_target(
            {"pid": 7777, "create_time": 1000.0}, SNAPSHOT, PROTECTED
        )
        assert ok is False
        assert "not in the current snapshot" in reason

    def test_empty_snapshot_rejects_everything(self):
        ok, _ = safety.check_target(
            {"pid": 100, "create_time": 1000.0}, [], PROTECTED
        )
        assert ok is False

    def test_non_integer_pid_is_rejected(self):
        ok, _ = safety.check_target(
            {"pid": "100; shutdown", "create_time": 1000.0}, SNAPSHOT, PROTECTED
        )
        assert ok is False


class TestFilterTargets:
    def test_splits_allowed_from_rejected(self):
        targets = [
            {"pid": 100, "create_time": 1000.0},  # chrome, fine
            {"pid": 300, "create_time": 1000.0},  # lsass, blocked
            {"pid": 7777, "create_time": 1000.0},  # absent
        ]
        allowed, rejected = safety.filter_targets(targets, SNAPSHOT, PROTECTED)
        assert [p["pid"] for p in allowed] == [100]
        assert len(rejected) == 2
        assert all("reason" in r for r in rejected)

    def test_allowed_entries_carry_snapshot_data(self):
        allowed, _ = safety.filter_targets(
            [{"pid": 200, "create_time": 1000.0}], SNAPSHOT, PROTECTED
        )
        assert allowed[0]["name"] == "Teams.exe"
        assert allowed[0]["rss"] == 1024

    def test_malicious_advice_naming_lsass_is_fully_rejected(self):
        # the end-to-end case the spec calls out: a hand-edited advice.json
        targets = [
            {"pid": 300, "create_time": 1000.0},
            {"pid": 400, "create_time": 1000.0},
            {"pid": 4, "create_time": 1000.0},
        ]
        allowed, rejected = safety.filter_targets(targets, SNAPSHOT, PROTECTED)
        assert allowed == []
        assert len(rejected) == 3

    def test_empty_target_list_is_handled(self):
        assert safety.filter_targets([], SNAPSHOT, PROTECTED) == ([], [])
