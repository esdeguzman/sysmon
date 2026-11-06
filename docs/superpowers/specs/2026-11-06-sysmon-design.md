# sysmon, Design Spec

**Date:** 2026-11-06
**Status:** Approved, implementation starting
**Machine:** one Windows 11 laptop, one user

## 1. Goal

A local RAM and process monitor with a browser sidebar. It samples the machine
in real time, writes a snapshot that Claude Code reads directly from disk, and
renders Claude's kill plan back into the sidebar behind an Optimize button.

### Success criteria

- `run.cmd` starts the monitor and opens the dashboard. No install step.
- The sidebar lists running killable processes, grouped by executable, updating
  every 2 seconds.
- Asking Claude Code "check my laptop" fills the suggestion panel within one
  poll cycle, with no API call and no network request.
- Optimize closes the targeted processes, reports what refused to close, and
  shows the RAM actually reclaimed.
- No action by the tool can bluescreen Windows or kill the session the user is
  talking to Claude through.

### Out of scope

Startup app management, disk cleanup, service tuning, registry edits, GPU and
temperature sensors, multi-machine support, packaging, authentication.

## 2. Decisions

| Topic | Decision | Rejected alternative |
|---|---|---|
| Backend | Python 3.14 stdlib `ThreadingHTTPServer` plus `psutil` 7.2.2, both already installed | Node, which would scrape `tasklist` for less accurate data |
| Frontend | Vanilla HTML, CSS and JS, no build step | React, which needs a toolchain for one page |
| Claude link | File handoff: the sampler writes `data/snapshot.json`, Claude writes `data/advice.json` | `claude -p` subprocess, which spends plan tokens in a session that has no memory of the conversation |
| Analysis | Claude, in the user's existing session | Local heuristics, which cannot tell what the user is in the middle of doing |
| History | Ring buffer, 2 second interval, 15 minutes deep, memory only | SQLite, which adds a growing file for data the advice does not use |
| Kill policy | Close request first, wait 5 seconds, recheck, report refusals. Force only on a second explicit press, per process | Unconditional `taskkill /F`, which destroys unsaved work with no prompt |
| Bind address | `127.0.0.1` only | `0.0.0.0`, which exposes a kill endpoint to the network |
| Target identity | `(pid, create_time)` pairs | Bare PIDs, which Windows recycles fast enough to kill the wrong process |

## 3. Architecture

```
sysmon/
├── monitor/
│   ├── __init__.py
│   ├── sampler.py      # psutil to ring buffer, background thread
│   ├── analysis.py     # samples to derived facts
│   ├── safety.py       # kill authorization
│   ├── killer.py       # close, wait, recheck, force
│   └── server.py       # HTTP routes
├── web/
│   ├── index.html
│   ├── app.js
│   └── style.css
├── data/
│   ├── snapshot.json   # written by sampler, read by Claude
│   └── advice.json     # written by Claude, polled by sidebar
├── tests/
└── run.cmd
```

`analysis.py` and `safety.py` are pure functions over data structures. Their
tests need no server, no browser and no process to kill.

### 3.1 `sampler.py`

A daemon thread samples every 2 seconds and appends to a `collections.deque`
capped at 450 entries, which is 15 minutes.

Each sample is a dict keyed by `(pid, create_time)`:

```python
{"pid": 4411, "create_time": 1762406400.0, "name": "chrome.exe",
 "rss": 268435456, "cpu_percent": 0.0, "username": "User",
 "exe": "C:\\Program Files\\Google\\Chrome\\chrome.exe"}
```

`cpu_percent` is read without a blocking interval. psutil returns the value
since the previous call on the same object, so the sampler keeps a persistent
`dict` of `psutil.Process` objects across samples rather than re-creating them.
Re-creating them every sample returns 0.0 forever, which would break idle
detection.

After each sample the thread writes `data/snapshot.json` atomically: write to
`snapshot.json.tmp`, then `os.replace`. A reader never sees a partial file.

### 3.2 `analysis.py`

Pure. Takes the deque, returns derived facts per process group.

| Fact | Definition |
|---|---|
| `idle_seconds` | Seconds since the last sample where `cpu_percent > 1.0`. Capped at the buffer depth and reported as `>=900` at the cap |
| `rss_trend` | Linear fit of RSS over the buffer, in MB per minute. Flagged `growing` above 5 MB/min sustained over 5 minutes |
| `group` | Processes sharing an executable name, with summed RSS, summed CPU, and the member PID list |
| `total` | System RAM used, available, percent, and the same for CPU |

Grouping is what makes the sidebar readable. Chrome is a dozen processes and
belongs on one row.

### 3.3 `safety.py`

Pure, and the unit that matters most. Input is a kill target and the current
snapshot. Output is `(allowed: bool, reason: str)`.

Four checks, all of which must pass:

1. **Name blocklist.** `System`, `Registry`, `smss.exe`, `csrss.exe`,
   `wininit.exe`, `winlogon.exe`, `services.exe`, `lsass.exe`, `LsaIso.exe`,
   `fontdrvhost.exe`, `dwm.exe`, `MemCompression`, `Idle`, and PIDs 0 and 4.
   Killing `lsass.exe` or `csrss.exe` bluescreens Windows at once. These never
   render as killable, and the server rejects them even when `advice.json`
   names them.
2. **Self-protection.** The monitor's own PID and every ancestor returned by
   walking `psutil.Process().parents()`, resolved once at startup. This covers
   the terminal running Claude Code when the monitor is started from it.
3. **PID reuse guard.** The target's `create_time` must equal the one in the
   current snapshot. A two second old page naming PID 4411 must not kill
   whatever inherited that number since.
4. **Presence.** The target must exist in the current snapshot. Arbitrary PIDs
   posted to the API are rejected.

The browser is a view with no authority. Every request is revalidated here
against the live process table.

### 3.4 `killer.py`

Given authorized targets:

1. The graceful path shells to `taskkill /PID <n>` without `/F`. That posts
   `WM_CLOSE` to the process window, letting an app with unsaved work show its
   own save dialog. `proc.terminate()` is not used for this step: on Windows it
   calls `TerminateProcess`, which is immediate and unconditional.
2. Wait up to 5 seconds, polling `psutil.pid_exists` plus a `create_time`
   match.
3. Anything still alive is reported as `refused`, with its reason where one is
   available.
4. Force is a separate call, `taskkill /F /PID <n>`, reached only by a second
   explicit press on that specific process. It runs through the same four
   safety checks.

Returns per target: `closed`, `refused`, or `failed` with the OS reason, plus
the RSS each freed.

### 3.5 `server.py`

`ThreadingHTTPServer` bound to `127.0.0.1:7777`.

| Route | Behavior |
|---|---|
| `GET /` | serves `web/index.html` |
| `GET /api/state` | current groups, derived facts, totals, and the parsed `advice.json` |
| `POST /api/kill` | body `{"targets": [{"pid": n, "create_time": t}], "force": false}`. Revalidates every target through `safety.py`, then calls `killer.py` |
| `GET /api/snapshot` | the raw current snapshot, for debugging |

Static files are served from `web/` by exact filename match, not by joining
user input to a path.

### 3.6 Frontend

One page, two columns. The sidebar holds the live process list, the suggestion
panel, and the Optimize button. The main pane holds RAM and CPU totals and the
trend readout.

- Polls `GET /api/state` every 2 seconds.
- Each group row has a checkbox. Claude's targets arrive pre-ticked. The user
  can tick and untick freely, so Optimize always acts on the checkboxes rather
  than on the advice file.
- With no `advice.json` the panel reads "No advice yet. Ask Claude to check
  your laptop." The live list works regardless.
- After Optimize, results render inline per process. Refusals get their own
  force button.

### 3.7 The advice contract

Claude writes `data/advice.json`:

```json
{
  "written_at": "2026-11-06T14:22:00",
  "summary": "RAM at 78 percent. Teams idle 42 minutes, 6 Chrome renderers idle 20 minutes.",
  "targets": [
    {"pid": 4411, "create_time": 1762406400.0, "name": "Teams.exe",
     "reason": "idle 42m, holding 900 MB", "est_mb": 900}
  ],
  "notes": "Code.exe left alone, it is active and holds unsaved work."
}
```

`targets` carries `create_time` so the PID reuse guard has something to check.
Malformed or missing files are treated as no advice.

## 4. Error handling

| Situation | Behavior |
|---|---|
| `psutil.AccessDenied` | Process is listed, greyed, labelled "needs admin". Not offered as killable |
| `psutil.NoSuchProcess` mid-sample | Skipped. Expected on every sample |
| `advice.json` missing or malformed | Sidebar shows "no advice yet". Live view unaffected |
| Port 7777 busy | Startup error naming the port, exit code 1 |
| Kill denied by the OS | Reported per process with the OS reason |
| Snapshot write interrupted | Atomic temp plus `os.replace`, so readers never see a partial file |
| Browser posts a stale target | Rejected by the PID reuse guard with "process changed, refresh" |

## 5. Testing

Automated, pytest.

- `safety.py`, the heaviest coverage: every blocklisted name rejected, PID 0
  and 4 rejected, own PID rejected, each ancestor rejected, `create_time`
  mismatch rejected, absent PID rejected, and an `advice.json` naming
  `lsass.exe` rejected end to end.
- `analysis.py`: `idle_seconds` from synthetic buffers including the cap case,
  `rss_trend` on flat, growing and noisy series, grouping of multi-process
  apps, empty buffer.
- `killer.py`: spawns real throwaway Python child processes and kills them.
  One child ignores `WM_CLOSE`, so the refusal path is exercised for real
  rather than mocked.
- `server.py`: route smoke tests, malformed body rejection, and an assertion
  that the bind address is loopback.

Manual checklist.

1. `run.cmd` starts the server and opens the dashboard.
2. The process list populates and updates.
3. `lsass.exe` and `csrss.exe` do not appear as killable.
4. Ask Claude Code "check my laptop". The suggestion panel fills within one
   poll cycle.
5. Open Notepad with unsaved text, target it, press Optimize. It refuses and
   shows its save dialog.
6. Press force on that refusal. It closes.
7. Hand-edit `advice.json` to name `lsass.exe`, press Optimize. Rejected with a
   reason, and Windows stays up.

## 6. Open questions

None.
