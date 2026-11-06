"""Taskbar attention via FlashWindowEx. ctypes only, no dependency.

Tk's winfo_id() returns the handle of Tk's own child window, not the
top-level frame the taskbar draws. Flashing the child does nothing
visible, so the parent is resolved with GetParent first. That one line is
the whole reason this is a module rather than two inline calls.
"""

import ctypes
from ctypes import wintypes

FLASHW_STOP = 0x00000000
FLASHW_TRAY = 0x00000002
FLASHW_TIMERNOFG = 0x0000000C  # flash until the window comes to the foreground


class FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


def _toplevel_hwnd(tk_window):
    """The frame window the taskbar shows, not Tk's inner child."""
    hwnd = tk_window.winfo_id()
    parent = ctypes.windll.user32.GetParent(hwnd)
    return parent or hwnd


def _flash(tk_window, flags, count):
    try:
        info = FLASHWINFO(
            ctypes.sizeof(FLASHWINFO), _toplevel_hwnd(tk_window), flags, count, 0
        )
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        return True
    except Exception:
        # Attention is a nicety. Never take the window down for it.
        return False


def start(tk_window):
    """Pulse the taskbar button until the window is focused."""
    return _flash(tk_window, FLASHW_TRAY | FLASHW_TIMERNOFG, 0)


def stop(tk_window):
    return _flash(tk_window, FLASHW_STOP, 0)
