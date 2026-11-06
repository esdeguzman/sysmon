# sysmon

A local RAM and process monitor. Claude reads a snapshot file and writes an
advice file. No API call is involved.

## The loop

1. The user runs `run.cmd`, which opens the native Tkinter window
   (`monitor/app.py`). A sampler thread inside it writes
   `data/snapshot.json` every 2 seconds.
2. The user asks Claude to check the laptop.
3. Claude reads `data/snapshot.json`.
4. Claude writes `data/advice.json`.
5. The sidebar picks it up on the next 2 second refresh.

## Reading the snapshot

`data/snapshot.json` holds `totals` and `facts.groups`. Each group is one
executable with summed RSS across its processes, plus `idle_seconds`,
`rss_trend` in MB per minute, `growing`, and the member `pids`.

`data/snapshot.json` also holds `processes`: the top 80 rows by memory, each
with `pid`, `name`, `rss`, `create_time` and `cpu_percent`.

**Always copy `pid` and `create_time` from `processes`. Never carry them
forward from a previous `advice.json`.** A stale `create_time` is rejected by
the PID reuse guard the moment that process has restarted, so the target
silently does nothing when the user presses Optimize. The first live headless
run did exactly this, because `processes` did not exist yet and a `claude -p`
run has Read and Write only.

## Writing advice

Write `data/advice.json`:

    {
      "written_at": "2026-11-06T14:22:00",
      "summary": "One or two sentences on what is using the RAM.",
      "targets": [
        {"pid": 4411, "create_time": 1762406400.0, "name": "Teams.exe",
         "reason": "idle 42m, holding 900 MB", "est_mb": 900}
      ],
      "notes": "What you deliberately left alone, and why."
    }

`create_time` is required. Without it the PID reuse guard rejects the target.
Copy it from the snapshot, never invent it.

`urgency` is optional. Set it to `"high"` only when something needs looking at
regardless of the RAM percentage, such as a confirmed leak or a runaway
process. It bypasses both thresholds and flashes the taskbar, so it spends the
user's attention directly. Leave it out and the app decides on its own.

## Rules

- Never target a process in `BLOCKED_NAMES` (`monitor/constants.py`). The
  server rejects them, but suggesting one wastes the user's time.
- Never target the user's editor or terminal without saying so in `notes`.
- `idle_seconds` equal to the full window means idle for at least that long,
  not exactly that long.
- A high `rss_trend` on a process started seconds ago is startup allocation,
  not a leak. `growing` already applies a 5 minute floor.
- Say what you left alone and why. That is usually more useful than the
  kill list.

## The checkbox model is per group, not per PID

The sidebar ticks whole executables. Naming one Chrome renderer in `targets`
selects every Chrome process, because the frontend maps the target's PID back
to its group name. When that is not what you want, say so in `notes` and let
the user untick.

## Tests

    python -m pytest

207 tests. `tests/test_killer.py` spawns and kills real throwaway Python
processes, so it takes about 6 seconds.

## Where the UI logic lives

`monitor/viewmodel.py` is pure and holds everything the sidebar decides:
formatting, which rows are ticked, how advice maps to groups, and
`process_state`. `monitor/app.py` is Tk wiring over it and should stay that
way, because widgets are the part that cannot be tested headless.

`process_state` returns `quiet` rather than `active` when a process has used
no CPU for the whole window but the window is still shorter than the five
minute idle threshold. Calling that `active` stated the opposite of what was
measured, which is the bug it exists to prevent.

## build_facts must stay linear

`analysis.build_facts` aggregates each sample by name once, then lets every
group index into that result. The obvious implementation, scanning the whole
buffer per group, is O(groups x samples x processes): measured at **5.5
seconds** on a full 15 minute buffer with 345 processes and 103 groups. The UI
refreshes every 2 seconds on the Tk main thread, so that froze the window and
left the sampler permanently behind. The fix took it to 116 ms.

`tests/test_analysis.py::TestBuildFactsScales` guards it. If that test starts
failing, the loops have been inverted back.

## The app can run Claude itself

`monitor/claude_runner.py` spawns `claude -p` as a child process and the
sidebar shows a dot that blinks green while it is alive. One button starts a
run and stops the one in flight. Output streams into a read-only pane.

    claude -p "<CLAUDE_PROMPT>" --allowedTools Read Write --output-format text

Run with `cwd` set to the project root, so this file is auto-discovered and
the advice contract comes with it. **Only Read and Write are allowed, and no
permission bypass flag is ever passed.** In print mode a tool that is not
allowed is refused rather than prompting, so a headless run cannot hang
waiting for an answer nobody is there to give. `TestBuildCommand` asserts the
bypass flags are absent.

Every run is a fresh session that spends plan tokens, so it is button-driven.
The single exception is RAM at or above the critical threshold, with a 30
minute cooldown so a machine stuck at 95 percent cannot loop.

**The timeout needs its own thread.** Checking the clock inside the stdout
reader only works while the child keeps printing: one that hangs silently
blocks on `readline` forever, which is the exact case a timeout exists to
catch. `test_timeout_kills_a_runaway` caught that and guards it.

`shutil.which` resolves to `claude.CMD`, a batch file. Verified that `Popen`
launches it and captures its output.

**What two real runs measured.** 97s and 85s wall clock, both exit 0, both
rewrote `advice.json`. The timeout is 420s on that evidence. Run one revealed
the `processes` gap: with Read and Write only there was no live `create_time`
anywhere, so it copied one from the previous advice file. It passed safety by
luck, because that process had not restarted. Run two, with `processes` in the
snapshot, copied live values for both targets and both passed the live safety
check.

**Known limit:** `processes` is capped at the top 80 rows by memory, which on
this machine bottoms out around 90 MB. Claude noticed and said so rather than
guessing: smaller leftovers have no live `create_time` and cannot be targeted.
Raise `SNAPSHOT_PROCESS_ROWS` if that starts to matter.

## It analyses itself when Claude is absent

`monitor/local_analysis.py` runs when `advice.json` is missing or expired, so
the app keeps working with no session attached. The sidebar header switches
from CLAUDE SUGGESTS to BUILT-IN RULES. Provenance is never blurred: the user
must always know whether they are reading judgement or a rule.

Rules find candidates freely and suggest conservatively. A group is a
candidate if it is killable, over 200 MB, and either idle for the whole
window or flagged growing. Nothing is judged until the window reaches 5
minutes, because before that everything looks idle.

**Two classes are found but never pre-ticked**, since a rule cannot tell idle
from finished-with:

| Class | Why |
|---|---|
| `CAUTION_NAMES` | editors and terminals (unsaved work), VPN and network (drops connectivity), developer processes (could be a running server or suite), `msedgewebview2.exe` (hosts another app's window) |
| 20 or more processes | a big tree is usually an open working set, like 47 Chrome renderers |

Both stay tickable by hand, and Claude can still target them with its own
reasoning. The restriction is on what a *rule* may decide unprompted.

Local findings alert at a higher bar than Claude's: 90 percent and 2 GB
against 85 and 1. `build_local_advice` never sets `urgency`, and
`should_alert` ignores it when `local=True`, because that field bypasses
every threshold and is authority a rule has not earned.

**The lists were calibrated against a real machine, not guessed.** The first
run pre-ticked the Windows Start Menu, a VPN, a test runner and a JVM. Those
are now blocked or cautioned, with a test each in
`TestRealWorldFalsePositives`.

## Advice ages, and the UI says so

`advice.json` is a snapshot of a moment. Shown without its age it reads as
present tense forever, which is how a three day old file ends up describing
RAM that has moved on and naming processes that exited yesterday.

| Age | Behaviour |
|---|---|
| Under 30 min | Normal. Header shows "4 min ago" |
| 30 min to 2 hours | Header shows "stale", summary greyed |
| Over 2 hours | Also expired: cannot raise an alert at all |

`vm.dead_targets` lists targets whose process is gone, including the case
where the PID was recycled by something else, and the sidebar names them.

Expired advice still displays, because it may still be right. It just stops
being allowed to spend the user's attention. Critical RAM is exempt from the
expiry gate: that is measured now, not claimed by a file.

## Getting the user's attention

`monitor/attention.py` decides when the app flashes its taskbar button, badges
its title and shows a banner. The bar is deliberately high, because an alert
that fires often is an alert that gets ignored:

| Condition | Fires |
|---|---|
| RAM at or above 85 percent AND 1 GB or more reclaimable | yes |
| RAM at or above 94 percent | yes, advice or not |
| `urgency: "high"` in advice.json | yes, whatever the numbers say |
| Anything else | no |

Reclaimable is measured from the live sample, not from the advice's own
`est_mb`, which goes stale as soon as an app grows or the user closes
something.

`AlertState` is edge-triggered: it fires when the condition appears, when the
advice behind it changes, and once per 15 minute cooldown while it persists.
Focusing the window acknowledges it. A condition that clears and returns
flashes again.

`monitor/flash.py` resolves the top-level window with `GetParent` before
calling `FlashWindowEx`. Tk's `winfo_id()` returns an inner child window, and
flashing that one succeeds, reports no error and blinks nothing.

## The web UI is still there

`run-web.cmd` serves the same data at http://127.0.0.1:7777. Useful for
driving `/api/state` and `/api/kill` with `curl`. The native window is the
default because a WebView2 shell measured 436 MB against Tkinter's 37.
