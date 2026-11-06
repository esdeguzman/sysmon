"""Bulk per-process memory and CPU in one syscall.

psutil opens a handle per process for `memory_info` and `cpu_percent`.
Measured on the target machine: 2.3 ms for pid/name/create_time, and
764 ms once memory_info is added, across 347 processes. The sampler runs
every 2 seconds, so that put the sampler thread at a 75 percent duty
cycle and froze the Tk main loop for ~1.5 s at a time. Dragging the
window stalled, which is how it was noticed.

`NtQuerySystemInformation(SystemProcessInformation)` returns every
process, with working set and CPU times, in a single call: 6 ms for the
same 347 processes.

**Identity deliberately still comes from psutil.** This module returns
memory and CPU only, keyed by pid. The create_time here disagrees with
psutil's at about 5e-7, and `killer._gone` compares create_time to decide
whether a process died. Had identity been taken from here, every kill
would have compared unequal, reported "already gone", and quietly killed
nothing. The names differ too: this call reports "Memory Compression"
where psutil reports "MemCompression", and only the latter is in
BLOCKED_NAMES.
"""

import ctypes
from ctypes import wintypes

SystemProcessInformation = 5
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004

try:
    _ntdll = ctypes.WinDLL("ntdll")
except (OSError, AttributeError):  # not Windows
    _ntdll = None


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", ctypes.c_void_p),
    ]


class _SYSTEM_PROCESS_INFORMATION(ctypes.Structure):
    """Only the prefix up to WorkingSetSize matters; the tail is padding."""

    _fields_ = [
        ("NextEntryOffset", wintypes.ULONG),
        ("NumberOfThreads", wintypes.ULONG),
        ("WorkingSetPrivateSize", ctypes.c_longlong),
        ("HardFaultCount", wintypes.ULONG),
        ("NumberOfThreadsHighWatermark", wintypes.ULONG),
        ("CycleTime", ctypes.c_ulonglong),
        ("CreateTime", ctypes.c_longlong),
        ("UserTime", ctypes.c_longlong),
        ("KernelTime", ctypes.c_longlong),
        ("ImageName", _UNICODE_STRING),
        ("BasePriority", ctypes.c_long),
        ("UniqueProcessId", ctypes.c_void_p),
        ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ("HandleCount", wintypes.ULONG),
        ("SessionId", wintypes.ULONG),
        ("UniqueProcessKey", ctypes.c_void_p),
        ("PeakVirtualSize", ctypes.c_size_t),
        ("VirtualSize", ctypes.c_size_t),
        ("PageFaultCount", wintypes.ULONG),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
    ]


def available():
    return _ntdll is not None


def memory_and_cpu():
    """{pid: {"rss": bytes, "cpu_ns": int}} for every process.

    Returns an empty dict rather than raising if the call fails, so the
    caller can fall back to psutil instead of the sampler dying.
    """
    if _ntdll is None:
        return {}

    size = 1 << 20
    buf = None
    for _ in range(8):  # the process list can grow between sizing and reading
        buf = ctypes.create_string_buffer(size)
        needed = wintypes.ULONG(0)
        status = _ntdll.NtQuerySystemInformation(
            SystemProcessInformation, buf, size, ctypes.byref(needed)
        )
        if status == 0:
            break
        if (status & 0xFFFFFFFF) == STATUS_INFO_LENGTH_MISMATCH:
            size = max(needed.value + (1 << 16), size * 2)
            continue
        return {}
    else:
        return {}

    out = {}
    offset = 0
    try:
        while True:
            entry = _SYSTEM_PROCESS_INFORMATION.from_buffer(buf, offset)
            pid = entry.UniqueProcessId or 0
            out[pid] = {
                "rss": entry.WorkingSetSize,
                # UserTime and KernelTime are in 100 ns units.
                "cpu_ns": (entry.UserTime + entry.KernelTime) * 100,
            }
            if entry.NextEntryOffset == 0:
                break
            offset += entry.NextEntryOffset
    except (ValueError, OSError):
        # A truncated or shifting buffer gives whatever was read cleanly.
        return out
    return out
