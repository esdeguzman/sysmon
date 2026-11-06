"""The advice pane must stay bounded and scrollable.

A long summary used to grow the pane until the Optimize button and the
Claude panel were pushed off the bottom of the window. These build the
real widgets rather than mocking them, because the bug was entirely in
geometry management.

Two things this harness learned the hard way:

* The window must be MAPPED, or Tk reports `yview()` as (0.0, 0.033) for
  a single short line because the widget has no layout at all. It is
  placed far offscreen so nothing appears on the user's display.
* Only ONE `Tk()` root per process. Creating and destroying several makes
  Tk lose its own library path and fail with "Can't find a usable
  tk.tcl" partway through the run.
"""

import tkinter as tk

import pytest

from monitor.app import SysmonApp


class FakeSampler:
    interval = 2.0

    def latest(self):
        return []

    def history(self):
        return []

    def totals(self):
        return {"ram_percent": 50.0, "cpu_percent": 5.0, "ram_used": 0, "ram_total": 1}

    def facts(self):
        return {
            "groups": [],
            "totals": self.totals(),
            "sample_count": 0,
            "window_seconds": 0.0,
        }

    def own_context(self):
        return 99999, []


LONG = (
    "RAM is 67.9 percent with 10.9 GB free, so there is no pressure. "
    "The sampler has only been up 12 seconds, so idle_seconds and rss_trend "
    "carry no weight in this snapshot and the targets below are picked on "
    "size and age instead. " * 6
)
SHORT = "All quiet."


@pytest.fixture(scope="module")
def app():
    root = tk.Tk()
    root.geometry("1180x760-3000-3000")  # mapped, far offscreen
    instance = SysmonApp(root, FakeSampler())
    root.update()
    yield instance
    try:
        root.destroy()
    except tk.TclError:
        pass


def settle(app):
    """Let Tk lay out and run the after_idle scrollbar sync."""
    for _ in range(3):
        app.root.update_idletasks()
        app.root.update()


def show(app, summary, notes=""):
    app._set_advice(summary, "summary", notes, "")
    settle(app)


class TestAdvicePaneStaysBounded:
    def test_the_pane_has_a_fixed_line_height(self, app):
        assert int(app.advice_text.cget("height")) <= 12

    def test_long_advice_does_not_grow_the_widget(self, app):
        show(app, SHORT)
        before = app.advice_text.winfo_height()
        show(app, LONG, LONG)
        assert app.advice_text.winfo_height() == before

    def test_the_controls_stay_packed_under_long_advice(self, app):
        show(app, LONG, LONG)
        # the two things the overflow bug pushed off the window
        assert app.btn.winfo_manager() == "pack"
        assert app.btn_claude.winfo_manager() == "pack"

    def test_the_controls_are_visible_on_screen_under_long_advice(self, app):
        show(app, LONG, LONG)
        sidebar_height = app.btn.master.winfo_height()
        # y + height must fall inside the sidebar, not past its bottom edge
        assert app.btn.winfo_y() + app.btn.winfo_height() <= sidebar_height


class TestAdviceScrollbar:
    """The scrollbar is always present, never auto-hidden.

    Auto-hiding meant re-deciding on every content change against a
    yview() Tk had not recomputed yet, and on a short-to-long transition
    it stayed hidden with no error raised. A visible empty trough is the
    cheaper correctness.
    """

    def test_scrollbar_is_packed_for_long_advice(self, app):
        show(app, LONG, LONG)
        assert app.advice_bar.winfo_manager() == "pack"

    def test_scrollbar_stays_packed_for_short_advice(self, app):
        show(app, SHORT)
        assert app.advice_bar.winfo_manager() == "pack"

    def test_scrollbar_survives_a_short_to_long_switch(self, app):
        show(app, SHORT)
        show(app, LONG, LONG)
        assert app.advice_bar.winfo_manager() == "pack"

    def test_scrollbar_has_real_width(self, app):
        # packed after an expand=True sibling, it rendered at zero width
        show(app, LONG, LONG)
        assert app.advice_bar.winfo_width() > 1

    def test_long_advice_actually_overflows_and_can_be_scrolled(self, app):
        show(app, LONG, LONG)
        assert app.advice_text.yview()[1] < 1.0
        app.advice_text.yview_moveto(1.0)
        settle(app)
        assert app.advice_text.yview()[1] == pytest.approx(1.0)

    def test_short_advice_fits_without_scrolling(self, app):
        show(app, SHORT)
        assert app.advice_text.yview() == (0.0, 1.0)

    def test_long_advice_starts_at_the_top(self, app):
        show(app, LONG, LONG)
        assert app.advice_text.yview()[0] == pytest.approx(0.0)
