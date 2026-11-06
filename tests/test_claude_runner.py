import sys
import time

from monitor import claude_runner as cr
from monitor.constants import CLAUDE_AUTO_COOLDOWN_SECONDS, CLAUDE_AUTO_RAM_PERCENT


class TestBuildCommand:
    def test_uses_print_mode(self):
        argv = cr.build_command("claude.exe", "hi")
        assert argv[0] == "claude.exe"
        assert "-p" in argv
        assert "hi" in argv

    def test_allows_only_read_and_write(self):
        argv = cr.build_command("claude.exe")
        allowed = argv[argv.index("--allowedTools") + 1 : argv.index("--output-format")]
        assert allowed == ["Read", "Write"]

    def test_never_bypasses_permissions(self):
        # a headless run must not be handed a blanket permission escape
        argv = cr.build_command("claude.exe")
        joined = " ".join(argv)
        assert "bypassPermissions" not in joined
        assert "--dangerously-skip-permissions" not in joined
        assert "--allow-dangerously-skip-permissions" not in joined

    def test_output_is_plain_text(self):
        argv = cr.build_command("claude.exe")
        assert argv[argv.index("--output-format") + 1] == "text"


class TestShouldAutoRun:
    def test_below_critical_never_runs(self):
        assert cr.should_auto_run(93.0, False, None, now=1000.0) is False

    def test_at_critical_runs_when_never_run_before(self):
        assert cr.should_auto_run(CLAUDE_AUTO_RAM_PERCENT, False, None, now=1000.0) is True

    def test_does_not_run_while_one_is_in_flight(self):
        assert cr.should_auto_run(99.0, True, None, now=1000.0) is False

    def test_respects_the_cooldown(self):
        last = 1000.0
        inside = last + CLAUDE_AUTO_COOLDOWN_SECONDS - 1
        assert cr.should_auto_run(99.0, False, last, now=inside) is False

    def test_runs_again_once_the_cooldown_passes(self):
        last = 1000.0
        after = last + CLAUDE_AUTO_COOLDOWN_SECONDS
        assert cr.should_auto_run(99.0, False, last, now=after) is True


class TestCleanLine:
    def test_strips_trailing_newline(self):
        assert cr.clean_line("hello\r\n") == "hello"

    def test_blank_lines_are_dropped(self):
        assert cr.clean_line("   \n") is None
        assert cr.clean_line("") is None

    def test_very_long_lines_are_truncated(self):
        assert len(cr.clean_line("x" * 5000)) == 200


class TestRunnerAgainstARealProcess:
    """Exercises the real subprocess and reader thread, not a mock."""

    def _argv(self, body):
        return [sys.executable, "-c", body]

    def test_captures_output_and_finishes(self):
        runner = cr.ClaudeRunner(timeout=20)
        started, _ = runner.start(argv=self._argv(
            "print('reading snapshot'); print('wrote advice')"
        ))
        assert started is True
        for _ in range(100):
            if not runner.is_running():
                break
            time.sleep(0.05)
        time.sleep(0.3)
        lines = runner.drain()
        assert "reading snapshot" in lines
        assert "wrote advice" in lines
        assert runner.last_status == "done"

    def test_a_failing_run_is_reported_not_swallowed(self):
        runner = cr.ClaudeRunner(timeout=20)
        runner.start(argv=self._argv("import sys; sys.exit(3)"))
        for _ in range(100):
            if not runner.is_running():
                break
            time.sleep(0.05)
        time.sleep(0.3)
        assert runner.last_status == "failed"
        assert any("code 3" in line for line in runner.drain())

    def test_stop_kills_a_long_run(self):
        runner = cr.ClaudeRunner(timeout=60)
        runner.start(argv=self._argv("import time; time.sleep(60)"))
        time.sleep(0.6)
        assert runner.is_running() is True
        assert runner.stop() is True
        for _ in range(60):
            if not runner.is_running():
                break
            time.sleep(0.05)
        assert runner.is_running() is False
        assert runner.last_status == "stopped"

    def test_two_starts_do_not_overlap(self):
        runner = cr.ClaudeRunner(timeout=60)
        runner.start(argv=self._argv("import time; time.sleep(5)"))
        time.sleep(0.4)
        started, message = runner.start(argv=self._argv("print('second')"))
        assert started is False
        assert "already running" in message
        runner.stop()

    def test_a_missing_executable_fails_cleanly(self):
        runner = cr.ClaudeRunner(timeout=5)
        started, message = runner.start(argv=["definitely-not-a-real-binary-xyz"])
        assert started is False
        assert runner.last_status == "failed"
        assert message

    def test_stop_on_an_idle_runner_is_harmless(self):
        assert cr.ClaudeRunner().stop() is False

    def test_drain_on_a_fresh_runner_is_empty(self):
        assert cr.ClaudeRunner().drain() == []

    def test_timeout_kills_a_runaway(self):
        runner = cr.ClaudeRunner(timeout=1)
        runner.start(argv=self._argv(
            "import time,sys\n"
            "print('working', flush=True)\n"
            "time.sleep(0.2)\n"
            "print('still working', flush=True)\n"
            "time.sleep(10)\n"
        ))
        for _ in range(120):
            if not runner.is_running():
                break
            time.sleep(0.05)
        assert runner.is_running() is False
        assert runner.last_status == "failed"
        assert any("timed out" in line for line in runner.drain())
