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

Per-process rows with `create_time` are not in that file. Get them with:

    python -c "from monitor.sampler import Sampler; import json; print(json.dumps(Sampler().sample()[:5], indent=2))"

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

127 tests. `tests/test_killer.py` spawns and kills real throwaway Python
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
