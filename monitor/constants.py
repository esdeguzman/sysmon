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

# Attention thresholds. The tool only earns a flash by being rare, so both
# pressure and payoff must be present: a full machine with nothing worth
# closing is not actionable, and 2 GB of slack on a quiet machine is not
# urgent. Critical pressure fires on its own, advice or not.
ALERT_RAM_PERCENT = 85.0
ALERT_RECLAIM_BYTES = 1024 * 1024 * 1024  # 1 GB
CRITICAL_RAM_PERCENT = 94.0
ALERT_COOLDOWN_SECONDS = 900  # 15 minutes before a standing condition re-flashes

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
        # Windows shell components. They respawn within seconds, so killing
        # them reclaims nothing and just flickers the desktop. Offering them
        # as targets wastes a click and looks like the tool is working.
        "startmenuexperiencehost.exe",
        "searchhost.exe",
        "shellexperiencehost.exe",
        "textinputhost.exe",
        "runtimebroker.exe",
        "applicationframehost.exe",
        "ctfmon.exe",
        "taskhostw.exe",
        "dllhost.exe",
        # Not a Windows process, but killing it ends the Claude Code session
        # that writes the advice file. The ancestor check already covers this
        # when the monitor is started from that session, and does not when
        # run.cmd is launched from Explorer, which is the documented path.
        "claude.exe",
    }
)

BLOCKED_PIDS = frozenset({0, 4})

# Advice ageing. Past STALE it is labelled and greyed; past EXPIRED it stops
# counting as advice at all, so a day-old file cannot raise an alert.
ADVICE_STALE_SECONDS = 1800  # 30 minutes
ADVICE_EXPIRED_SECONDS = 7200  # 2 hours

# Built-in analysis, used when Claude's advice is absent or expired. A rule
# is less trustworthy than a judgement, so it has to clear more before it
# spends the user's attention.
LOCAL_ALERT_RAM_PERCENT = 90.0
LOCAL_ALERT_RECLAIM_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB
LOCAL_MIN_CANDIDATE_BYTES = 200 * 1024 * 1024  # below this, closing it is noise
LOCAL_MANY_PROCESSES = 20  # a big process tree is usually an open working set
UPTIME_NAG_DAYS = 7

# Killable, but never pre-ticked by a rule: unsaved work lives here, and a
# rule cannot tell "idle" from "finished with". The user can still tick
# them by hand, and Claude can still target them with its own reasoning.
CAUTION_NAMES = frozenset(
    {
        "code.exe",
        "code - insiders.exe",
        "trae.exe",
        "devenv.exe",
        "idea64.exe",
        "pycharm64.exe",
        "webstorm64.exe",
        "rider64.exe",
        "studio64.exe",
        "sublime_text.exe",
        "notepad++.exe",
        "notepad.exe",
        "wordpad.exe",
        "winword.exe",
        "excel.exe",
        "powerpnt.exe",
        "warp.exe",
        "windowsterminal.exe",
        "wt.exe",
        "powershell.exe",
        "pwsh.exe",
        "cmd.exe",
        "conhost.exe",
        "bash.exe",
        "git-bash.exe",
        "wsl.exe",
        "obs64.exe",
        "ms-teams.exe",
        "teams.exe",
        "zoom.exe",
        # Network and VPN. Closing one of these drops connectivity, which
        # is a bad surprise at any time and a worse one mid-call.
        "cloudflare warp.exe",
        "warp-svc.exe",
        "openvpn.exe",
        "wireguard.exe",
        "nordvpn.exe",
        "expressvpn.exe",
        "tailscale-ipn.exe",
        # Developer processes. A rule cannot tell a finished run from a
        # dev server, a running test suite or a local backend.
        "node.exe",
        "cypress.exe",
        "java.exe",
        "javaw.exe",
        "python.exe",
        "pythonw.exe",
        "dotnet.exe",
        "docker desktop.exe",
        "com.docker.backend.exe",
        "gradle.exe",
        "mysqld.exe",
        "postgres.exe",
        # Hosts another application's UI, so killing it breaks that app
        # rather than the one named here.
        "msedgewebview2.exe",
    }
)

# Running the Claude Code CLI from inside the app. Every run is a fresh
# session that spends plan tokens, so it is button-driven by default.
CLAUDE_TIMEOUT_SECONDS = 420  # a real run measured 97s; leave headroom
CLAUDE_AUTO_COOLDOWN_SECONDS = 1800  # 30 min, so a stuck-high machine cannot loop
CLAUDE_AUTO_RAM_PERCENT = CRITICAL_RAM_PERCENT  # only at critical pressure
CLAUDE_MAX_OUTPUT_LINES = 400

CLAUDE_PROMPT = (
    "Check this laptop. Read data/snapshot.json: `facts.groups` has the "
    "per-app totals with idle_seconds and rss_trend, and `processes` has the "
    "live per-process rows. Copy pid and create_time for every target from "
    "`processes`, never from a previous advice file, because a stale "
    "create_time is rejected by the PID reuse guard. Then write "
    "data/advice.json following the contract in CLAUDE.md. Keep the summary "
    "to two sentences. Say in notes what you deliberately left alone and "
    "why. Do not target anything in BLOCKED_NAMES."
)
SNAPSHOT_PROCESS_ROWS = 80  # enough to cover anything worth closing
