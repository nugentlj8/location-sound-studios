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
import lss_scene
try:
    import lss_update
except Exception:
    lss_update = None

DEFAULT_IN = lss_render.DEFAULT_IN

PRESETS = lss_presets.load()

BG, FG, ACC, FIELD = "#13232E", "#F0E7D6", "#CF7A34", "#1D3140"
EDGE = "#3A4E5C"                 # swatch border, so a dark colour still shows
MUTED, WARN = "#7d93a3", "#ED574C"

# Every colour the presets already use, offered in each custom-colour dropdown.
# Derived, never hand-copied - add a preset and it appears here.
PALETTE = lss_presets.palette(PRESETS)
PALETTE_HEX = dict(PALETTE)                      # label -> colour
PALETTE_NAME = {h: l for l, h in PALETTE}        # colour -> label
CUSTOM = "Custom…"

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
        root.minsize(760, 720)
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
        st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=0)
        st.configure("TNotebook.Tab", background=FIELD, foreground=FG,
                     borderwidth=0, padding=(16, 8))
        st.map("TNotebook.Tab", background=[("selected", BG)],
               foreground=[("selected", ACC)])

        f = ttk.Frame(root, padding=18)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(f, text="LOCATION SOUND STUDIOS",
                  font=("TkDefaultFont", 13, "bold")).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(0, 14))
        r += 1

        # The audio in and the folder out stay above the tabs: they are the two
        # things every render needs, and neither belongs to one group.
        self.audio = self._file_row(f, r, "Audio file", "Choose…", self.pick_audio)
        r += 1
        self.outdir = self._file_row(f, r, "Output folder", "Choose…", self.pick_out)
        self.outdir.insert(0, lss_render.usable(lss_render.default_outdir(PRESETS),
                                                "LSS Renders"))
        r += 1

        # Everything else is grouped by what it decides: what the frame SAYS,
        # what it LOOKS like, what COLOUR it is, what SHAPE the audio takes, and
        # how the video encodes. The form was one 27-row column taller than a
        # laptop screen; the notebook sizes to its tallest tab, so colour has
        # its own rather than making Look the tab that sets the height.
        nb = ttk.Notebook(f)
        nb.grid(row=r, column=0, columnspan=3, sticky="nsew", pady=(16, 4))
        r += 1
        slate, look, colour, shape, video = (ttk.Frame(nb, padding=14)
                                             for _ in range(5))
        for tab, title in ((slate, "Slate"), (look, "Look"), (colour, "Colour"),
                           (shape, "Shape"), (video, "Video")):
            tab.columnconfigure(1, weight=1)
            nb.add(tab, text=title)

        # ---------- slate: what the frame says ----------
        sr = 0
        self.place = self._entry(slate, sr, "Place", "Roosevelt Row"); sr += 1
        self.city = self._entry(slate, sr, "City, State", "Phoenix, AZ"); sr += 1
        self.cond = self._entry(slate, sr, "Conditions", "Clear"); sr += 1
        today = datetime.date.today().isoformat()
        self.date = self._entry(slate, sr, "Recording date", today); sr += 1
        self.start = self._entry(slate, sr, "Start time", "06:30 PM"); sr += 1
        ttk.Label(slate, text="Time the published file begins — after any trimming.",
                  foreground=MUTED).grid(row=sr, column=1, sticky="w", pady=(0, 8))
        sr += 1
        ttk.Label(slate, text="Number").grid(row=sr, column=0, sticky="w",
                                             padx=(0, 12), pady=4)
        nf = ttk.Frame(slate)
        nf.grid(row=sr, column=1, columnspan=2, sticky="ew", pady=4)
        self.numstyle = ttk.Combobox(nf, values=list(lss_render.NUM_STYLES),
                                     state="readonly", width=18)
        self.numstyle.set("No.")
        self.numstyle.pack(side="left")
        self.number = ttk.Entry(nf, width=8)
        self.number.pack(side="left", padx=(8, 0))
        ttk.Label(nf, text="  blank to hide it",
                  foreground=MUTED).pack(side="left")
        sr += 1
        self.outname = self._entry(slate, sr, "Output file name", ""); sr += 1
        ttk.Label(slate, text="Names the folder and files, not the frame. "
                              "Blank uses the place.",
                  foreground=MUTED).grid(row=sr, column=1, sticky="w", pady=(0, 6))
        sr += 1

        # ---------- look: series and silhouette ----------
        sn = lss_presets.series_names(PRESETS)
        tn = lss_presets.theme_names(PRESETS)
        cn = lss_presets.color_preset_names(PRESETS)
        lr = 0
        self.series = self._combo(look, lr, "Series", sn, sn[0]); lr += 1
        self.series.bind("<<ComboboxSelected>>", self._series_changed)
        # the silhouette a series can wear depends on the scene it is set in,
        # so this list is rebuilt whenever the series changes
        self.style = self._combo(look, lr, "Silhouette", ["blocks"], "blocks"); lr += 1
        self.style.bind("<<ComboboxSelected>>", self._style_changed)
        self.detail = self._combo(look, lr, "Silhouette detail",
                                  list(lss_scene.DETAIL), "Default"); lr += 1
        ttk.Label(look, text="How much shape the mountains, trees and houses "
                             "carry. Tower width is for blocks.",
                  foreground=MUTED).grid(row=lr, column=1, sticky="w", pady=(0, 6))
        lr += 1
        self.ahead = self._combo(look, lr, "Trees before the playhead",
                                 lss_scene.TREE_AHEAD, lss_scene.TREE_AHEAD[0])
        lr += 1
        self.face = self._combo(look, lr, "Mountain faces",
                                lss_scene.MOUNTAIN_FACE, lss_scene.MOUNTAIN_FACE[0])
        lr += 1
        ttk.Label(look, text="Both grey out for a silhouette that has no "
                             "trees or no mountains.",
                  foreground=MUTED).grid(row=lr, column=1, sticky="w", pady=(0, 8))
        lr += 1
        self.filled = tk.BooleanVar(value=False)
        ttk.Checkbutton(look, text="Solid silhouette instead of outlines",
                        variable=self.filled).grid(row=lr, column=1, sticky="w", pady=4)
        lr += 1
        self._series_changed()          # now that ahead/face exist to be greyed

        # ---------- colour ----------
        cr = 0
        self.theme = self._combo(colour, cr, "Occasion", tn, tn[0]); cr += 1
        self.colors = self._combo(colour, cr, "Colours", cn, cn[0]); cr += 1
        ttk.Label(colour, text="Sets the sky. The occasion keeps its own accent.",
                  foreground=MUTED).grid(row=cr, column=1, sticky="w", pady=(0, 6))
        cr += 1
        self.cust_bg = self._color_row(colour, cr, "Custom sky"); cr += 1
        self.cust_fg = self._color_row(colour, cr, "Custom silhouette"); cr += 1
        self.cust_a = self._color_row(colour, cr, "Custom accent"); cr += 1
        ttk.Label(colour, text="Pick a colour already in use, or type #RRGGBB. "
                               "Blank follows the colours above.",
                  foreground=MUTED).grid(row=cr, column=1, sticky="w", pady=(0, 8))
        cr += 1
        self.mono = tk.BooleanVar(value=False)
        ttk.Checkbutton(colour, text="Monochrome slate — small text in the "
                                     "silhouette colour, not the accent",
                        variable=self.mono).grid(row=cr, column=1, sticky="w",
                                                 pady=(0, 8))
        cr += 1

        # One thumbnail per colour, off a single pass over the audio. Only for
        # thumbnails - several full encodes of one recording is not something
        # anyone wants by accident - so it follows the Thumbnail only tick.
        ttk.Label(colour, text="Compare").grid(row=cr, column=0, sticky="nw",
                                               padx=(0, 12), pady=4)
        vb = ttk.Frame(colour)
        vb.grid(row=cr, column=1, columnspan=2, sticky="ew", pady=4)
        self.variants = tk.Listbox(vb, selectmode="multiple", height=5,
                                   bg=FIELD, fg=FG, relief="flat",
                                   highlightthickness=0, activestyle="none",
                                   selectbackground=ACC, selectforeground=BG,
                                   disabledforeground="#5A6E7C",
                                   exportselection=False)   # or Tk drops the
        for name in cn:                                     # selection on focus
            self.variants.insert("end", name)
        self.variants.pack(side="left", fill="x", expand=True)
        sb = ttk.Scrollbar(vb, orient="vertical", command=self.variants.yview)
        sb.pack(side="left", fill="y")
        self.variants.config(yscrollcommand=sb.set)
        cr += 1
        self.vhint = ttk.Label(
            colour, text="Tick Thumbnail only to render several colours at once.",
            foreground=MUTED)
        self.vhint.grid(row=cr, column=1, sticky="w", pady=(0, 8))
        cr += 1

        # ---------- shape: how loudness becomes height ----------
        hr = 0
        self.scale = self._combo(shape, hr, "Height scaling",
                                 lss_render.SCALES, lss_render.SCALES[0]); hr += 1
        self.dyn = self._combo(shape, hr, "Dynamics",
                               list(lss_scene.DYNAMICS), "More"); hr += 1
        ttk.Label(shape, text="Dynamics has no effect on Fixed loudness.",
                  foreground=MUTED).grid(row=hr, column=1, sticky="w", pady=(0, 8))
        hr += 1
        self.towers = self._combo(shape, hr, "Tower width",
                                  list(lss_render.TOWERS) + ["Auto"],
                                  "Default"); hr += 1
        self.rows = self._combo(shape, hr, "Stacked rows",
                                ["1", "2", "3", "4", "5"], "1"); hr += 1
        ttk.Label(shape, text="Both are for blocks and topo. A generative "
                              "silhouette is one scene, not stacked rows.",
                  foreground=MUTED).grid(row=hr, column=1, sticky="w", pady=(0, 8))
        hr += 1
        self.peak = tk.BooleanVar(value=True)
        ttk.Checkbutton(shape, text="Height = loudest moment (not average)",
                        variable=self.peak).grid(row=hr, column=1, sticky="w", pady=4)
        hr += 1
        self.align = tk.BooleanVar(value=True)
        ttk.Checkbutton(shape, text="Line up tall features with loud moments",
                        variable=self.align).grid(row=hr, column=1, sticky="w", pady=4)
        hr += 1

        # ---------- video: the encode only ----------
        vr = 0
        self.size = self._combo(video, vr, "Resolution",
                                list(SIZES), list(SIZES)[0]); vr += 1
        self.chroma = tk.BooleanVar(value=False)
        ttk.Checkbutton(video, text="Full colour detail (4:4:4) — verify it "
                                    "uploads OK",
                        variable=self.chroma).grid(row=vr, column=1, sticky="w",
                                                   pady=(8, 4))
        vr += 1
        ttk.Label(video, text="Thumbnail only skips everything on this tab.",
                  foreground=MUTED).grid(row=vr, column=1, sticky="w", pady=(6, 0))
        vr += 1

        # ---------- always visible: render, and what to render ----------
        act = ttk.Frame(f)
        act.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(12, 6))
        r += 1
        self.go = ttk.Button(act, text="Render", style="Go.TButton",
                             command=self.start_render)
        self.go.pack(side="left")
        self.thumbonly = tk.BooleanVar(value=False)
        ttk.Checkbutton(act, text="Thumbnail only — skip the video encode",
                        variable=self.thumbonly).pack(side="left", padx=(16, 0))
        self.thumbonly.trace_add("write", lambda *_a: self._thumbonly_changed())
        ttk.Label(act, text="Preview at").pack(side="left", padx=(20, 6))
        self.preview = ttk.Entry(act, width=5)
        self.preview.pack(side="left")
        ttk.Label(act, text="%  blank = unplayed",
                  foreground=MUTED).pack(side="left", padx=(6, 0))

        self.bar = ttk.Progressbar(f, mode="determinate", maximum=1000)
        self.bar.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(4, 2))
        r += 1
        self.status = ttk.Label(f, text="", foreground=MUTED)
        self.status.grid(row=r, column=0, columnspan=3, sticky="w", pady=(0, 6))
        r += 1

        self.log = tk.Text(f, height=7, bg=FIELD, fg=FG, relief="flat",
                           insertbackground=FG, wrap="word")
        self.log.grid(row=r, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        f.rowconfigure(r, weight=1)
        self._thumbonly_changed()
        self.say("Choose an audio file and fill in the slate, then press Render.")
        self.root.after(120, self.drain)
        self.root.after(400, self._check_updates)

    def _check_updates(self):
        if lss_update is None or getattr(self, "_update_checked", False):
            return
        self._update_checked = True
        import threading
        def worker():
            try:
                newer = lss_update.check()
            except Exception:
                newer = None
            if newer:
                self.q.put(("update", newer))
        threading.Thread(target=worker, daemon=True).start()

    def _series_changed(self, _evt=None):
        """Offer only the silhouettes this series' scene actually has."""
        scene = lss_presets.series_scene(self.series.get(), PRESETS)
        styles = lss_scene.SCENE_STYLES[scene]
        cur = self.style.get()
        self.style["values"] = styles
        self.style.set(cur if cur in styles else lss_scene.DEFAULT_STYLE[scene])
        self._style_changed()

    def _thumbonly_changed(self):
        """The Compare list only means anything without an encode, so it
        follows the tick rather than sitting there inviting six video renders."""
        on = bool(self.thumbonly.get())
        self.variants.config(state="normal" if on else "disabled")
        self.vhint.config(
            text="Renders Colours above plus each ticked colour — one "
                 "thumbnail each, in one folder."
            if on else "Tick Thumbnail only to render several colours at once.")

    def _picked_variants(self):
        """Selected colour presets, but only when they can actually be used -
        a selection made before the tick was cleared must not leak into a
        video render."""
        if not self.thumbonly.get():
            return []
        return [self.variants.get(i) for i in self.variants.curselection()]

    def _style_changed(self, _evt=None):
        """Grey out a treatment the chosen silhouette never reaches - blocks
        has no trees to draw faint, and a forest has no mountain faces."""
        s = self.style.get()
        for w, styles in ((self.ahead, lss_scene.TREE_AHEAD_STYLES),
                          (self.face, lss_scene.MOUNTAIN_FACE_STYLES)):
            w.config(state="readonly" if s in styles else "disabled")

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

    def _color_row(self, f, r, label):
        """Swatch, hex field and a dropdown of the colours already in use.

        The FIELD is the single source of truth - the dropdown only ever writes
        into it, and the swatch and the dropdown both follow whatever it holds.
        So a hex typed by hand wins, and the two can never disagree with it.

        (No swatch beside each dropdown entry: a Tk listbox draws text only, and
        the popup a ttk.Combobox uses is a plain listbox. The hex is in every
        label instead, and the swatch here shows the choice.)
        """
        ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", padx=(0, 12), pady=4)
        box = ttk.Frame(f)
        box.grid(row=r, column=1, columnspan=2, sticky="ew", pady=4)
        sw = tk.Frame(box, width=20, height=20, bg=FIELD,
                      highlightthickness=1, highlightbackground=EDGE)
        sw.pack(side="left")
        sw.pack_propagate(False)
        var = tk.StringVar()
        e = ttk.Entry(box, textvariable=var, width=11)
        e.pack(side="left", padx=(8, 0))
        c = ttk.Combobox(box, values=[l for l, _ in PALETTE] + [CUSTOM],
                         state="readonly", width=30)
        c.set(CUSTOM)
        c.pack(side="left", padx=(8, 0))
        c.bind("<<ComboboxSelected>>",
               lambda _evt, c=c, var=var, e=e: self._color_picked(c, var, e))
        var.trace_add("write",
                      lambda *_a, var=var, c=c, sw=sw: self._color_sync(var, c, sw))
        return e

    def _color_picked(self, combo, var, entry):
        h = PALETTE_HEX.get(combo.get())
        if h:
            var.set(h)
        else:
            entry.focus_set()      # "Custom…" - the field is theirs to type in

    def _color_sync(self, var, combo, swatch):
        h = var.get().strip().upper()
        combo.set(PALETTE_NAME.get(h, CUSTOM))
        swatch.config(bg=h if lss_presets.valid_hex(h) else FIELD)

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
                self.go.config(state="normal", text="Render")
                thumbs = payload.get("thumbnails") or [payload["thumbnail"]]
                if payload.get("video"):
                    what = "video and thumbnail"
                elif len(thumbs) > 1:
                    what = "%d thumbnails" % len(thumbs)
                else:
                    what = "thumbnail"
                # said here rather than in a dialog: nothing to dismiss before
                # the next render, and the paths are in the log either way
                self.status.config(text="Render complete — %s in %s"
                                        % (what, payload.get("folder", "")),
                                   foreground=ACC)
                self.say("\nFolder    : %s" % payload.get("folder", ""))
                for p in thumbs:
                    self.say("Thumbnail : " + os.path.basename(p))
                self.say("Video     : " + payload["video"] if payload.get("video")
                         else "(thumbnail only — no video rendered)")
            elif kind == "error":
                self.busy = False
                self.bar["value"] = 0
                self.status.config(text="Failed.", foreground=WARN)
                self.go.config(state="normal", text="Render")
                self.say("\nFAILED: " + payload)
                messagebox.showerror("Render failed", payload)
        self.root.after(120, self.drain)


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
        return self._preview_frac()[1]

    def _preview_frac(self):
        """(fraction, error). --progress as a percentage, since that is how a
        person says it. Blank means the unplayed frame, i.e. 0."""
        s = self.preview.get().strip().rstrip("%").strip()
        if not s:
            return 0.0, None
        try:
            v = float(s)
        except ValueError:
            return 0.0, f"Preview must be a percentage like 50, not '{s}'."
        if not 0 <= v <= 100:
            return 0.0, "Preview must be between 0 and 100%."
        return v / 100.0, None

    def _offer_update(self, version):
        from tkinter import messagebox
        if getattr(self, "_update_offered", False):
            return
        self._update_offered = True
        if messagebox.askyesno(
                "Update available",
                f"Version {version} is available (you have "
                f"{lss_update.local_version()}).\n\nUpdate now? The app will "
                "restart itself when it's done."):
            ok = lss_update.update(log=lambda m: self.q.put(("log", m)))
            if ok:
                messagebox.showinfo("Updated",
                    "Update installed. The app will now restart.")
                self._relaunch()

    def _relaunch(self):
        """Restart the app so the new code is loaded, without a console window."""
        import subprocess
        exe = sys.executable
        # if launched via python.exe, prefer pythonw.exe so no console appears
        if exe.lower().endswith("python.exe"):
            pw = exe[:-len("python.exe")] + "pythonw.exe"
            if os.path.exists(pw):
                exe = pw
        try:
            flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW
            subprocess.Popen([exe, os.path.abspath(__file__)],
                             creationflags=flags)
        except Exception:
            try:
                subprocess.Popen([exe, os.path.abspath(__file__)])
            except Exception:
                pass
        self.root.destroy()
        os._exit(0)

    def start_render(self):
        if self.busy:
            return
        err = self.validate()
        if err:
            messagebox.showwarning("Check the form", err)
            return
        custom = {"accent": self.cust_a.get(),
                  "background": self.cust_bg.get(), "foreground": self.cust_fg.get()}
        for val in custom.values():
            if val.strip() and not lss_presets.valid_hex(val):
                messagebox.showwarning("Check the form",
                                       f"'{val}' is not a colour like #CF7A34.")
                return
        # base_acc is the accent before any colour preset has had a say, so
        # every Compare variant resolves from the same starting point
        series_name, base_acc = lss_presets.resolve(
            self.series.get(), self.theme.get(), PRESETS, custom)
        bg, fg, acc = lss_presets.resolve_colors(
            self.colors.get(), self.theme.get(), PRESETS, base_acc, custom)
        picks = self._picked_variants()
        if picks and custom["background"].strip():
            messagebox.showwarning(
                "Check the form",
                f"A custom sky overrides every colour, so all {len(picks)} "
                "would render the same.\n\nClear the custom sky, or clear the "
                "Compare list.")
            return
        geometry = lss_presets.series_geometry(self.series.get(), PRESETS)
        _suf, cyc, cmin = lss_presets.theme_extras(self.theme.get(), PRESETS)
        # Only second-guess colours typed in by hand. The built-in presets were
        # already chosen against measured contrast on rendered frames, and some
        # lean on hue rather than luminance, so warning about them every render
        # would be noise.
        hand = custom["accent"].strip()
        if hand and lss_presets.contrast_on(hand, bg) < 4.5:
            if not messagebox.askyesno(
                    "Low contrast",
                    f"{hand} is dim against the background and may look muddy "
                    "at thumbnail size.\n\nRender anyway?"):
                return
        scene = lss_presets.series_scene(self.series.get(), PRESETS)
        frac = self._preview_frac()[0]
        err = lss_scene.check(scene, self.style.get(), int(self.rows.get()),
                              bool(self.filled.get()), frac)
        if err:
            messagebox.showwarning("Check the form", err[0].upper() + err[1:])
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
            "color_preset": self.colors.get(),
            # empty unless something is ticked, so a plain single render keeps
            # its plain <slug>_thumb.png name
            "variants": (lss_presets.variant_set(
                self.colors.get(), picks, self.theme.get(), PRESETS,
                base_acc, custom) if picks else []),
            "accent": acc,
            "background": bg, "foreground": fg,
            "width": w, "height": h, "fps": 10, "geometry": geometry,
            "scene": scene, "style": self.style.get(),
            "detail": self.detail.get(),
            "tree_ahead": self.ahead.get(), "mountain_face": self.face.get(),
            "progress": frac,
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
        self.status.config(text="Starting…", foreground=MUTED)
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


def _find_icon(name):
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, name),
                 os.path.join(os.path.dirname(here), name)):
        if os.path.exists(cand):
            return cand
    return None


def _set_taskbar_identity(root):
    """Make Windows show our icon in the taskbar, not the Python logo."""
    # 1. distinct AppUserModelID so we are not grouped under python.exe
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "LocationSoundStudios.App")
    except Exception:
        pass

    # 2. title-bar icon via the .ico (tkinter's own mechanism)
    ico = _find_icon("LSS.ico")
    if ico:
        try:
            root.iconbitmap(default=ico)
        except Exception:
            pass

    # 3. force the TASKBAR button icon through Win32. tkinter's iconbitmap
    #    often does not reach the taskbar, so load the .ico straight onto the
    #    window handle with WM_SETICON. This is what actually replaces the
    #    Python logo on the taskbar.
    if ico and os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            root.update_idletasks()          # ensure the HWND exists
            hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
            if not hwnd:
                hwnd = root.winfo_id()
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            LR_DEFAULTSIZE = 0x00000040
            WM_SETICON = 0x0080
            ICON_SMALL, ICON_BIG = 0, 1
            user32 = ctypes.windll.user32
            for size, which in ((16, ICON_SMALL), (32, ICON_BIG)):
                hicon = user32.LoadImageW(None, ico, IMAGE_ICON, size, size,
                                          LR_LOADFROMFILE)
                if hicon:
                    user32.SendMessageW(hwnd, WM_SETICON, which, hicon)
        except Exception:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    _set_taskbar_identity(root)
    png = _find_icon("icon_master.png")
    if png:
        try:
            root._icon_img = tk.PhotoImage(file=png)
            root.iconphoto(True, root._icon_img)
        except Exception:
            pass
    App(root)
    root.mainloop()
