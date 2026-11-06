# sysmon

A local RAM and process monitor. Claude reads a snapshot file and writes an
advice file. No API call is involved.

## The loop

1. The user runs `run.cmd`. A sampler thread writes `data/snapshot.json`
   every 2 seconds and the dashboard opens at http://127.0.0.1:7777.
2. The user asks Claude to check the laptop.
3. Claude reads `data/snapshot.json`.
4. Claude writes `data/advice.json`.
5. The sidebar picks it up within one 2 second poll.

## Reading the snapshot

`data/snapshot.json` holds `totals` and `facts.groups`. Each group is one
executable with summed RSS across its processes, plus `idle_seconds`,
`rss_trend` in MB per minute, `growing`, and the member `pids`.

Per-process rows with `create_time` are at `GET /api/snapshot`, or run:

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

63 tests. `tests/test_killer.py` spawns and kills real throwaway Python
processes, so it takes about 6 seconds.
