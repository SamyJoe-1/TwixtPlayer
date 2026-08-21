"""The manager window: pick a file, pick a profile, see what will happen, play.

Tkinter is used deliberately -- it ships with Python, so the manager has no
dependencies of its own and starts instantly.
"""

from __future__ import annotations

import dataclasses
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import detect, player, profiles

VIDEO_TYPES = [
    ("Video files", "*.mkv *.mp4 *.avi *.mov *.webm *.m2ts *.ts *.wmv *.flv *.m4v"),
    ("All files", "*.*"),
]


class ManagerWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("twixt - frame interpolation manager")
        self.geometry("760x620")
        self.minsize(700, 580)

        self.source: detect.SourceInfo | None = None
        self.profile = profiles.get("film")
        self.display_fps = detect.display_refresh_hz()
        self.session: player.Session | None = None
        self._events: queue.Queue = queue.Queue()

        self._build()
        self._refresh_summary()
        self.after(100, self._drain_events)

    # --- layout -------------------------------------------------------------

    def _build(self) -> None:
        pad = dict(padx=12, pady=6)

        # file row
        top = ttk.LabelFrame(self, text="Source")
        top.pack(fill="x", **pad)
        self.file_var = tk.StringVar(value="(no file selected)")
        ttk.Label(top, textvariable=self.file_var, anchor="w").pack(
            side="left", fill="x", expand=True, padx=8, pady=8)
        ttk.Button(top, text="Open...", command=self.pick_file).pack(
            side="right", padx=8, pady=8)

        # profile + settings
        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, **pad)

        left = ttk.LabelFrame(mid, text="Profile")
        left.pack(side="left", fill="y", padx=(0, 8))

        self.profile_var = tk.StringVar(value="film")
        for key, prof in profiles.PRESETS.items():
            ttk.Radiobutton(left, text=prof.name, value=key,
                            variable=self.profile_var,
                            command=self.on_profile_change).pack(
                anchor="w", padx=10, pady=3)

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=8, padx=8)
        ttk.Button(left, text="Load .json...", command=self.load_profile).pack(
            fill="x", padx=10, pady=2)
        ttk.Button(left, text="Save as...", command=self.save_profile).pack(
            fill="x", padx=10, pady=2)

        right = ttk.LabelFrame(mid, text="Settings")
        right.pack(side="left", fill="both", expand=True)

        self.backend_var = tk.StringVar()
        self.grid_var = tk.StringVar()
        self.mask_var = tk.StringVar()
        self.rate_var = tk.StringVar()
        self.cap_var = tk.StringVar()

        self._combo(right, "Backend", self.backend_var,
                    ["rife", "mvtools", "svpflow"], 0)
        self._combo(right, "Frame rate", self.rate_var,
                    ["display", "multiple", "fixed"], 1)
        self._combo(right, "Motion grid", self.grid_var,
                    ["small", "medium", "large"], 2)
        self._combo(right, "Artifact masking", self.mask_var,
                    ["disabled", "weak", "average", "strong"], 3)
        self._combo(right, "Max output fps", self.cap_var,
                    ["60", "90", "100", "120", "144", "165", "180", "240"], 4)

        right.columnconfigure(1, weight=1)

        # summary
        box = ttk.LabelFrame(self, text="What will happen")
        box.pack(fill="x", **pad)
        self.summary = tk.Text(box, height=7, wrap="word", relief="flat",
                               background=self.cget("background"),
                               font=("Consolas", 9))
        self.summary.pack(fill="x", padx=8, pady=8)
        self.summary.configure(state="disabled")

        # actions
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", **pad)
        self.play_btn = ttk.Button(bottom, text="Play", command=self.play)
        self.play_btn.pack(side="left")
        ttk.Button(bottom, text="Benchmark", command=self.run_benchmark).pack(
            side="left", padx=6)
        ttk.Button(bottom, text="Save script...", command=self.save_script).pack(
            side="left")
        ttk.Button(bottom, text="Check install", command=self.show_doctor).pack(
            side="right")

        self.status = tk.StringVar(value=f"{self.display_fps:.0f} Hz display")
        ttk.Label(self, textvariable=self.status, anchor="w",
                  relief="sunken").pack(fill="x", side="bottom")

        self._load_profile_into_widgets()

    def _combo(self, parent, label, var, values, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w",
                                           padx=10, pady=6)
        box = ttk.Combobox(parent, textvariable=var, values=values,
                           state="readonly", width=18)
        box.grid(row=row, column=1, sticky="ew", padx=10, pady=6)
        box.bind("<<ComboboxSelected>>", lambda _e: self.on_setting_change())
        return box

    # --- state --------------------------------------------------------------

    def _load_profile_into_widgets(self) -> None:
        self.backend_var.set(self.profile.backend)
        self.grid_var.set(self.profile.grid)
        self.mask_var.set(self.profile.artifact_masking)
        self.rate_var.set(self.profile.rate_mode)
        self.cap_var.set(f"{self.profile.max_output_fps:g}")

    def _collect_widgets_into_profile(self) -> None:
        self.profile.backend = self.backend_var.get()
        self.profile.grid = self.grid_var.get()
        self.profile.artifact_masking = self.mask_var.get()
        self.profile.rate_mode = self.rate_var.get()
        try:
            self.profile.max_output_fps = float(self.cap_var.get())
        except ValueError:
            pass

    def on_profile_change(self) -> None:
        self.profile = profiles.get(self.profile_var.get())
        self._load_profile_into_widgets()
        self._refresh_summary()

    def on_setting_change(self) -> None:
        self._collect_widgets_into_profile()
        self._refresh_summary()

    # --- actions ------------------------------------------------------------

    def pick_file(self) -> None:
        path = filedialog.askopenfilename(title="Choose a video",
                                          filetypes=VIDEO_TYPES)
        if not path:
            return
        self.status.set("reading file...")
        self.update_idletasks()
        self.source = detect.probe(path)
        self.file_var.set(str(Path(path)))
        self.status.set(f"{self.display_fps:.0f} Hz display")
        self._refresh_summary()

    def load_profile(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Profile", "*.json")])
        if not path:
            return
        try:
            self.profile = profiles.Profile.load(Path(path))
        except Exception as exc:
            messagebox.showerror("twixt", f"Could not read that profile:\n{exc}")
            return
        self._load_profile_into_widgets()
        self._refresh_summary()

    def save_profile(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("Profile", "*.json")])
        if not path:
            return
        self._collect_widgets_into_profile()
        self.profile.save(Path(path))
        self.status.set(f"saved {Path(path).name}")

    def save_script(self) -> None:
        from .engine import generate_script
        path = filedialog.asksaveasfilename(defaultextension=".vpy",
                                            filetypes=[("VapourSynth", "*.vpy")])
        if not path:
            return
        self._collect_widgets_into_profile()
        info = self.source or detect.SourceInfo(Path("input"))
        Path(path).write_text(
            generate_script(self.profile, info.fps, self.display_fps,
                            standalone_source=str(info.path.resolve()),
                            matrix=info.matrix),
            encoding="utf-8")
        self.status.set(f"saved {Path(path).name}")

    def play(self) -> None:
        if not self.source:
            messagebox.showinfo("twixt", "Choose a video file first.")
            return
        self._collect_widgets_into_profile()
        try:
            self.session = player.launch(self.source.path, self.profile)
        except RuntimeError as exc:
            messagebox.showerror("twixt", str(exc))
            return
        self.status.set("playing")
        threading.Thread(target=self._watch_player, daemon=True).start()

    def _watch_player(self) -> None:
        session = self.session
        if session:
            session.wait()
            session.script.unlink(missing_ok=True)
        self._events.put(("status", "stopped"))

    def run_benchmark(self) -> None:
        if not self.source:
            messagebox.showinfo("twixt", "Choose a video file first.")
            return
        self._collect_widgets_into_profile()
        self.status.set("benchmarking...")
        profile_copy = dataclasses.replace(self.profile)
        source = self.source.path

        def work():
            try:
                self._events.put(("bench", player.benchmark(source, profile_copy)))
            except Exception as exc:
                self._events.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def show_doctor(self) -> None:
        lines = [f"{'OK ' if ok else '-- '} {name:<18} {detail}"
                 for name, ok, detail in detect.diagnose()]
        messagebox.showinfo("twixt install check", "\n".join(lines))

    # --- summary ------------------------------------------------------------

    def _refresh_summary(self) -> None:
        info = self.source
        lines: list[str] = []

        if not info:
            lines.append("Open a video file to see the exact conversion.")
            lines.append(f"Display refresh: {self.display_fps:.0f} Hz")
        else:
            target = self.profile.resolve_target_fps(info.fps, self.display_fps)
            multi = self.profile.multiplier(info.fps, self.display_fps)
            lines.append(f"{info.width}x{info.height}  {info.codec}  "
                         f"{info.fps:.3f} fps  ({info.duration / 60:.1f} min)")
            if info.fps > self.profile.bypass_above_fps:
                lines.append("")
                lines.append(f"Source is already above "
                             f"{self.profile.bypass_above_fps:g} fps - "
                             "interpolation will be skipped.")
            else:
                generated = max(0.0, target - info.fps)
                lines.append("")
                lines.append(f"{info.fps:.3f} fps  ->  {target:.2f} fps"
                             f"   (x{multi}, {multi.numerator - multi.denominator}"
                             f" new frames per {multi.denominator} source frames)")
                lines.append(f"{generated:.0f} of every {target:.0f} frames "
                             "per second will be generated.")
            lines.append("")
            lines.append(f"Backend: {self.profile.backend}   "
                         f"Grid: {self.profile.grid}   "
                         f"Masking: {self.profile.artifact_masking}")

        available = detect.available_backends()
        if not available.get(self.profile.backend):
            lines.append("")
            lines.append(f"! The '{self.profile.backend}' backend is not installed. "
                         "Run bootstrap.ps1.")

        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", "\n".join(lines))
        self.summary.configure(state="disabled")

    # --- background event pump ---------------------------------------------

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "status":
                    self.status.set(payload)
                elif kind == "error":
                    self.status.set("failed")
                    messagebox.showerror("twixt", payload)
                elif kind == "bench":
                    verdict = ("real time - good to go" if payload["realtime"]
                               else "TOO SLOW - will stutter")
                    self.status.set(f"{payload['achieved_fps']:.1f} fps achieved")
                    messagebox.showinfo(
                        "Benchmark",
                        f"{payload['resolution']}\n"
                        f"target    {payload['target_fps']:.2f} fps\n"
                        f"achieved  {payload['achieved_fps']:.2f} fps\n"
                        f"headroom  {payload['headroom']:.2f}x\n\n{verdict}")
        except queue.Empty:
            pass
        self.after(150, self._drain_events)


def main() -> int:
    ManagerWindow().mainloop()
    return 0
