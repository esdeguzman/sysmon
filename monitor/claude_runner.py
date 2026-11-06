"""Runs the Claude Code CLI as a child process, without blocking the UI.

Every run is a fresh `claude -p` session that spends plan tokens, so it is
button-driven. The one exception is critical memory pressure, which is
also the moment a real opinion is worth most and the user is least likely
to be sitting there. That path carries a cooldown so a machine stuck at
95 percent cannot loop.

Output is drained on a reader thread into a queue. The UI polls the queue
from the Tk main thread, because Tk is not thread safe.
"""

import os
import queue
import shutil
import subprocess
import threading
import time

from monitor.constants import (
    CLAUDE_AUTO_COOLDOWN_SECONDS,
    CLAUDE_AUTO_RAM_PERCENT,
    CLAUDE_MAX_OUTPUT_LINES,
    CLAUDE_PROMPT,
    CLAUDE_TIMEOUT_SECONDS,
    PROJECT_ROOT,
)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_claude():
    """Path to the Claude Code CLI, or None if it is not installed."""
    return shutil.which("claude")


def build_command(claude_path, prompt=CLAUDE_PROMPT):
    """The exact argv for a headless run.

    Only Read and Write are allowed. In print mode a tool that is not
    allowed is refused rather than prompting, so the run cannot hang
    waiting for an answer nobody is there to give.
    """
    return [
        claude_path,
        "-p",
        prompt,
        "--allowedTools",
        "Read",
        "Write",
        "--output-format",
        "text",
    ]


def should_auto_run(ram_percent, running, last_run, now):
    """Whether critical pressure justifies spending a session unprompted."""
    if running:
        return False
    if ram_percent < CLAUDE_AUTO_RAM_PERCENT:
        return False
    if last_run is not None and now - last_run < CLAUDE_AUTO_COOLDOWN_SECONDS:
        return False
    return True


def clean_line(line):
    """Trim a CLI output line for display, or None if it carries nothing."""
    text = (line or "").rstrip("\r\n").rstrip()
    if not text.strip():
        return None
    return text[:200]


class ClaudeRunner:
    """Owns at most one child process at a time."""

    def __init__(self, cwd=None, timeout=CLAUDE_TIMEOUT_SECONDS):
        self.cwd = str(cwd or PROJECT_ROOT)
        self.timeout = timeout
        self.lines = queue.Queue()
        self._proc = None
        self._thread = None
        self._lock = threading.Lock()
        self.last_run = None
        self.last_status = "idle"  # idle | running | done | failed | stopped
        self._timed_out = False

    # --------------------------------------------------------------- state

    def available(self):
        return find_claude() is not None

    def is_running(self):
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def drain(self, limit=CLAUDE_MAX_OUTPUT_LINES):
        """Pull whatever the reader thread has queued. Never blocks."""
        out = []
        while len(out) < limit:
            try:
                out.append(self.lines.get_nowait())
            except queue.Empty:
                break
        return out

    # --------------------------------------------------------------- action

    def start(self, prompt=CLAUDE_PROMPT, argv=None):
        """Spawn a run. Returns (started, message)."""
        if self.is_running():
            return False, "already running"

        if argv is None:
            claude_path = find_claude()
            if claude_path is None:
                self.last_status = "failed"
                return False, "claude CLI not found on PATH"
            argv = build_command(claude_path, prompt)

        try:
            proc = subprocess.Popen(
                argv,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_NO_WINDOW,
            )
        except OSError as exc:
            self.last_status = "failed"
            self.lines.put(f"could not start: {exc}")
            return False, str(exc)

        with self._lock:
            self._proc = proc
        self.last_run = time.monotonic()
        self.last_status = "running"
        self._timed_out = False
        self.lines.put("started")

        # The watchdog is a separate thread on purpose. Checking the clock
        # inside the reader loop only works while the child keeps printing:
        # one that hangs silently blocks on readline forever, which is
        # precisely the case the timeout exists to catch.
        threading.Thread(
            target=self._watchdog,
            args=(proc, time.monotonic() + self.timeout),
            daemon=True,
        ).start()

        self._thread = threading.Thread(target=self._pump, args=(proc,), daemon=True)
        self._thread.start()
        return True, "started"

    def stop(self):
        """Kill the current run, if any."""
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            proc.kill()
        except OSError:
            return False
        self.last_status = "stopped"
        self.lines.put("stopped by user")
        return True

    # --------------------------------------------------------------- reader

    def _watchdog(self, proc, deadline):
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.1)
        if proc.poll() is None:
            self._timed_out = True
            try:
                proc.kill()
            except OSError:
                pass

    def _pump(self, proc):
        try:
            for raw in proc.stdout:
                text = clean_line(raw)
                if text:
                    self.lines.put(text)
        except Exception as exc:
            self.lines.put(f"read error: {exc}")

        code = proc.wait()
        if self.last_status == "stopped":
            return
        if self._timed_out:
            self.last_status = "failed"
            self.lines.put(f"timed out after {self.timeout}s")
            return
        if code == 0:
            self.last_status = "done"
            self.lines.put("finished")
        else:
            self.last_status = "failed"
            self.lines.put(f"exited with code {code}")
