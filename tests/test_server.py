import json

import pytest

from monitor import safety, server
from monitor.constants import HOST


class FakeSampler:
    interval = 2.0

    def __init__(self, rows=None):
        self._rows = rows or [
            {
                "pid": 100,
                "name": "chrome.exe",
                "rss": 1024,
                "cpu_percent": 0.0,
                "create_time": 1000.0,
                "access": True,
            },
            {
                "pid": 300,
                "name": "lsass.exe",
                "rss": 2048,
                "cpu_percent": 0.0,
                "create_time": 1000.0,
                "access": True,
            },
        ]

    def latest(self):
        return self._rows

    def buffer_list(self):
        return [self._rows]

    def totals(self):
        return {"ram_percent": 50.0, "cpu_percent": 10.0}

    def facts(self):
        from monitor import analysis

        return analysis.build_facts(self.buffer_list(), self.totals(), self.interval)


PROTECTED = safety.protected_pids(own_pid=9000, ancestor_pids=[8000])


class TestBuildState:
    def test_state_has_facts_and_advice(self, tmp_path):
        state = server.build_state(FakeSampler(), tmp_path / "advice.json")
        assert "groups" in state
        assert "totals" in state
        assert state["advice"]["available"] is False

    def test_blocked_processes_are_flagged_not_hidden(self, tmp_path):
        state = server.build_state(FakeSampler(), tmp_path / "advice.json")
        names = {g["name"]: g for g in state["groups"]}
        assert names["lsass.exe"]["killable"] is False
        assert names["chrome.exe"]["killable"] is True

    def test_advice_is_included_when_present(self, tmp_path):
        path = tmp_path / "advice.json"
        path.write_text(
            json.dumps({"summary": "hi", "targets": [{"pid": 100, "create_time": 1000.0}]}),
            encoding="utf-8",
        )
        state = server.build_state(FakeSampler(), path)
        assert state["advice"]["summary"] == "hi"


class TestHandleKill:
    def test_rejects_non_object_payload(self, tmp_path):
        result = server.handle_kill([1, 2], FakeSampler(), PROTECTED)
        assert result["ok"] is False

    def test_rejects_missing_targets_key(self, tmp_path):
        result = server.handle_kill({}, FakeSampler(), PROTECTED)
        assert result["ok"] is False

    def test_rejects_targets_that_is_not_a_list(self):
        result = server.handle_kill({"targets": "all"}, FakeSampler(), PROTECTED)
        assert result["ok"] is False

    def test_blocked_target_is_rejected_without_killing(self):
        payload = {"targets": [{"pid": 300, "create_time": 1000.0}]}
        result = server.handle_kill(payload, FakeSampler(), PROTECTED)
        assert result["ok"] is True
        assert result["results"] == []
        assert len(result["rejected"]) == 1
        assert "system-critical" in result["rejected"][0]["reason"]

    def test_absent_pid_is_rejected(self):
        payload = {"targets": [{"pid": 999, "create_time": 1.0}]}
        result = server.handle_kill(payload, FakeSampler(), PROTECTED)
        assert len(result["rejected"]) == 1

    def test_too_many_targets_is_rejected(self):
        payload = {"targets": [{"pid": 100, "create_time": 1000.0}] * 200}
        result = server.handle_kill(payload, FakeSampler(), PROTECTED)
        assert result["ok"] is False


class TestBind:
    def test_server_binds_loopback_only(self):
        assert HOST == "127.0.0.1"
        assert server.HOST == "127.0.0.1"
