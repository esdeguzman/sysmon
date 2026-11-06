import subprocess
import sys
import time

import psutil
import pytest

from monitor import killer


def spawn_sleeper():
    """A child that just sleeps. Closes on request, dies on force."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    time.sleep(0.4)
    return proc


def as_target(proc):
    p = psutil.Process(proc.pid)
    return {
        "pid": proc.pid,
        "name": p.name(),
        "create_time": p.create_time(),
        "rss": p.memory_info().rss,
    }


@pytest.fixture
def sleeper():
    proc = spawn_sleeper()
    yield proc
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=5)


class TestKillTargets:
    def test_console_child_is_force_killed(self, sleeper):
        results = killer.kill_targets([as_target(sleeper)], force=True, wait=3.0)
        assert len(results) == 1
        assert results[0]["status"] == "closed"
        assert not psutil.pid_exists(sleeper.pid) or _is_zombie(sleeper.pid)

    def test_windowless_child_refuses_graceful_close(self, sleeper):
        # A process with no window never receives WM_CLOSE, so taskkill
        # without /F cannot reach it. This exercises the refusal path for
        # real rather than mocking it.
        results = killer.kill_targets([as_target(sleeper)], force=False, wait=2.0)
        assert results[0]["status"] in {"refused", "failed"}
        assert psutil.pid_exists(sleeper.pid)

    def test_freed_bytes_reported_on_close(self, sleeper):
        target = as_target(sleeper)
        results = killer.kill_targets([target], force=True, wait=3.0)
        assert results[0]["freed_bytes"] == target["rss"]

    def test_already_dead_process_reports_closed(self):
        proc = spawn_sleeper()
        target = as_target(proc)
        proc.kill()
        proc.wait(timeout=5)
        results = killer.kill_targets([target], force=True, wait=2.0)
        assert results[0]["status"] == "closed"

    def test_empty_target_list_returns_empty(self):
        assert killer.kill_targets([], force=False) == []

    def test_result_carries_pid_and_name(self, sleeper):
        target = as_target(sleeper)
        results = killer.kill_targets([target], force=True, wait=3.0)
        assert results[0]["pid"] == target["pid"]
        assert results[0]["name"] == target["name"]


def _is_zombie(pid):
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True
