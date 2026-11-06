"""The native window. One process, no browser, no HTTP server.

The sampler runs on its own daemon thread inside this process, so the
whole tool is a single ~40 MB python.exe. A WebView2 shell measured 436 MB
across seven processes on the machine this was built for, which is a poor
trade for a tool whose job is reclaiming RAM.

Tk is not thread safe. Every widget touch happens on the main thread:
background work posts its result back through `root.after`.
"""

import threading
import time
import tkinter as tk

import psutil
from tkinter import font as tkfont
from tkinter import ttk

from monitor import attention, flash, killer, local_analysis, safety, viewmodel as vm
from monitor.advice import read_advice
from monitor.constants import ADVICE_PATH, BLOCKED_NAMES, BLOCKED_PIDS
from monitor.sampler import Sampler

REFRESH_MS = 2000

BG = "#11131a"
PANEL = "#171a23"
LINE = "#262a36"
TEXT = "#e6e8ef"
MUTED = "#868da3"
ACCENT = "#5b8cff"
WARN = "#e0a33e"
BAD = "#e05a5a"
GOOD = "#4ec98a"


def annotate_killable(groups):
    """Mark each group killable, matching what the server does for the page."""
    for group in groups:
        blocked = group["name"] in BLOCKED_NAMES
        pid_blocked = any(pid in BLOCKED_PIDS for pid in group.get("pids", []))
        group["killable"] = not (blocked or pid_blocked)
    return groups


class SysmonApp:
    def __init__(self, root, sampler):
        self.root = root
        self.sampler = sampler
        self.checked = set()
        self.touched = False  # once the user ticks anything, advice stops steering
        self.busy = False
        self.rows_by_name = {}
        self._row_widgets = []
        self.alert_state = attention.AlertState()
        self.alerting = False

        own_pid, ancestors = sampler.own_context()
        self.protected = safety.protected_pids(own_pid, ancestors)

        root.title("sysmon")
        root.geometry("1180x760")
        root.minsize(900, 600)
        root.configure(bg=BG)

        self.f_body = tkfont.Font(family="Segoe UI", size=9)
        self.f_small = tkfont.Font(family="Segoe UI", size=8)
        self.f_head = tkfont.Font(family="Segoe UI", size=9, weight="bold")
        self.f_meter = tkfont.Font(family="Segoe UI", size=19)

        self._build_sidebar()
        self._build_main()
        # Looking at the window is the acknowledgement. Stop the flash.
        root.bind("<FocusIn>", self._on_focus)
        self.refresh()

    # ---------------------------------------------------------------- layout

    def _build_sidebar(self):
        side = tk.Frame(self.root, bg=PANEL, width=360)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        head = tk.Frame(side, bg=PANEL)
        head.pack(fill="x", padx=14, pady=(14, 6))
        tk.Label(head, text="PROCESSES", bg=PANEL, fg=TEXT, font=self.f_head).pack(side="left")
        self.lbl_count = tk.Label(head, text="", bg=PANEL, fg=MUTED, font=self.f_small)
        self.lbl_count.pack(side="right")

        # Canvas plus inner frame is the standard Tk scrolling idiom. There
        # are more rows than fit, and a non-scrolling list would hide them.
        wrap = tk.Frame(side, bg=PANEL)
        wrap.pack(fill="both", expand=True, padx=(10, 4))
        self.canvas = tk.Canvas(wrap, bg=PANEL, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.list_frame = tk.Frame(self.canvas, bg=PANEL)
        self.list_window = self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind(
            "<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>", lambda e: self.canvas.itemconfig(self.list_window, width=e.width)
        )
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        tk.Frame(side, bg=LINE, height=1).pack(fill="x", padx=14, pady=(8, 0))
        adv_head = tk.Frame(side, bg=PANEL)
        adv_head.pack(fill="x", padx=14, pady=(8, 2))
        self.lbl_source = tk.Label(
            adv_head, text="CLAUDE SUGGESTS", bg=PANEL, fg=MUTED, font=self.f_small
        )
        self.lbl_source.pack(side="left")
        self.lbl_age = tk.Label(adv_head, text="", bg=PANEL, fg=MUTED, font=self.f_small)
        self.lbl_age.pack(side="right")
        self.lbl_advice = tk.Message(
            side,
            text="No advice yet. Ask Claude to check your laptop.",
            width=320,
            bg=PANEL,
            fg=MUTED,
            font=self.f_body,
            justify="left",
        )
        self.lbl_advice.pack(anchor="w", padx=14)
        self.lbl_notes = tk.Message(
            side, text="", width=320, bg=PANEL, fg=MUTED, font=self.f_small, justify="left"
        )
        self.lbl_notes.pack(anchor="w", padx=14, pady=(4, 0))
        self.lbl_dead = tk.Message(
            side, text="", width=320, bg=PANEL, fg=WARN, font=self.f_small, justify="left"
        )
        self.lbl_dead.pack(anchor="w", padx=14, pady=(4, 0))

        self.btn = tk.Button(
            side,
            text="Optimize",
            command=self.on_optimize,
            bg="#2a3043",
            fg=MUTED,
            activebackground=ACCENT,
            activeforeground="white",
            relief="flat",
            font=self.f_head,
            pady=8,
            state="disabled",
            cursor="arrow",
        )
        self.btn.pack(fill="x", padx=14, pady=(10, 6))

        self.results = tk.Frame(side, bg=PANEL)
        self.results.pack(fill="x", padx=14, pady=(0, 12))

    def _build_main(self):
        main = tk.Frame(self.root, bg=BG)
        main.pack(side="right", fill="both", expand=True)

        self.banner = tk.Label(
            main,
            text="",
            bg=WARN,
            fg="#1a1300",
            font=self.f_head,
            anchor="w",
            padx=14,
            pady=7,
        )
        # Packed and unpacked by _render_banner, so it takes no space when quiet.

        self.meters = tk.Frame(main, bg=BG)
        self.meters.pack(fill="x", padx=22, pady=(22, 10))
        self.ram = self._meter(self.meters, "RAM", ACCENT)
        self.cpu = self._meter(self.meters, "CPU", GOOD)

        panel = tk.Frame(main, bg=PANEL)
        panel.pack(fill="both", expand=True, padx=22, pady=(0, 22))
        bar = tk.Frame(panel, bg=PANEL)
        bar.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(bar, text="WATCHING", bg=PANEL, fg=MUTED, font=self.f_small).pack(side="left")
        self.lbl_window = tk.Label(bar, text="", bg=PANEL, fg=MUTED, font=self.f_small)
        self.lbl_window.pack(side="right")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "sysmon.Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            rowheight=24,
            borderwidth=0,
        )
        style.configure(
            "sysmon.Treeview.Heading",
            background=PANEL,
            foreground=MUTED,
            relief="flat",
            borderwidth=0,
        )
        style.map("sysmon.Treeview", background=[("selected", "#1d2740")])

        cols = ("ram", "cpu", "idle", "state")
        self.tree = ttk.Treeview(
            panel, columns=cols, show="tree headings", style="sysmon.Treeview", height=16
        )
        self.tree.heading("#0", text="PROCESS")
        self.tree.column("#0", width=240, anchor="w")
        for col, title, width in zip(cols, ("RAM", "CPU", "IDLE", "STATE"), (90, 70, 70, 90)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="e" if col != "state" else "w")
        self.tree.tag_configure("growing", foreground=WARN)
        self.tree.tag_configure("idle", foreground=MUTED)
        self.tree.tag_configure("quiet", foreground=MUTED)
        self.tree.tag_configure("active", foreground=GOOD)
        self.tree.pack(fill="both", expand=True, padx=16, pady=(4, 16))

    def _meter(self, parent, label, color):
        box = tk.Frame(parent, bg=PANEL)
        box.pack(side="left", fill="x", expand=True, padx=(0, 14))
        tk.Label(box, text=label, bg=PANEL, fg=MUTED, font=self.f_small).pack(
            anchor="w", padx=16, pady=(14, 0)
        )
        value = tk.Label(box, text="--", bg=PANEL, fg=TEXT, font=self.f_meter)
        value.pack(anchor="w", padx=16, pady=(2, 8))
        track = tk.Frame(box, bg="#232838", height=7)
        track.pack(fill="x", padx=16, pady=(0, 16))
        track.pack_propagate(False)
        fill = tk.Frame(track, bg=color, height=7)
        fill.place(x=0, y=0, relwidth=0.0, relheight=1.0)
        return {"value": value, "fill": fill, "color": color}

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    # ----------------------------------------------------------------- state

    def refresh(self):
        try:
            facts = self.sampler.facts()
            snapshot = self.sampler.latest()
            groups = annotate_killable(facts.get("groups", []))
            advice = read_advice(ADVICE_PATH)

            window_seconds = facts.get("window_seconds") or 0.0
            totals = facts.get("totals", {})
            age = vm.advice_age(advice)

            # Claude's advice wins while it is fresh. Built-in rules take
            # over when it is absent or expired, so the app keeps doing its
            # job with no session attached.
            local = None
            if not advice.get("available") or age["expired"]:
                local = local_analysis.build_local_advice(
                    groups, window_seconds, totals, self._uptime_days()
                )

            if not self.touched:
                if local is not None:
                    self.checked = set(local["suggested_names"])
                else:
                    self.checked = vm.advice_checked_names(advice, snapshot)

            self._render_meters(totals)
            self._render_window(facts)
            self._render_rows(groups, window_seconds)
            self._render_trend(groups, window_seconds)
            self._render_advice(advice, age, vm.dead_targets(advice, snapshot), local)
            self._update_button(snapshot)
            self._evaluate_attention(advice, totals, groups, snapshot, age, local)
        except Exception as exc:  # a render fault must not stop the clock
            self.lbl_window.configure(text=f"refresh error: {exc}")
        self.root.after(REFRESH_MS, self.refresh)

    def _render_meters(self, totals):
        ram = totals.get("ram_percent", 0.0)
        used = vm.format_bytes(totals.get("ram_used", 0))
        total = vm.format_bytes(totals.get("ram_total", 0))
        self.ram["value"].configure(text=f"{used} / {total}    {ram:.0f}%")
        self.ram["fill"].place_configure(relwidth=min(ram, 100) / 100)
        self.ram["fill"].configure(bg=BAD if ram >= 85 else ACCENT)

        cpu = totals.get("cpu_percent", 0.0)
        self.cpu["value"].configure(text=f"{cpu:.0f}%")
        self.cpu["fill"].place_configure(relwidth=min(cpu, 100) / 100)

    def _render_window(self, facts):
        minutes = (facts.get("window_seconds") or 0) / 60
        self.lbl_window.configure(
            text=f"{facts.get('sample_count', 0)} samples over {minutes:.0f} min"
        )

    def _render_rows(self, groups, window_seconds):
        rows = vm.row_models(groups, self.checked, window_seconds=window_seconds)
        self.lbl_count.configure(text=f"{len(groups)} apps")

        # Rebuilding 40 rows every 2s flickers and loses scroll position, so
        # widgets are created once per name and only their text is updated.
        if len(rows) != len(self._row_widgets):
            for widget in self.list_frame.winfo_children():
                widget.destroy()
            self._row_widgets = [self._make_row() for _ in rows]

        for widget_set, row in zip(self._row_widgets, rows):
            self._fill_row(widget_set, row)

    def _make_row(self):
        frame = tk.Frame(self.list_frame, bg=PANEL)
        frame.pack(fill="x", pady=1)
        var = tk.IntVar(value=0)
        box = tk.Checkbutton(
            frame,
            variable=var,
            bg=PANEL,
            activebackground=PANEL,
            selectcolor=PANEL,
            highlightthickness=0,
            bd=0,
        )
        box.pack(side="left")
        rss = tk.Label(frame, text="", bg=PANEL, fg=MUTED, font=self.f_body)
        rss.pack(side="right", padx=(4, 8))
        text = tk.Frame(frame, bg=PANEL)
        text.pack(side="left", fill="x", expand=True)
        name = tk.Label(text, text="", bg=PANEL, fg=TEXT, font=self.f_body, anchor="w")
        name.pack(fill="x")
        detail = tk.Label(text, text="", bg=PANEL, fg=MUTED, font=self.f_small, anchor="w")
        detail.pack(fill="x")
        return {
            "frame": frame,
            "box": box,
            "var": var,
            "name": name,
            "detail": detail,
            "rss": rss,
            "bound": None,
        }

    def _fill_row(self, w, row):
        w["name"].configure(text=row["name"], fg=TEXT if row["killable"] else MUTED)
        w["detail"].configure(text=row["detail"])
        w["rss"].configure(text=row["rss_text"])
        w["box"].configure(state="normal" if row["killable"] else "disabled")

        if w["var"].get() != int(row["checked"]):
            w["var"].set(int(row["checked"]))

        if w["bound"] != row["name"]:
            w["bound"] = row["name"]
            w["box"].configure(command=lambda n=row["name"], v=w["var"]: self._toggle(n, v))

    def _toggle(self, name, var):
        self.touched = True
        if var.get():
            self.checked.add(name)
        else:
            self.checked.discard(name)
        self._update_button(self.sampler.latest())

    def _render_trend(self, groups, window_seconds):
        self.tree.delete(*self.tree.get_children())
        for group in groups[:16]:
            state = vm.process_state(group, window_seconds)
            self.tree.insert(
                "",
                "end",
                text=group["name"],
                values=(
                    vm.format_bytes(group.get("rss", 0)),
                    f"{group.get('cpu_percent', 0.0):.1f}%",
                    vm.format_idle(group.get("idle_seconds", 0)),
                    state,
                ),
                tags=(state,),
            )

    # ------------------------------------------------------------- attention

    def _uptime_days(self):
        try:
            return (time.time() - psutil.boot_time()) / 86400
        except Exception:
            return 0.0

    def _evaluate_attention(self, advice, totals, groups, snapshot, age, local):
        if local is not None:
            reclaimable = local["reclaimable"]
            active, reason = attention.should_alert(
                local, totals, reclaimable, local=True
            )
        else:
            targets = vm.advice_checked_names(advice, snapshot)
            reclaimable = attention.reclaimable_bytes(groups, targets)
            active, reason = attention.should_alert(
                advice, totals, reclaimable, expired=age["expired"]
            )

        if self.alert_state.update(active, attention.alert_key(advice), time.monotonic()):
            # Flashing a window that is already in front is just noise.
            if self.root.focus_displayof() is None:
                flash.start(self.root)

        self._render_banner(active, reason)
        self.root.title(attention.title_for(active, reclaimable))
        self.alerting = active

    def _render_banner(self, active, reason):
        if active and not self.banner.winfo_ismapped():
            self.banner.pack(side="top", fill="x", before=self.meters)
        elif not active and self.banner.winfo_ismapped():
            self.banner.pack_forget()
        if active:
            self.banner.configure(text=f"⚠  {reason}")

    def _on_focus(self, _event=None):
        flash.stop(self.root)
        self.alert_state.acknowledge(time.monotonic())

    def _render_advice(self, advice, age, dead, local):
        if local is not None:
            # Provenance matters more than tidiness here. The user must
            # always know whether they are reading judgement or a rule.
            self.lbl_source.configure(text="BUILT-IN RULES", fg=WARN)
            self.lbl_age.configure(text="no Claude session", fg=MUTED)
            self.lbl_advice.configure(text=local["summary"], fg=TEXT)
            self.lbl_notes.configure(text=local["notes"])
            self.lbl_dead.configure(text="")
            return

        self.lbl_source.configure(text="CLAUDE SUGGESTS", fg=MUTED)
        if not advice.get("available"):
            text = (
                f"advice.json could not be read: {advice['error']}"
                if advice.get("error")
                else "No advice yet. Ask Claude to check your laptop."
            )
            self.lbl_advice.configure(text=text, fg=MUTED)
            self.lbl_notes.configure(text="")
            self.lbl_dead.configure(text="")
            self.lbl_age.configure(text="")
            return

        # Stale advice is greyed rather than hidden. It may still be right,
        # but it should not read as a statement about the machine right now.
        self.lbl_age.configure(
            text=f"{age['label']} · stale" if age["stale"] else age["label"],
            fg=WARN if age["stale"] else MUTED,
        )
        self.lbl_advice.configure(
            text=advice.get("summary") or "Claude wrote advice with no summary.",
            fg=MUTED if age["stale"] else TEXT,
        )
        self.lbl_notes.configure(text=advice.get("notes") or "")

        notes = []
        if dead:
            shown = ", ".join(dead[:3]) + (" and more" if len(dead) > 3 else "")
            notes.append(f"{shown} no longer running")
        if age["expired"]:
            notes.append("Ask Claude for a fresh check.")
        self.lbl_dead.configure(text="  ".join(notes))

    def _update_button(self, snapshot):
        if self.busy:
            return
        count = len(vm.selected_targets(snapshot, self.checked))
        if count:
            self.btn.configure(
                text=f"Optimize   {count} processes",
                state="normal",
                bg=ACCENT,
                fg="white",
                cursor="hand2",
            )
        else:
            self.btn.configure(
                text="Optimize", state="disabled", bg="#2a3043", fg=MUTED, cursor="arrow"
            )

    # ------------------------------------------------------------- the action

    def on_optimize(self):
        targets = vm.selected_targets(self.sampler.latest(), self.checked)
        self._start_kill(targets, force=False)

    def _start_kill(self, targets, force):
        if self.busy or not targets:
            return
        self.busy = True
        self.btn.configure(text="Working...", state="disabled", bg="#2a3043", fg=MUTED)
        for widget in self.results.winfo_children():
            widget.destroy()

        def work():
            snapshot = self.sampler.latest()
            allowed, rejected = safety.filter_targets(targets, snapshot, self.protected)
            results = killer.kill_targets(allowed, force=force)
            # Tk is not thread safe. Hand the result back to the main thread.
            self.root.after(0, lambda: self._finish_kill(results, rejected))

        threading.Thread(target=work, daemon=True).start()

    def _finish_kill(self, results, rejected):
        self.busy = False
        summary = vm.summarize_results(results, rejected)

        if summary["freed_bytes"] > 0:
            self._result_line(f"Reclaimed {summary['freed_text']}", GOOD)
        for line in summary["lines"]:
            color = {
                "closed": GOOD,
                "refused": WARN,
                "failed": BAD,
                "rejected": BAD,
            }.get(line["status"], MUTED)
            self._result_line(f"{line['text']}  {line['right']}".strip(), color)

        for entry in summary["forceable"]:
            self._force_button(entry)

        self.checked.clear()
        self.touched = True
        self._update_button(self.sampler.latest())

    def _result_line(self, text, color):
        tk.Label(
            self.results,
            text=text,
            bg=PANEL,
            fg=color,
            font=self.f_small,
            anchor="w",
            justify="left",
            wraplength=320,
        ).pack(fill="x", pady=1)

    def _force_button(self, entry):
        proc = next(
            (p for p in self.sampler.latest() if p.get("pid") == entry.get("pid")), None
        )
        if not proc or proc.get("create_time") is None:
            return
        target = {"pid": proc["pid"], "create_time": proc["create_time"]}
        tk.Button(
            self.results,
            text=f"force close {entry.get('name')}",
            command=lambda t=target: self._start_kill([t], force=True),
            bg=PANEL,
            fg=WARN,
            activebackground=PANEL,
            activeforeground=WARN,
            relief="solid",
            bd=1,
            font=self.f_small,
            cursor="hand2",
        ).pack(fill="x", pady=2)


def main():
    sampler = Sampler()
    sampler.start()
    root = tk.Tk()
    SysmonApp(root, sampler)
    try:
        root.mainloop()
    finally:
        sampler.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
