# sysmon

A local RAM and process monitor for one Windows laptop. Native window, with a
sidebar that renders Claude Code's kill plan behind an Optimize button.

## Running it

    run.cmd

That opens the window. One process, measured at 37 MB, no browser and no
server. Nothing to install: Python's standard library plus `psutil`.

## Why not a web page

It was a web page first, served on localhost. For a tool whose job is
reclaiming RAM, the shell it runs in is part of the measurement, so the
options were measured rather than assumed:

| Shell | RAM | Processes |
|---|---|---|
| Tkinter window | 37 MB | 1 |
| Local server plus a browser tab | 52 MB plus the tab | 1 plus the browser's |
| pywebview / WebView2 | 436 MB | 7 |

WebView2 loses because standalone it starts its own Chromium stack rather
than sharing the browser already running.

The web UI still works and its tests still run. `run-web.cmd` serves it on
http://127.0.0.1:7777 for debugging, where `curl` against `/api/state` and
`/api/kill` is useful.

## How the Claude part works

The app never talks to any model. The sampler writes `data/snapshot.json`,
Claude Code reads that file from disk and writes `data/advice.json`, and the
sidebar picks it up on the next 2 second refresh. No API key, no network
request, no token spend beyond the conversation you are already having.

Ask Claude Code "check my laptop" with the monitor running.

## Telling you when it matters

The window flashes its taskbar button, badges its title and shows a banner
only when RAM is at or above 85 percent and there is at least 1 GB worth
closing, or when RAM passes 94 percent, or when Claude explicitly marks the
advice urgent. Nothing else raises an alert, on purpose: one that fires
often is one you stop seeing.

Focusing the window stops the flash. A standing condition re-flashes at most
once every 15 minutes.

## Asking Claude from inside the app

The sidebar has a CLAUDE panel with a dot that blinks green while a run is
alive, one button that starts and stops it, and a read-only pane showing what
Claude is doing. It runs the `claude` CLI you already have installed, so there
is no API key involved.

Each press is a fresh session that spends plan tokens, so nothing runs unless
you press it. The one exception is RAM at or above 94 percent, where it asks
once on its own, with a 30 minute cooldown.

It is given Read and Write only, and never a permission bypass flag.

## It works without Claude

When there is no advice file, or it is over 2 hours old, the app falls back to
built-in rules and the sidebar says BUILT-IN RULES rather than CLAUDE SUGGESTS.
They flag apps that have used no CPU for the whole window, and apps whose
memory is climbing.

They are deliberately timid about what they pre-tick. Editors, terminals, VPN
and network processes, developer processes, and anything with 20 or more
processes get reported with a reason but never selected for you, because a
rule cannot tell an idle app from one you are coming back to. You can still
tick them yourself.

Rules alert at a higher bar than Claude does: 90 percent RAM and 2 GB, against
85 and 1.

## The advice ages

The sidebar shows how old Claude's advice is. Past 30 minutes it is labelled
stale and greyed. Past 2 hours it stops counting for alerting, so an old file
cannot flash at you about a machine that has moved on. Targets whose process
has exited are named.

The monitoring itself needs no Claude session. Sampling, the process list and
the Optimize button are all local. Only new advice needs you to ask.

## What it will not do

- Kill anything system-critical. `lsass.exe`, `csrss.exe`, `winlogon.exe` and
  the rest of the blocklist are rejected even if the advice file names them.
- Kill the terminal running your Claude session, or the monitor itself.
- Force-kill anything on the first press. Optimize sends a close request and
  reports what refused, so an app with unsaved work gets to ask you first.
  Force is a second, per-process click.
- Reach the network. The optional web server binds `127.0.0.1` only.

## Tests

    python -m pytest

224 tests. `tests/test_killer.py` spawns and kills real throwaway processes,
so the run takes about 6 seconds.
