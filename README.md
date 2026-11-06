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
advice urgent. Everything else is silent on purpose: an alert that fires
often is one you stop seeing.

Focusing the window stops the flash. A standing condition re-flashes at most
once every 15 minutes.

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

127 tests. `tests/test_killer.py` spawns and kills real throwaway processes,
so the run takes about 6 seconds.
