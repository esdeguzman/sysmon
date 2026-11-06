# sysmon

A local RAM and process monitor for one Windows laptop, with a sidebar that
renders Claude Code's kill plan behind an Optimize button.

## Running it

    run.cmd

That starts the sampler, serves the dashboard on http://127.0.0.1:7777 and
opens your browser. Nothing to install: it uses Python's standard library
plus `psutil`.

## How the Claude part works

The dashboard never talks to any model. The sampler writes
`data/snapshot.json`, Claude Code reads that file from disk and writes
`data/advice.json`, and the sidebar polls for it. No API key, no network
request, no token spend beyond the conversation you are already having.

Ask Claude Code "check my laptop" with the monitor running.

## What it will not do

- Kill anything system-critical. `lsass.exe`, `csrss.exe`, `winlogon.exe` and
  the rest of the blocklist are rejected server-side even if the advice file
  names them.
- Kill the terminal running your Claude session, or the monitor itself.
- Force-kill anything on the first press. Optimize sends a close request and
  reports what refused, so an app with unsaved work gets to ask you first.
  Force is a second, per-process click.
- Listen on anything but `127.0.0.1`.

## Tests

    python -m pytest
