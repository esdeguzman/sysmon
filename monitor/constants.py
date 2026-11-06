"""Shared literals. No logic lives here."""

from pathlib import Path

SAMPLE_INTERVAL = 2.0
BUFFER_DEPTH = 450  # 450 * 2s = 15 minutes
IDLE_CPU_THRESHOLD = 1.0
GROWTH_MB_PER_MIN = 5.0
GROWTH_MIN_SECONDS = 300
GRACEFUL_WAIT_SECONDS = 5.0

HOST = "127.0.0.1"
PORT = 7777

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
WEB_DIR = PROJECT_ROOT / "web"
SNAPSHOT_PATH = DATA_DIR / "snapshot.json"
ADVICE_PATH = DATA_DIR / "advice.json"

# Killing any of these bluescreens Windows or breaks the session.
# Compared case-insensitively against the process name.
BLOCKED_NAMES = frozenset(
    {
        "system",
        "system idle process",
        "idle",
        "registry",
        "memcompression",
        "smss.exe",
        "csrss.exe",
        "wininit.exe",
        "winlogon.exe",
        "services.exe",
        "lsass.exe",
        "lsaiso.exe",
        "fontdrvhost.exe",
        "dwm.exe",
        "svchost.exe",
        "ntoskrnl.exe",
        "audiodg.exe",
        "sihost.exe",
        "explorer.exe",
        # Not a Windows process, but killing it ends the Claude Code session
        # that writes the advice file. The ancestor check already covers this
        # when the monitor is started from that session, and does not when
        # run.cmd is launched from Explorer, which is the documented path.
        "claude.exe",
    }
)

BLOCKED_PIDS = frozenset({0, 4})
