"""Reads the plan Claude writes to data/advice.json.

This file is hand-editable and written by another process, so every read
assumes it is absent, half-written or wrong. A bad advice file must never
take the live view down with it.
"""

import json


def _empty(error=None):
    return {
        "available": False,
        "summary": "",
        "targets": [],
        "notes": "",
        "written_at": "",
        "urgency": "",
        "error": error,
    }


def read_advice(path):
    """Parse advice.json. Never raises, whatever is on disk."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty()
    except (OSError, ValueError) as exc:
        return _empty(str(exc))

    if not isinstance(raw, dict):
        return _empty("advice.json must contain a JSON object")

    targets = []
    for entry in raw.get("targets") or []:
        if not isinstance(entry, dict):
            continue
        # A target with no pid cannot be checked or acted on, so drop it
        # here rather than letting it reach safety as a malformed request.
        if not isinstance(entry.get("pid"), int):
            continue
        targets.append(entry)

    return {
        "available": True,
        "summary": str(raw.get("summary") or ""),
        "targets": targets,
        "notes": str(raw.get("notes") or ""),
        "written_at": str(raw.get("written_at") or ""),
        # Lets Claude force an alert when live thresholds would not fire,
        # for a leak or a runaway process on an otherwise quiet machine.
        "urgency": str(raw.get("urgency") or "").lower(),
        "error": None,
    }
