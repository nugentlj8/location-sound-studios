#!/usr/bin/env python3
"""
Location Sound Studios - Studio.

A small window that collects the details and renders the video.
Run with:  python lss_studio.py
"""

import datetime, os, queue, sys, threading, traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import lss_render
import lss_presets
try:
    import lss_update
except Exception:
    lss_update = None

DEFAULT_IN = lss_render.DEFAULT_IN
DEFAULT_OUT = lss_render.DEFAULT_OUT

PRESETS = lss_presets.load()

BG, FG, ACC, FIELD = "#13232E", "#F0E7D6", "#CF7A34", "#1D3140"

SCALES = ["Skyline (rank)", "Auto (percentile)", "Fixed loudness"]
SIZES = {"1440p (recommended)": (2560, 1440),
         "2160p / 4K": (3840, 2160),
         "1080p": (1920, 1080)}


def _eta(seconds):
    if not seconds or seconds <= 0:
        return ""
    if seconds < 60:
        return "  ·  less than a minute left"
    m = int(round(seconds / 60.0))
    if m < 90:
        return "  ·  about %d minute%s left" % (m, "" if m == 1 else "s")
    h, rm = divmod(m, 60)
    if rm < 8:
        return "  ·  about %d hour%s left" % (h, "" if h == 1 else "s")
    return "  ·  about %dh %dm left" % (h, rm)


class App:
    def __init__(self, root):
        self.root = root
        root.title("Location Sound Studios")
        root.configure(bg=BG)
        root.minsize(680, 880)
        self.q = queue.Queue()
        self.busy = False

        # the dropdown popup is a plain Tk listbox and ignores ttk styling
        root.option_add("*TCombobox*Listbox.background", FIELD)
        root.option_add("*TCombobox*Listbox.foreground", FG)
        root.option_add("*TCombobox*Listbox.selectBackground", ACC)
        root.option_add("*TCombobox*Listbox.selectForeground", BG)
        root.option_add("*TCombobox*Listbox.font", "TkDefaultFont 10")

        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("TFrame", background=BG)
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.configure("TEntry", fieldbackground=FIELD, foreground=FG,
                     insertcolor=FG, borderwidth=0)
        st.configure("TCombobox", fieldbackground=FIELD, background=FIELD,
                     foreground=FG, arrowcolor=FG, borderwidth=0, padding=5)
        st.map("TCombobox",
               fieldbackground=[("readonly", FIELD), ("disabled", FIELD)],
               background=[("readonly", FIELD), ("active", FIELD)],
               foreground=[("readonly", FG), ("disabled", "#6B7F8D")],
               selectbackground=[("readonly", FIELD)],
               selectforeground=[("readonly", FG)],
               arrowcolor=[("readonly", FG)])
        st.map("TEntry",
               fieldbackground=[("!disabled", FIELD)],
               foreground=[("!disabled", FG)])
        st.configure("Go.TButton", background=ACC, foreground="#13232E",
                     borderwidth=0, padding=9)
        st.configure("Horizontal.TProgressbar", background=ACC,
                     troughcolor=FIELD, borderwidth=0, thickness=10)

        f = ttk.Frame(root, padding=18)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(f, text="LOCATION SOUND STUDIOS",
                  font=("TkDefaultFont", 13, "bold")).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 14))
        r += 1

        self.audio = self._file_row(f, r, "Audio file", "Choose…", self.pick_audio)
        r += 1
        self.outname = self._entry(f, r, "Output file name", ""); r += 1
        ttk.Label(f, text="Leave blank to name it after the place.",
                  foreground="#7d93a3").grid(row=r, column=1, sticky="w", pady=(0, 6))
        r += 1
        self.outdir = self._file_row(f, r, "Output folder", "Choose…", self.pick_out)
        self.outdir.insert(0, lss_render.usable(DEFAULT_OUT, "LSS Renders"))
        r += 1

        ttk.Separator(f).grid(row=r, column=0, columnspan=3, sticky="ew", pady=12)
        r += 1

        self.place = self._entry(f, r, "Place", "Roosevelt Row"); r += 1
        self.city = self._entry(f, r, "City, State", "Phoenix, AZ"); r += 1
        self.cond = self._entry(f, r, "Conditions", "Clear"); r += 1

        today = datetime.date.today().isoformat()
        self.date = self._entry(f, r, "Recording date", today); r += 1
        self.start = self._entry(f, r, "Start time", "06:30 PM"); r += 1
        ttk.Label(f, text="Time the published file begins — after any trimming.",
                  foreground="#7d93a3").grid(row=r, column=1, sticky="w", pady=(0, 8))
        r += 1

        sn = lss_presets.series_names(PRESETS)
        tn = lss_presets.theme_names(PRESETS)
        self.series = self._combo(f, r, "Series", sn, sn[0]); r += 1
        self.theme = self._combo(f, r, "Occasion", tn, tn[0]); r += 1
        ttk.Label(f, text="Number").grid(row=r, column=0, sticky="w",
                                         padx=(0, 12), pady=4)
        nf = ttk.Frame(f)
        nf.grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        self.numstyle = ttk.Combobox(nf, values=list(lss_render.NUM_STYLES),
                                     state="readonly", width=18)
        self.numstyle.set("No.")
        self.numstyle.pack(side="left")
        self.number = ttk.Entry(nf, width=8)
        self.number.pack(side="left", padx=(8, 0))
        ttk.Label(nf, text="  blank to hide it",
                  foreground="#7d93a3").pack(side="left")
        r += 1

        ttk.Label(f, text="Custom colours").grid(row=r, column=0, sticky="w",
                                                 padx=(0, 12), pady=4)
        cf = ttk.Frame(f)
        cf.grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        self.cust_a = ttk.Entry(cf, width=11); self.cust_a.pack(side="left")
        self.cust_b = ttk.Entry(cf, width=11); self.cust_b.pack(side="left", padx=(8, 0))
        ttk.Label(cf, text="  #RRGGBB — leave blank to use the preset",
                  foreground="#7d93a3").pack(side="left")
        r += 1
        self.size = self._combo(f, r, "Resolution", list(SIZES), list(SIZES)[0]); r += 1
        self.scale = self._combo(f, r, "Height scaling", SCALES, SCALES[0]); r += 1
        self.rows = self._combo(f, r, "Skylines",
                                ["1", "2", "3", "4", "5"], "1"); r += 1
        self.towers = self._combo(f, r, "Tower width",
                                  ["Thick", "Default", "Thin", "Fine", "Auto"],
                                  "Default"); r += 1
        self.dyn = self._combo(f, r, "Dynamics",
                               ["Natural", "More", "Most"], "More"); r += 1
        ttk.Label(f, text="Dynamics has no effect on Fixed loudness.",
                  foreground="#7d93a3").grid(row=r, column=1, sticky="w", pady=(0, 6))
        r += 1

        self.thumbonly = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Thumbnail only — skip the video encode",
                        variable=self.thumbonly).grid(row=r, column=1, sticky="w", pady=6)
        r += 1

        self.mono = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Monochrome slate (small text in bone, not accent)",
                        variable=self.mono).grid(row=r, column=1, sticky="w", pady=6)
        r += 1

        self.chroma = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Full colour detail (4:4:4) — verify it uploads OK",
                        variable=self.chroma).grid(row=r, column=1, sticky="w", pady=6)
        r += 1

        self.filled = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Solid silhouette instead of outlines",
                        variable=self.filled).grid(row=r, column=1, sticky="w", pady=6)
        r += 1

        self.peak = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Tower height = loudest moment (not average)",
                        variable=self.peak).grid(row=r, column=1, sticky="w", pady=6)
        r += 1

        self.align = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Line up tall towers with loud moments",
                        variable=self.align).grid(row=r, column=1, sticky="w", pady=6)
        r += 1

        self.go = ttk.Button(f, text="Render", style="Go.TButton", command=self.start_render)
        self.go.grid(row=r, column=1, sticky="w", pady=(14, 8))
        r += 1

        self.bar = ttk.Progressbar(f, mode="determinate", maximum=1000)
        self.bar.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(4, 2))
        r += 1
        self.status = ttk.Label(f, text="", foreground="#7d93a3")
        self.status.grid(row=r, column=0, columnspan=3, sticky="w", pady=(0, 6))
        r += 1

        self.log = tk.Text(f, height=9, bg=FIELD, fg=FG, relief="flat",
                           insertbackground=FG, wrap="word")
        self.log.grid(row=r, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        f.rowconfigure(r, weight=1)
        self.say("Choose an audio file and fill in the slate, then press Render.")
        self.root.after(120, self.drain)
        self.root.after(400, self._check_updates)

    def _check_updates(self):
        if lss_update is None:
            return
        import threading
        def worker():
            try:
                newer = lss_update.check()
            except Exception:
                newer = None
            if newer:
                self.q.put(("update", newer))
        threading.Thread(target=worker, daemon=True).start()

    # ---------- widget helpers ----------
    def _entry(self, f, r, label, default=""):
        ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=4)
        e = ttk.Entry(f)
        e.insert(0, default)
        e.grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        return e

    def _combo(self, f, r, label, values, default):
        ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=4)
        c = ttk.Combobox(f, values=values, state="readonly")
        c.set(default)
        c.grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        return c

    def _file_row(self, f, r, label, btn, cmd):
        ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=4)
        e = ttk.Entry(f)
        e.grid(row=r, column=1, sticky="ew", pady=4)
        ttk.Button(f, text=btn, command=cmd).grid(row=r, column=2, padx=(8, 0))
        return e

    def pick_audio(self):
        start = lss_render.usable(DEFAULT_IN, "")
        p = filedialog.askopenfilename(
            title="Choose the rendered audio",
            initialdir=start if os.path.isdir(start) else None,
            filetypes=[("Audio", "*.flac *.wav *.aiff *.aif *.mp3"), ("All files", "*.*")])
        if p:
            self.audio.delete(0, "end"); self.audio.insert(0, p)
            if not self.place.get().strip():
                stem = os.path.splitext(os.path.basename(p))[0].replace("_", " ")
                self.place.insert(0, stem.title())

    def pick_out(self):
        cur = self.outdir.get().strip()
        p = filedialog.askdirectory(title="Where should the files go?",
                                    initialdir=cur if os.path.isdir(cur) else None)
        if p:
            self.outdir.delete(0, "end"); self.outdir.insert(0, p)

    # ---------- logging ----------
    def say(self, s):
        self.log.insert("end", s + "\n")
        self.log.see("end")

    def drain(self):
        while True:
            try:
                kind, payload = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.say(payload)
            elif kind == "update":
                self._offer_update(payload)
            elif kind == "prog":
                frac, eta = payload
                self.bar["value"] = int(frac * 1000)
                self.status.config(text="%d%%%s" % (int(frac * 100), _eta(eta)))
            elif kind == "done":
                self.busy = False
                self.bar["value"] = 1000
                self.status.config(text="Finished.")
                self.go.config(state="normal", text="Render")
                self.say("\nFolder    : %s\nThumbnail : %s%s" %
                         (payload.get("folder", ""), payload["thumbnail"],
                          "\nVideo     : " + payload["video"] if payload.get("video")
                          else "\n(thumbnail only — no video rendered)"))
                messagebox.showinfo("Finished", "Render complete.")
            elif kind == "error":
                self.busy = False
                self.bar["value"] = 0
                self.status.config(text="Failed.")
                self.go.config(state="normal", text="Render")
                self.say("\nFAILED: " + payload)
                messagebox.showerror("Render failed", payload)
        self.root.after(120, self.drain)
        self.root.after(400, self._check_updates)


    # ---------- render ----------
    def validate(self):
        if not os.path.isfile(self.audio.get().strip()):
            return "Choose an audio file that exists."
        for w, n in ((self.place, "Place"), (self.city, "City"), (self.cond, "Conditions")):
            if not w.get().strip():
                return f"{n} cannot be empty."
        try:
            datetime.datetime.strptime(self.date.get().strip(), "%Y-%m-%d")
        except ValueError:
            return "Recording date must look like 2026-07-23."
        try:
            datetime.datetime.strptime(self.start.get().strip().upper(), "%I:%M %p")
        except ValueError:
            return "Start time must look like 06:30 PM."
        return None

    def _offer_update(self, version):
        from tkinter import messagebox
        if messagebox.askyesno(
                "Update available",
                f"Version {version} is available (you have "
                f"{lss_update.local_version()}).\n\nUpdate now? The app will "
                "use the new version next time you open it."):
            ok = lss_update.update(log=lambda m: self.q.put(("log", m)))
            if ok:
                messagebox.showinfo("Updated",
                    "Update installed. Close and reopen the app to use it.")

    def start_render(self):
        if self.busy:
            return
        err = self.validate()
        if err:
            messagebox.showwarning("Check the form", err)
            return
        custom = {"accent": self.cust_a.get(), "accent2": self.cust_b.get()}
        for box, val in ((self.cust_a, custom["accent"]), (self.cust_b, custom["accent2"])):
            if val.strip() and not lss_presets.valid_hex(val):
                messagebox.showwarning("Check the form",
                                       f"'{val}' is not a colour like #CF7A34.")
                return
        series_name, acc, acc2 = lss_presets.resolve(
            self.series.get(), self.theme.get(), PRESETS, custom)
        geometry = lss_presets.series_geometry(self.series.get(), PRESETS)
        _suf, cyc, cmin = lss_presets.theme_extras(self.theme.get(), PRESETS)
        low = [c for c in (acc, acc2) if c and lss_presets.contrast_on_ink(c) < 4.5]
        if low:
            if not messagebox.askyesno(
                    "Low contrast",
                    "These colours are dim against the background and may look "
                    "muddy at thumbnail size:\n\n  " + "  ".join(low) +
                    "\n\nRender anyway?"):
                return
        w, h = SIZES[self.size.get()]
        cfg = {
            "audio": self.audio.get().strip(),
            "outdir": self.outdir.get().strip(),
            "place": self.place.get().strip().upper(),
            "city": self.city.get().strip().upper(),
            "conditions": self.cond.get().strip().upper(),
            "date": self.date.get().strip(),
            "start": self.start.get().strip().upper(),
            "series": series_name,
            "number": self.number.get().strip(),
            "number_style": self.numstyle.get(),
            "accent": acc, "accent2": acc2,
            "width": w, "height": h, "fps": 10, "geometry": geometry,
            "cycle": cyc, "cycle_minutes": cmin,
            "towers": self.towers.get(), "dynamics": self.dyn.get(),
            "scale": self.scale.get(),
            "rows": int(self.rows.get()),
            "filled": bool(self.filled.get()),
            "full_chroma": bool(self.chroma.get()),
            "align_loud": bool(self.align.get()),
            "height_stat": "peak" if self.peak.get() else "rms",
            "thumb_only": bool(self.thumbonly.get()),
            "slate_mono": bool(self.mono.get()),
            "outname": self.outname.get().strip(),
        }
        self.busy = True
        self.go.config(state="disabled", text="Rendering…")
        self.log.delete("1.0", "end")
        self.bar["value"] = 0
        self.status.config(text="Starting…")
        threading.Thread(target=self.work, args=(cfg,), daemon=True).start()

    def work(self, cfg):
        try:
            res = lss_render.run(
                cfg,
                progress=lambda s: self.q.put(("log", s)),
                on_progress=lambda fr, eta: self.q.put(("prog", (fr, eta))))
            self.q.put(("done", res))
        except Exception:
            self.q.put(("error", traceback.format_exc(limit=3)))


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
